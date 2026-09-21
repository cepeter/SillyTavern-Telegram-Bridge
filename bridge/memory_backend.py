"""Canonical Hindsight memory backend and session-scoped memory state."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import sqlite3
import threading
import time

from bridge.config import (
    HINDSIGHT_CONTEXT_MAX_CHARS,
    HINDSIGHT_DEFAULT_URL,
    HINDSIGHT_RECALL_MAX_TOKENS,
    HINDSIGHT_RETAIN_MAX_MESSAGES,
)
from bridge.database import db_connect, get_meta, run_write_txn
from bridge.network_security import validate_provider_endpoint


def hindsight_bank_id(chat_id: str) -> str:
    return "sillytavern-telegram-" + hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:24]


def hindsight_tags(chat_id: str, session_id: str, character_name: str) -> list[str]:
    user_key = hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:24]
    character_key = "".join(ch.lower() if ch.isalnum() else "-" for ch in character_name).strip("-")[:48] or "unknown"
    return [f"user:telegram-{user_key}", f"session:{session_id}", f"character:{character_key}"]


def hindsight_client():
    from hindsight_client import Hindsight
    base_url = os.environ.get("HINDSIGHT_API_URL", HINDSIGHT_DEFAULT_URL).rstrip("/")
    validate_provider_endpoint(base_url, "SILLYTAVERN_HINDSIGHT_ALLOWED_HOSTS")
    api_key = os.environ.get("HINDSIGHT_API_KEY") or None
    return Hindsight(base_url=base_url, api_key=api_key, timeout=30.0, user_agent="SillyTavernTelegramBridge/1.0")


_HINDSIGHT_SESSION_LOCKS: dict[tuple[str, str], threading.RLock] = {}


_HINDSIGHT_SESSION_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_HINDSIGHT_SESSION_LOCKS_GUARD = threading.Lock()


async def _close_hindsight_client_async(client) -> None:
    """Close any generated Hindsight API clients owned by a wrapper instance."""
    seen = set()
    candidates = [
        getattr(client, "_memory_api", None),
        getattr(client, "documents", None),
        getattr(client, "api_client", None),
    ]
    for candidate in candidates:
        api_client = getattr(candidate, "api_client", candidate)
        if api_client is None or id(api_client) in seen:
            continue
        seen.add(id(api_client))
        close = getattr(api_client, "close", None)
        if close is None:
            continue
        result = close()
        if inspect.isawaitable(result):
            await result


def close_hindsight_client(client) -> None:
    if client is None:
        return
    try:
        asyncio.run(_close_hindsight_client_async(client))
    except Exception:
        logging.debug("Could not close Hindsight client cleanly", exc_info=True)


def hindsight_session_lock(chat_id: str, session_id: str) -> threading.RLock:
    key = (str(chat_id), str(session_id))
    with _HINDSIGHT_SESSION_LOCKS_GUARD:
        return _HINDSIGHT_SESSION_LOCKS.setdefault(key, threading.RLock())


def hindsight_session_prefix(session_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(session_id)).strip("-.")[:80] or "session"
    digest = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:12]
    return f"st-session-{safe}-{digest}"


def hindsight_conversation_document_id(session_id: str) -> str:
    return hindsight_session_prefix(session_id) + "-conversation"


def hindsight_explicit_document_id(session_id: str, fact: str) -> str:
    digest = hashlib.sha256(fact.strip().encode("utf-8")).hexdigest()[:32]
    return hindsight_session_prefix(session_id) + "-explicit-" + digest


def _record_hindsight_document(chat_id: str, session_id: str, document_id: str, kind: str) -> None:
    mapping_db = db_connect()
    try:
        def write_mapping():
            mapping_db.execute(
                "INSERT OR REPLACE INTO hindsight_documents(chat_id,session_id,document_id,kind,created_at) VALUES(?,?,?,?,?)",
                (str(chat_id), str(session_id), str(document_id), str(kind), time.time()),
            )
            mapping_db.commit()
        run_write_txn(mapping_db, write_mapping)
    finally:
        mapping_db.close()


def _hindsight_not_found(exc: Exception) -> bool:
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    return status == 404 or "404" in str(exc)


async def _listed_hindsight_document_ids(api, bank_id: str, **filters) -> set[str]:
    found = set()
    offset = 0
    while True:
        result = await api.list_documents(bank_id=bank_id, limit=1000, offset=offset, **filters)
        items = list(getattr(result, "items", []) or [])
        for item in items:
            document_id = item.get("id") if isinstance(item, dict) else getattr(item, "id", "")
            if document_id:
                found.add(str(document_id))
        offset += len(items)
        if not items or offset >= int(getattr(result, "total", 0) or 0):
            break
    return found


async def _delete_hindsight_session_documents(client, bank_id: str, session_id: str, mapped_ids: set[str]) -> int:
    api = client.documents
    tag = f"session:{session_id}"
    prefix = hindsight_session_prefix(session_id)
    try:
        tagged = await _listed_hindsight_document_ids(api, bank_id, tags=[tag], tags_match="any_strict")
        prefixed = await _listed_hindsight_document_ids(api, bank_id, q=prefix)
    except Exception as exc:
        if _hindsight_not_found(exc):
            return 0
        raise
    document_ids = tagged | prefixed | set(mapped_ids) | {f"st-session-{session_id}"}
    deleted = 0
    for document_id in sorted(document_ids):
        try:
            await api.delete_document(bank_id=bank_id, document_id=document_id)
            deleted += 1
        except Exception as exc:
            if not _hindsight_not_found(exc):
                raise
    remaining = (
        await _listed_hindsight_document_ids(api, bank_id, tags=[tag], tags_match="any_strict")
        | await _listed_hindsight_document_ids(api, bank_id, q=prefix)
    )
    if remaining:
        raise RuntimeError("Hindsight session documents remain after deletion")
    return deleted


async def _delete_hindsight_session_documents_and_close(client, bank_id: str, session_id: str, mapped_ids: set[str]) -> int:
    try:
        return await _delete_hindsight_session_documents(client, bank_id, session_id, mapped_ids)
    finally:
        await _close_hindsight_client_async(client)


def _purge_hindsight_session_backend(db: sqlite3.Connection, chat_id: str, session_id: str) -> int:
    """Delete only documents attributable to one session, failing closed."""
    with hindsight_session_lock(chat_id, session_id):
        mapped_ids = {
            str(row[0]) for row in db.execute(
                "SELECT document_id FROM hindsight_documents WHERE chat_id=? AND session_id=?",
                (str(chat_id), str(session_id)),
            ).fetchall()
        }
        try:
            return asyncio.run(_delete_hindsight_session_documents_and_close(
                hindsight_client(), hindsight_bank_id(chat_id), str(session_id), mapped_ids,
            ))
        except Exception as exc:
            logging.error("Hindsight session purge failed for %s/%s", chat_id, session_id, exc_info=True)
            raise RuntimeError("Hindsight session memory cleanup failed") from exc


def memory_mode(db: sqlite3.Connection, chat_id: str) -> str:
    return get_meta(db, f"memory_mode:{chat_id}", "on")


def memory_scope(db: sqlite3.Connection, chat_id: str) -> str:
    return "session"


def memory_recall_filter(db: sqlite3.Connection, chat_id: str, session: dict[str, str], character_name: str) -> list[str]:
    tags = hindsight_tags(chat_id, session["session_id"], character_name)
    return [tags[1]]


def recall_memory_results(db: sqlite3.Connection, chat_id: str, session: dict[str, str], query: str, character_name: str = "", max_tokens: int = HINDSIGHT_RECALL_MAX_TOKENS):
    if memory_mode(db, chat_id) != "on" or not query.strip():
        return []
    client = None
    try:
        client = hindsight_client()
        results = client.recall(
            bank_id=hindsight_bank_id(chat_id),
            query=query[:4000],
            max_tokens=max_tokens,
            budget="low",
            tags=memory_recall_filter(db, chat_id, session, character_name or session.get("character_file", "unknown")),
            tags_match="any_strict",
        )
        return list(getattr(results, "results", []) or [])
    except Exception:
        logging.warning("Hindsight recall unavailable for chat %s", chat_id, exc_info=True)
        return []
    finally:
        close_hindsight_client(client)


def recall_memory_context(db: sqlite3.Connection, chat_id: str, session: dict[str, str], fields: dict[str, str], query: str) -> str:
    results = recall_memory_results(db, chat_id, session, query, fields["name"])
    sections = []
    for result in results:
        text = str(getattr(result, "text", "") or "").strip()
        if text:
            sections.append("- " + text)
    return "\n".join(sections)[:HINDSIGHT_CONTEXT_MAX_CHARS]


def _retain_with_client(chat_id: str, session_id: str, document_id: str, character_name: str,
                        content: str, context: str, kind: str, log_message: str) -> bool:
    """Retain one document via a short-lived Hindsight client; failures are logged, not raised."""
    client = None
    try:
        client = hindsight_client()
        client.retain(
            bank_id=hindsight_bank_id(chat_id),
            content=content,
            context=context,
            document_id=document_id,
            metadata={"source": "sillytavern_telegram_bridge", "session_id": session_id, "character": character_name},
            tags=hindsight_tags(chat_id, session_id, character_name),
            retain_async=False,
        )
        _record_hindsight_document(chat_id, session_id, document_id, kind)
        return True
    except Exception:
        logging.warning(log_message, chat_id, exc_info=True)
        return False
    finally:
        close_hindsight_client(client)


def _memory_hindsight_epoch_key(
    chat_id: str,
    session_id: str,
) -> str:
    return f"hindsight_epoch:{chat_id}:{session_id}"


def _memory_hindsight_epoch(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> int:
    try:
        return max(
            0,
            int(
                get_meta(
                    db,
                    _memory_hindsight_epoch_key(
                        chat_id,
                        session_id,
                    ),
                    "0",
                )
                or 0
            ),
        )
    except (TypeError, ValueError):
        return 0


def _memory_hindsight_session_exists(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> bool:
    return bool(
        db.execute(
            "SELECT 1 FROM sessions "
            "WHERE chat_id=? AND session_id=?",
            (str(chat_id), str(session_id)),
        ).fetchone()
    )


def _memory_hindsight_conversation_snapshot(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> tuple[str, str]:
    rows = db.execute(
        "SELECT role,content,created_at FROM messages "
        "WHERE chat_id=? AND session_id=? "
        "ORDER BY created_at DESC,rowid DESC LIMIT ?",
        (
            chat_id,
            session_id,
            HINDSIGHT_RETAIN_MAX_MESSAGES,
        ),
    ).fetchall()
    rows = list(reversed(rows))
    if not rows:
        return "", ""

    conversation = json.dumps(
        [
            {
                "role": role,
                "content": content,
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(created_at),
                ),
            }
            for role, content, created_at in rows
        ],
        ensure_ascii=False,
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                [str(role), str(content)]
                for role, content, _created_at in rows
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return conversation, fingerprint


def _write_hindsight_successful_purge_state(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> None:
    def write_purge_state():
        db.execute(
            "DELETE FROM hindsight_documents "
            "WHERE chat_id=? AND session_id=?",
            (str(chat_id), str(session_id)),
        )
        next_epoch = (
            _memory_hindsight_epoch(
                db,
                chat_id,
                session_id,
            )
            + 1
        )
        db.execute(
            "INSERT OR REPLACE INTO meta(key,value) "
            "VALUES(?,?)",
            (
                _memory_hindsight_epoch_key(
                    chat_id,
                    session_id,
                ),
                str(next_epoch),
            ),
        )
        db.commit()

    run_write_txn(db, write_purge_state)


def _retain_session_memory_backend(chat_id: str, session: dict[str, str], character_name: str, conversation: str) -> None:
    session_id = str(session["session_id"])
    with hindsight_session_lock(chat_id, session_id):
        session_db = db_connect()
        try:
            exists = session_db.execute(
                "SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?",
                (str(chat_id), session_id),
            ).fetchone()
        finally:
            session_db.close()
        if not exists:
            return
        document_id = hindsight_conversation_document_id(session_id)
        _retain_with_client(
            chat_id,
            session_id,
            document_id,
            character_name,
            conversation,
            f"SillyTavern Telegram roleplay session with character {character_name}",
            "conversation",
            "Hindsight retain unavailable for chat %s",
        )


def remember_fact(db: sqlite3.Connection, chat_id: str, session: dict[str, str], fields: dict[str, str], fact: str) -> bool:
    if not fact.strip():
        return False
    session_id = str(session["session_id"])
    with hindsight_session_lock(chat_id, session_id):
        if not db.execute("SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?", (str(chat_id), session_id)).fetchone():
            return False
        document_id = hindsight_explicit_document_id(session_id, fact)
        return _retain_with_client(
            chat_id,
            session_id,
            document_id,
            fields["name"],
            f"User explicitly stated: {fact.strip()[:4000]}",
            f"Explicit user memory request for character {fields['name']}",
            "explicit",
            "Hindsight explicit retain unavailable for chat %s",
        )

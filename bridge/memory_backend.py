"""Canonical Hindsight memory backend and session-scoped memory state."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import sqlite3
import threading
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

from bridge.limits import (
    HINDSIGHT_CONTEXT_MAX_CHARS,
    HINDSIGHT_DEFAULT_URL,
    HINDSIGHT_RECALL_MAX_TOKENS,
    HINDSIGHT_RETAIN_MAX_MESSAGES,
)
from bridge.meta_repository import delete_meta_value, load_meta_value, store_meta_value
from bridge.metadata import get_meta
from bridge.settings import AppSettings
from bridge.sqlite_store import db_connect, write_transaction


def hindsight_bank_id(chat_id: str) -> str:
    return "sillytavern-telegram-" + hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:24]


def hindsight_tags(chat_id: str, session_id: str, character_name: str) -> list[str]:
    user_key = hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:24]
    character_key = "".join(ch.lower() if ch.isalnum() else "-" for ch in character_name).strip("-")[:48] or "unknown"
    return [f"user:telegram-{user_key}", f"session:{session_id}", f"character:{character_key}"]


def _validated_hindsight_base_url(value: str) -> tuple[str, str]:
    raw = str(value or "").strip().rstrip("/")
    message = "Hindsight SDK endpoint must use a numeric loopback origin"
    try:
        parsed = urlsplit(raw)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(message) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(message)
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError(message) from exc
    if not address.is_loopback:
        raise ValueError(message)
    return raw, address.compressed


def _ensure_hindsight_loopback_proxy_bypass(host: str) -> None:
    # Merge read both spellings (NO_PROXY and no_proxy) but wrote the merged
    # value back to both, so every call re-read its own previous output twice
    # and doubled the variable: 3 * 2**(n-1) entries, 42MB and 6.3M items
    # after 22 client constructions. urllib parses NO_PROXY on every request,
    # so the growth stalled polling outright. Deduplicate on read so the merge
    # is idempotent, and skip the write when nothing changed so os.environ -
    # and the copy handed to every child process - stays stable.
    values: list[str] = []
    seen: set[str] = set()
    for name in ("NO_PROXY", "no_proxy"):
        for item in os.environ.get(name, "").split(","):
            entry = item.strip()
            if entry and entry not in seen:
                seen.add(entry)
                values.append(entry)
    for required in (host, "127.0.0.1", "::1", "[::1]"):
        if required not in seen:
            seen.add(required)
            values.append(required)
    combined = ",".join(values)
    for name in ("NO_PROXY", "no_proxy"):
        if os.environ.get(name) != combined:
            os.environ[name] = combined


def hindsight_client(*, app_settings: AppSettings):
    base_url, host = _validated_hindsight_base_url(app_settings.environ.get("HINDSIGHT_API_URL", HINDSIGHT_DEFAULT_URL))
    _ensure_hindsight_loopback_proxy_bypass(host)

    from hindsight_client import Hindsight

    api_key = app_settings.environ.get("HINDSIGHT_API_KEY") or None
    return Hindsight(base_url=base_url, api_key=api_key, timeout=30.0, user_agent="SillyTavernTelegramBridge/1.0")


_HINDSIGHT_SESSION_LOCKS: weakref.WeakValueDictionary[tuple[str, str], threading.RLock] = weakref.WeakValueDictionary()
_HINDSIGHT_SESSION_LOCKS_GUARD = threading.Lock()


def close_hindsight_client(client) -> None:
    """Close the supported SDK wrapper on its own synchronous lifecycle."""
    if client is None:
        return
    try:
        client.close()
    except Exception:
        logging.debug("Could not close Hindsight client cleanly", exc_info=True)


@contextmanager
def hindsight_client_scope(*, app_settings: AppSettings) -> Iterator[Any]:
    """Own the loop used by one synchronous SDK client, including its cleanup.

    The SDK's sync API reuses the thread's current loop but does not close it.
    Runner installs an owned loop before construction and drains/closes it after
    the transport, even when client construction, requests, or cleanup fail.
    """
    with asyncio.Runner():
        client = hindsight_client(app_settings=app_settings)
        try:
            yield client
        finally:
            close_hindsight_client(client)


def hindsight_session_lock(chat_id: str, session_id: str) -> threading.RLock:
    """Keep one reentrant lock per live scope without retaining past sessions."""
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


def _record_hindsight_document(
    chat_id: str, session_id: str, document_id: str, kind: str, *, app_settings: AppSettings
) -> None:
    mapping_db = db_connect(app_settings=app_settings)
    try:

        def write_mapping():
            mapping_db.execute(
                (
                    "INSERT OR REPLACE INTO hindsight_documents(chat_id,session_id,document_i"
                    "d,kind,created_at) VALUES(?,?,?,?,?)"
                ),
                (str(chat_id), str(session_id), str(document_id), str(kind), time.time()),
            )

        with write_transaction(mapping_db):
            write_mapping()
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
    document_ids = (
        tagged
        | prefixed
        | set(mapped_ids)
        | {
            f"st-session-{session_id}",
            f"{prefix}-curated",
        }
    )
    deleted = 0
    for document_id in sorted(document_ids):
        try:
            await api.delete_document(bank_id=bank_id, document_id=document_id)
            deleted += 1
        except Exception as exc:
            if not _hindsight_not_found(exc):
                raise
    remaining = await _listed_hindsight_document_ids(
        api, bank_id, tags=[tag], tags_match="any_strict"
    ) | await _listed_hindsight_document_ids(api, bank_id, q=prefix)
    if remaining:
        raise RuntimeError("Hindsight session documents remain after deletion")
    return deleted


async def _delete_hindsight_session_documents_and_close(
    client, bank_id: str, session_id: str, mapped_ids: set[str]
) -> int:
    try:
        return await _delete_hindsight_session_documents(client, bank_id, session_id, mapped_ids)
    finally:
        await client.aclose()


def _purge_hindsight_session_backend(
    db: sqlite3.Connection, chat_id: str, session_id: str, *, app_settings: AppSettings
) -> int:
    """Delete only documents attributable to one session, failing closed."""
    with hindsight_session_lock(chat_id, session_id):
        mapped_ids = {
            str(row[0])
            for row in db.execute(
                "SELECT document_id FROM hindsight_documents WHERE chat_id=? AND session_id=?",
                (str(chat_id), str(session_id)),
            ).fetchall()
        }
        try:
            return asyncio.run(
                _delete_hindsight_session_documents_and_close(
                    hindsight_client(app_settings=app_settings),
                    hindsight_bank_id(chat_id),
                    str(session_id),
                    mapped_ids,
                )
            )
        except Exception as exc:
            logging.error("Hindsight session purge failed for %s/%s", chat_id, session_id, exc_info=True)
            raise RuntimeError("Hindsight session memory cleanup failed") from exc


def memory_mode(db: sqlite3.Connection, chat_id: str) -> str:
    return get_meta(db, f"memory_mode:{chat_id}", "on")


def memory_scope(db: sqlite3.Connection, chat_id: str) -> str:
    return "session"


def memory_recall_filter(
    db: sqlite3.Connection, chat_id: str, session: dict[str, str], character_name: str
) -> list[str]:
    tags = hindsight_tags(chat_id, session["session_id"], character_name)
    return [tags[1]]


def recall_memory_results(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    query: str,
    character_name: str = "",
    max_tokens: int = HINDSIGHT_RECALL_MAX_TOKENS,
    *,
    app_settings: AppSettings,
):
    if memory_mode(db, chat_id) != "on" or not query.strip():
        return []
    try:
        with hindsight_client_scope(app_settings=app_settings) as client:
            results = client.recall(
                bank_id=hindsight_bank_id(chat_id),
                query=query[:4000],
                max_tokens=max_tokens,
                budget="low",
                tags=memory_recall_filter(
                    db, chat_id, session, character_name or session.get("character_file", "unknown")
                ),
                tags_match="any_strict",
            )
            return list(getattr(results, "results", []) or [])
    except Exception:
        logging.warning("Hindsight recall unavailable for chat %s", chat_id, exc_info=True)
        return []


def recall_memory_context(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
    query: str,
    *,
    app_settings: AppSettings,
) -> str:
    results = recall_memory_results(db, chat_id, session, query, fields["name"], app_settings=app_settings)
    sections = []
    for result in results:
        text = str(getattr(result, "text", "") or "").strip()
        if text:
            sections.append("- " + text)
    return "\n".join(sections)[:HINDSIGHT_CONTEXT_MAX_CHARS]


def _retain_with_client(
    chat_id: str,
    session_id: str,
    document_id: str,
    character_name: str,
    content: str,
    context: str,
    kind: str,
    log_message: str,
    *,
    app_settings: AppSettings,
) -> bool:
    """Retain one document via a short-lived Hindsight client; failures are logged, not raised."""
    try:
        with hindsight_client_scope(app_settings=app_settings) as client:
            client.retain(
                bank_id=hindsight_bank_id(chat_id),
                content=content,
                context=context,
                document_id=document_id,
                metadata={
                    "source": "sillytavern_telegram_bridge",
                    "session_id": session_id,
                    "character": character_name,
                },
                tags=hindsight_tags(chat_id, session_id, character_name),
                retain_async=False,
            )
            _record_hindsight_document(chat_id, session_id, document_id, kind, app_settings=app_settings)
            return True
    except Exception:
        logging.warning(log_message, chat_id, exc_info=True)
        return False


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


def clear_curated_memory_state(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    """Invalidate in-flight curator work and remove local derived state.

    Lifecycle callers joining a transaction must already own the Hindsight lock.
    """
    with hindsight_session_lock(chat_id, session_id), write_transaction(db):
        revision_key = f"memory_curator_revision:{chat_id}:{session_id}"
        revision = int(load_meta_value(db, revision_key, "0") or 0)
        store_meta_value(db, revision_key, str(revision + 1))
        delete_meta_value(db, f"memory_curator:{chat_id}:{session_id}")


def _memory_hindsight_session_exists(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> bool:
    return bool(
        db.execute(
            "SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?",
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
    # Byte-safe cap: trim oldest turns from the assembled conversation so newest
    # turns survive (fixes PR #352 rejection: trimming from start keeps newest).
    max_bytes = int(os.getenv("STT_MEMORY_CONTENT_MAX", "51200"))
    conversation_bytes = conversation.encode("utf-8")
    if len(conversation_bytes) > max_bytes:
        trimmed_bytes = conversation_bytes[-max_bytes:]  # newest at end preserved
        conversation = trimmed_bytes.decode("utf-8", errors="replace")
    fingerprint = hashlib.sha256(
        json.dumps(
            [[str(role), str(content)] for role, content, _created_at in rows],
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
            "DELETE FROM hindsight_documents WHERE chat_id=? AND session_id=?",
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
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            (
                _memory_hindsight_epoch_key(
                    chat_id,
                    session_id,
                ),
                str(next_epoch),
            ),
        )

    with write_transaction(db):
        write_purge_state()


def _retain_session_memory_backend(
    chat_id: str, session: dict[str, str], character_name: str, conversation: str, *, app_settings: AppSettings
) -> None:
    session_id = str(session["session_id"])
    with hindsight_session_lock(chat_id, session_id):
        session_db = db_connect(app_settings=app_settings)
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
            app_settings=app_settings,
        )


def remember_fact(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
    fact: str,
    *,
    app_settings: AppSettings,
) -> bool:
    if not fact.strip():
        return False
    session_id = str(session["session_id"])
    with hindsight_session_lock(chat_id, session_id):
        if not db.execute(
            "SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?", (str(chat_id), session_id)
        ).fetchone():
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
            app_settings=app_settings,
        )

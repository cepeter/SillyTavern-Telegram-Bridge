"""Canonical Hindsight memory backend and session-scoped memory state."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from bridge.hindsight_client_runtime import close_hindsight_client as close_hindsight_client
from bridge.hindsight_client_runtime import hindsight_client as hindsight_client
from bridge.hindsight_diagnostics import (
    handle_hindsight_retain_failure,
    hindsight_retain_scope,
    report_hindsight_completion,
)
from bridge.hindsight_endpoint import (
    _ensure_hindsight_loopback_proxy_bypass as _ensure_hindsight_loopback_proxy_bypass,
)
from bridge.limits import (
    HINDSIGHT_RECALL_MAX_TOKENS,
    HINDSIGHT_RETAIN_MAX_MESSAGES,
)
from bridge.memory_contracts import MemoryBlock, MemoryReadScope, MemorySearchResult
from bridge.memory_fact_store import index_fact_is_current, remember_local_fact
from bridge.memory_identity import (
    hindsight_generation_tags as hindsight_generation_tags,
)
from bridge.memory_identity import (
    hindsight_session_prefix as hindsight_session_prefix,
)
from bridge.memory_retirement_store import RETIREMENT_SCOPE_UNPAUSED
from bridge.memory_scope_store import eligible_fact, ranked_fact_block, resolve_memory_scope, scope_is_current
from bridge.memory_store import (
    ARCHIVAL_PENDING,
    archival_attempts_outstanding,
    purge_external_memory,
    reconcile_archival_attempts,
    retire_derived_layer,
)
from bridge.meta_repository import delete_meta_value, load_meta_value, store_meta_value
from bridge.metadata import get_meta
from bridge.port_contracts import ForegroundRecall
from bridge.settings import AppSettings
from bridge.sqlite_store import write_transaction


def hindsight_bank_id(chat_id: str) -> str:
    return "sillytavern-telegram-" + hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:24]


def hindsight_tags(chat_id: str, session_id: str, character_name: str) -> list[str]:
    user_key = hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:24]
    character_key = "".join(ch.lower() if ch.isalnum() else "-" for ch in character_name).strip("-")[:48] or "unknown"
    return [f"user:telegram-{user_key}", f"session:{session_id}", f"character:{character_key}"]


_HINDSIGHT_SESSION_LOCKS: weakref.WeakValueDictionary[tuple[str, str], threading.RLock] = weakref.WeakValueDictionary()
_HINDSIGHT_SESSION_LOCKS_GUARD = threading.Lock()


@contextmanager
def hindsight_client_scope(*, app_settings: AppSettings, request_timeout: float | None = None) -> Iterator[Any]:
    """Own the loop used by one synchronous SDK client, including its cleanup.

    The SDK's sync API reuses the thread's current loop but does not close it.
    Runner installs an owned loop before construction and drains/closes it after
    the transport, even when client construction, requests, or cleanup fail.
    """
    with asyncio.Runner():
        client = (
            hindsight_client(app_settings=app_settings)
            if request_timeout is None
            else hindsight_client(app_settings=app_settings, request_timeout=request_timeout)
        )
        try:
            yield client
        finally:
            close_hindsight_client(client)


def hindsight_session_lock(chat_id: str, session_id: str) -> threading.RLock:
    """Keep one reentrant lock per live scope without retaining past sessions."""
    key = (str(chat_id), str(session_id))
    with _HINDSIGHT_SESSION_LOCKS_GUARD:
        return _HINDSIGHT_SESSION_LOCKS.setdefault(key, threading.RLock())


def hindsight_conversation_document_id(session_id: str) -> str:
    return hindsight_session_prefix(session_id) + "-conversation"


def hindsight_explicit_document_id(session_id: str, fact: str) -> str:
    digest = hashlib.sha256(fact.strip().encode("utf-8")).hexdigest()[:32]
    return hindsight_session_prefix(session_id) + "-explicit-" + digest


def _hindsight_not_found(exc: Exception) -> bool:
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    return status == 404 or "404" in str(exc)


async def _listed_hindsight_document_ids(api: Any, bank_id: str, **filters: Any) -> set[str]:
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


async def _delete_hindsight_session_documents(client: Any, bank_id: str, session_id: str, mapped_ids: set[str]) -> int:
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
    client: Any, bank_id: str, session_id: str, mapped_ids: set[str]
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
    return [tags[1], "native-fact"]


def recall_memory_results(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    query: str,
    character_name: str = "",
    max_tokens: int = HINDSIGHT_RECALL_MAX_TOKENS,
    *,
    app_settings: AppSettings,
    read_scope: MemoryReadScope | None = None,
    remote_recall: ForegroundRecall | None = None,
) -> list[MemorySearchResult]:
    if remote_recall is None or memory_mode(db, chat_id) != "on" or not query.strip():
        return []
    scope = read_scope or resolve_memory_scope(db, chat_id, session, {"name": character_name})
    if scope is None or not scope_is_current(db, scope, external=True):
        return []
    try:
        results = remote_recall(
            bank_id=hindsight_bank_id(chat_id),
            session_id=session["session_id"],
            query=query[:4000],
            max_tokens=max_tokens,
        )
        if memory_mode(db, chat_id) != "on" or not scope_is_current(db, scope, external=True):
            return []
        accepted = []
        seen: set[str] = set()
        for document_id, kind in results[:64]:
            if kind not in {"world", "experience"} or document_id in seen:
                continue
            if not memory_document_is_current(db, chat_id, session["session_id"], document_id):
                continue
            indexed = index_fact_is_current(db, document_id)
            fact = eligible_fact(db, scope, indexed.memory_id) if indexed else None
            if fact is not None:
                seen.add(document_id)
                accepted.append(MemorySearchResult(document_id=document_id, text=fact.fact.summary, type=kind))
        return accepted
    except Exception:
        logging.warning("Hindsight recall unavailable for chat %s", chat_id, exc_info=True)
        return []


def memory_document_is_current(db: sqlite3.Connection, chat_id: str, session_id: str, document_id: str) -> bool:
    """Only retained local native-fact documents may contribute semantic rank."""
    row = db.execute(
        "SELECT 1 FROM memory_fact_index WHERE document_id=? AND chat_id=? AND session_id=? AND state='retained'",
        (document_id, chat_id, session_id),
    ).fetchone()
    return bool(row and index_fact_is_current(db, document_id))


async def _delete_retired_with_client(client: Any, bank_id: str, documents: list[str]) -> None:
    for document_id in documents:
        try:
            await client.documents.delete_document(bank_id=bank_id, document_id=document_id)
        except Exception as exc:
            if not _hindsight_not_found(exc):
                raise


def _retirement_would_delete_current_source(db: sqlite3.Connection, document_id: str) -> bool:
    return bool(
        db.execute(
            "SELECT 1 FROM memory_segments g JOIN sessions s ON s.chat_id=g.chat_id AND s.session_id=g.session_id "
            "AND s.created_at=g.session_created_at WHERE g.document_id=? AND g.valid IN (1,?) AND g.layer<>'hindsight'",
            (document_id, ARCHIVAL_PENDING),
        ).fetchone()
        or db.execute(
            "SELECT 1 FROM memory_fact_index f JOIN sessions s ON s.chat_id=f.chat_id AND s.session_id=f.session_id "
            "AND s.created_at=f.session_created_at WHERE f.document_id=? AND f.state<>'retired'",
            (document_id,),
        ).fetchone()
    )


def cleanup_retired_memory_documents(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    app_settings: AppSettings,
    lease_token: str = "",
    blocking_only: bool = False,
) -> bool:
    """Delete one bounded exact-ID batch without holding the lifecycle lock over I/O."""
    if db.in_transaction:
        raise RuntimeError("Memory cleanup cannot run in a transaction")
    try:
        reconcile_archival_attempts(db, chat_id=chat_id, session_id=session_id)
    except Exception:
        logging.warning("Archival recovery deferred for chat %s", chat_id, exc_info=True)
        return False
    own_token = not lease_token
    token = lease_token or uuid.uuid4().hex
    with hindsight_session_lock(chat_id, session_id), write_transaction(db):
        rows = db.execute(
            "SELECT document_id FROM memory_retired_documents r WHERE chat_id=? AND session_id=? AND deleted=0 "  # noqa: S608 -- fixed internal SQL predicate; values are bound
            "AND next_attempt_at<=? AND lease_token=? AND (NOT ? OR NOT EXISTS(SELECT 1 FROM "
            "memory_archival_attempts a "
            "WHERE a.document_id=r.document_id AND a.finished=0)) AND "
            + RETIREMENT_SCOPE_UNPAUSED
            + " ORDER BY document_id LIMIT 16",
            (chat_id, session_id, time.time(), lease_token, int(blocking_only), time.time()),
        ).fetchall()
        if not rows:
            return True
        if own_token:
            db.executemany(
                "UPDATE memory_retired_documents SET lease_token=?,lease_deadline=? "
                "WHERE chat_id=? AND session_id=? AND document_id=? AND lease_token=''",
                [(token, time.time() + 900, chat_id, session_id, doc) for (doc,) in rows],
            )
    try:
        for (document_id,) in rows:
            succeeded = False
            with hindsight_session_lock(chat_id, session_id), write_transaction(db):
                row = db.execute(
                    "SELECT retirement_revision FROM memory_retired_documents WHERE chat_id=? AND session_id=? "
                    "AND document_id=? AND deleted=0 AND lease_token=?",
                    (chat_id, session_id, document_id, token),
                ).fetchone()
                if row is None:
                    continue
                revision = row[0]
                outstanding_at_start = archival_attempts_outstanding(db, document_id)
                db.execute(
                    "UPDATE memory_retired_documents SET lease_deadline=? "
                    "WHERE chat_id=? AND session_id=? AND deleted=0 AND lease_token=?",
                    (time.time() + 900, chat_id, session_id, token),
                )
                eligible = not _retirement_would_delete_current_source(db, document_id)
            if eligible:
                try:
                    with hindsight_client_scope(app_settings=app_settings) as client:
                        asyncio.get_event_loop().run_until_complete(
                            _delete_retired_with_client(client, hindsight_bank_id(chat_id), [document_id])
                        )
                    succeeded = True
                except Exception:
                    logging.warning("Retired memory document cleanup deferred")
            with hindsight_session_lock(chat_id, session_id), write_transaction(db):
                terminal = succeeded and not outstanding_at_start and not archival_attempts_outstanding(db, document_id)
                db.execute(
                    "UPDATE memory_retired_documents SET deleted=?,attempts=attempts+1,"
                    "next_attempt_at=CASE WHEN ? THEN 0 "
                    "WHEN ? THEN ?+MIN(3600,300*(1 << MIN(attempts/4,4))) "
                    "ELSE ?+MIN(3600,5*(1 << MIN(attempts,10))) END "
                    "WHERE chat_id=? AND session_id=? AND document_id=? AND deleted=0 AND lease_token=? "
                    "AND retirement_revision=?",
                    (
                        int(terminal),
                        int(terminal),
                        int(succeeded),
                        time.time(),
                        time.time(),
                        chat_id,
                        session_id,
                        document_id,
                        token,
                        revision,
                    ),
                )
            if not succeeded:
                return False
        return not db.execute(
            "SELECT 1 FROM memory_retired_documents r WHERE chat_id=? AND session_id=? AND deleted=0 "
            "AND next_attempt_at<=? AND (NOT ? OR NOT EXISTS(SELECT 1 FROM memory_archival_attempts a "
            "WHERE a.document_id=r.document_id AND a.finished=0))",
            (chat_id, session_id, time.time(), int(blocking_only)),
        ).fetchone()
    finally:
        if own_token:
            with write_transaction(db):
                db.execute(
                    "UPDATE memory_retired_documents SET lease_token='',lease_deadline=0 WHERE lease_token=?", (token,)
                )


def list_hindsight_cleanup_page(
    chat_id: str,
    session_id: str,
    query_kind: str,
    offset: int,
    *,
    app_settings: AppSettings,
) -> Any:
    """One public metadata page; enumeration never invokes broad deletion."""
    filters = (
        {"tags": [f"session:{session_id}"], "tags_match": "any_strict"}
        if query_kind == "tag"
        else {"q": hindsight_session_prefix(session_id)}
    )
    try:
        with hindsight_client_scope(app_settings=app_settings) as client:
            return asyncio.get_event_loop().run_until_complete(
                client.documents.list_documents(
                    bank_id=hindsight_bank_id(chat_id),
                    limit=1000,
                    offset=offset,
                    **filters,
                )
            )
    except Exception as exc:
        if _hindsight_not_found(exc):
            return SimpleNamespace(items=[], total=0)
        raise


def recall_memory_context(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
    query: str,
    *,
    app_settings: AppSettings,
    remote_recall: ForegroundRecall | None = None,
) -> str:
    scope = resolve_memory_scope(db, chat_id, session, fields)
    return (
        recall_scoped_memory(db, scope, query, app_settings=app_settings, remote_recall=remote_recall).text
        if scope
        else ""
    )


def recall_scoped_memory(
    db: sqlite3.Connection,
    scope: MemoryReadScope,
    query: str,
    *,
    app_settings: AppSettings,
    remote_recall: ForegroundRecall | None = None,
) -> MemoryBlock:
    results = recall_memory_results(
        db,
        scope.chat_id,
        {"session_id": scope.session_id},
        query,
        scope.principals[0] if scope.principals else "",
        app_settings=app_settings,
        read_scope=scope,
        remote_recall=remote_recall,
    )
    return ranked_fact_block(db, scope, [str(getattr(result, "document_id", "") or "") for result in results])


def _retain_with_client(
    chat_id: str,
    session_id: str,
    document_id: str,
    character_name: str,
    content: str,
    context: str,
    kind: str,
    *,
    app_settings: AppSettings,
    generation_tags: tuple[str, ...] = (),
) -> bool:
    """Retain an accepted native summary; mapping acceptance belongs to its fenced worker."""
    if kind != "native_fact":
        raise ValueError("Only native facts may be retained in Hindsight")
    with hindsight_retain_scope(document_id, session_id, character_name) as metadata:
        try:
            with hindsight_client_scope(app_settings=app_settings, request_timeout=90.0) as client:
                response = client.retain(
                    bank_id=hindsight_bank_id(chat_id),
                    content=content,
                    context=context,
                    document_id=document_id,
                    metadata=metadata,
                    tags=[*hindsight_tags(chat_id, session_id, character_name), "native-fact", *generation_tags],
                    retain_async=False,
                )
                report_hindsight_completion(response)
                return True
        except Exception as error:
            return handle_hindsight_retain_failure(error, chat_id, session_id)


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
        return max(0, int(get_meta(db, _memory_hindsight_epoch_key(chat_id, session_id), "0") or 0))
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
        retire_derived_layer(db, chat_id, session_id, "curator")


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
    fingerprint = hashlib.sha256(
        json.dumps(
            [[str(role), str(content)] for role, content, _created_at in rows],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return conversation, fingerprint


def _prepare_hindsight_purge_state(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    with hindsight_session_lock(chat_id, session_id), write_transaction(db):
        next_epoch = _memory_hindsight_epoch(db, chat_id, session_id) + 1
        purge_external_memory(db, chat_id, session_id, purge_epoch=next_epoch)
        store_meta_value(db, _memory_hindsight_epoch_key(chat_id, session_id), str(next_epoch))


def _write_hindsight_successful_purge_state(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    with write_transaction(db):
        db.execute("DELETE FROM hindsight_documents WHERE chat_id=? AND session_id=?", (chat_id, session_id))
        db.execute(
            "UPDATE memory_retired_documents SET deleted=CASE WHEN EXISTS("
            "SELECT 1 FROM memory_segments g WHERE g.document_id=memory_retired_documents.document_id "
            "AND g.layer='hindsight') OR EXISTS(SELECT 1 FROM memory_archival_attempts a "
            "WHERE a.document_id=memory_retired_documents.document_id) THEN 0 ELSE 1 END,next_attempt_at=0 "
            "WHERE chat_id=? AND session_id=?",
            (chat_id, session_id),
        )


def _retain_session_memory_backend(
    chat_id: str, session: dict[str, str], character_name: str, conversation: str, *, app_settings: AppSettings
) -> bool:
    logging.warning("Legacy conversation export is unsupported; native facts are indexed by durable workers")
    return False


def remember_fact(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
    fact: str,
    *,
    app_settings: AppSettings,
) -> bool:
    return bool(remember_local_fact(db, str(chat_id), session["session_id"], fields.get("name", ""), fact))

"""Optional near-real-time sync through SillyTavern's loopback HTTP API."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
from http.cookiejar import CookieJar
from urllib.parse import urlparse
import urllib.error
import urllib.request

import bridge.sillytavern_api as _st_api

from bridge.repositories import (
    count_session_messages as _count_session_messages,
)
from bridge.sync_poll_safety import (
    SyncPollSafetyAdapter as _SyncPollSafetyAdapter,
)
from bridge.sync_service import SyncService as _SyncService


PHASE3_SYNC_INTERVAL_SECONDS = 2.0
_PHASE3_WORKER_LOCK = threading.Lock()
_PHASE3_WORKER = None
_PHASE3_STOP_EVENT = threading.Event()
_PHASE3_STOP_RESULTS = {"sync ID mismatch; realtime stopped", "initial divergence; realtime stopped", "conflict detected; realtime stopped"}
_PHASE3_MAX_MESSAGE_CHARS = 12000
_PHASE3_MAX_TOTAL_CHARS = 200000


def _bounded_number(raw: str, default, low, high, cast):
    try:
        return min(high, max(low, cast(raw)))
    except (TypeError, ValueError):
        return default


def refresh_phase3_config() -> None:
    """Refresh realtime polling and loopback API configuration."""
    global PHASE3_SYNC_INTERVAL_SECONDS
    PHASE3_SYNC_INTERVAL_SECONDS = _bounded_number(
        os.environ.get("SILLYTAVERN_SYNC_API_INTERVAL_SECONDS", "2"),
        2.0,
        1.0,
        30.0,
        float,
    )
    _st_api.refresh_sillytavern_api_config()


refresh_phase3_config()


def _phase3_records(db: sqlite3.Connection, chat_id: str, session: dict[str, str], fields: dict[str, str], binding: dict[str, object], rows: list[tuple]) -> list[dict]:
    return build_sync_records(db, chat_id, session, fields, str(binding["sync_id"]), rows)


def _phase3_snapshot(records: list[dict]) -> tuple[dict, list[tuple[str, str]], dict[int, tuple[list[str], int]]]:
    metadata = records[0].get("chat_metadata", {}) if records and isinstance(records[0], dict) else {}
    messages = []
    total_chars = 0
    variants = {}
    message_index = 0
    for record in records[1:] if metadata else records:
        if not isinstance(record, dict) or record.get("is_system") or "mes" not in record:
            continue
        content = str(record.get("mes") or "").strip()
        if len(content) > _PHASE3_MAX_MESSAGE_CHARS:
            raise ValueError("SillyTavern API message exceeds the sync limit")
        if not content:
            continue
        total_chars += len(content)
        if total_chars > _PHASE3_MAX_TOTAL_CHARS:
            raise ValueError("SillyTavern API transcript exceeds the sync limit")
        messages.append(("user" if record.get("is_user") else "assistant", content))
        if not record.get("is_user") and isinstance(record.get("swipes"), list):
            swipes = [str(item).strip() for item in record["swipes"] if str(item or "").strip()][:8]
            if len(swipes) > 1:
                try:
                    selected = max(0, min(int(record.get("swipe_id", 0)), len(swipes) - 1))
                except (TypeError, ValueError):
                    selected = 0
                variants[message_index] = (swipes, selected)
        message_index += 1
    if not isinstance(metadata, dict):
        metadata = {}
    if not messages:
        raise ValueError("SillyTavern API chat contains no user/assistant messages")
    return metadata, messages, variants


def _phase3_reset_failures(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    def write():
        db.execute("UPDATE sync_bindings SET realtime_failures=0,realtime_next_retry_at=0,last_error='' WHERE chat_id=? AND session_id=?", (chat_id, session_id))
        db.commit()
    run_write_txn(db, write)


def _phase3_disable(db: sqlite3.Connection, chat_id: str, session_id: str, error: str) -> None:
    def write():
        db.execute("UPDATE sync_bindings SET realtime_enabled=0,last_error=?,realtime_next_retry_at=0 WHERE chat_id=? AND session_id=?", (error[:1000], chat_id, session_id))
        db.commit()
    run_write_txn(db, write)


def phase3_sync_now(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    """Synchronize one binding through SillyTavern's supported chat API."""
    client = _st_api.phase3_client()
    session = load_session(db, chat_id, session_id, DEFAULT_MODEL)
    binding = sync_binding(db, chat_id, session_id)
    file_id = sync_file_id(binding)
    group = group_state(db, chat_id, session_id)
    is_group = bool(group.get("enabled"))
    rows = sync_local_rows(db, chat_id, session_id)
    local_hash = sync_transcript_hash([(role, content) for _rowid, role, content, _created_at in rows])
    remote_records = client.get_chat(session, file_id, is_group)
    fields = card_fields_from_file(session["character_file"])
    if not remote_records:
        client.save_chat(session, fields, file_id, is_group, _phase3_records(db, chat_id, session, fields, binding, rows))
        set_sync_state(db, chat_id, session_id, local_hash, "bridge_to_sillytavern_api")
        _phase3_reset_failures(db, chat_id, session_id)
        return "created SillyTavern API chat"
    metadata, remote_messages, remote_variants = _phase3_snapshot(remote_records)
    remote_sync = metadata.get("bridge_sync") if isinstance(metadata.get("bridge_sync"), dict) else {}
    if remote_sync.get("sync_id") and remote_sync.get("sync_id") != binding["sync_id"]:
        set_sync_state(db, chat_id, session_id, local_hash, "", "sync_id_mismatch", "API chat sync ID does not match this session")
        _phase3_disable(db, chat_id, session_id, "sync ID mismatch")
        return "sync ID mismatch; realtime stopped"
    remote_hash = sync_transcript_hash(remote_messages)
    baseline = str(binding.get("last_hash") or "")
    if remote_hash == local_hash:
        set_sync_state(db, chat_id, session_id, local_hash, str(binding.get("last_direction") or ""))
        _phase3_reset_failures(db, chat_id, session_id)
        return "unchanged"
    if not baseline:
        set_sync_state(db, chat_id, session_id, local_hash, "", "initial_divergence", "API chat has no common checkpoint")
        _phase3_disable(db, chat_id, session_id, "initial divergence")
        return "initial divergence; realtime stopped"
    if local_hash == baseline and remote_hash != baseline:
        imported_hash = apply_sync_snapshot(db, chat_id, session, metadata, remote_messages, remote_variants)
        set_sync_state(db, chat_id, session_id, imported_hash, "sillytavern_api_to_bridge")
        _phase3_reset_failures(db, chat_id, session_id)
        return "imported SillyTavern API changes"
    if remote_hash == baseline and local_hash != baseline:
        client.save_chat(session, fields, file_id, is_group, _phase3_records(db, chat_id, session, fields, binding, rows))
        set_sync_state(db, chat_id, session_id, local_hash, "bridge_to_sillytavern_api")
        _phase3_reset_failures(db, chat_id, session_id)
        return "exported bridge changes through API"
    set_sync_state(db, chat_id, session_id, local_hash, "", "conflict", "both sides changed since the last checkpoint")
    _phase3_disable(db, chat_id, session_id, "conflict detected")
    return "conflict detected; realtime stopped"


def phase3_toggle_realtime(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    binding = sync_binding(db, chat_id, session_id)
    if binding.get("realtime_enabled"):
        _phase3_disable(db, chat_id, session_id, "")
        return "realtime API sync disabled"
    if not _st_api.phase3_api_configured():
        return "realtime API sync is not configured"
    try:
        result = phase3_sync_now(db, chat_id, session_id)
    except (_st_api.SillyTavernApiError, ValueError) as exc:
        _phase3_disable(db, chat_id, session_id, str(exc))
        return f"realtime API unavailable: {exc}"
    if result in _PHASE3_STOP_RESULTS:
        return result
    def mark_enabled():
        db.execute("UPDATE sync_bindings SET realtime_enabled=1,realtime_failures=0,realtime_next_retry_at=0,last_error='' WHERE chat_id=? AND session_id=?", (chat_id, session_id))
        db.commit()
    run_write_txn(db, mark_enabled)
    return "realtime API sync enabled; " + result


def phase3_sync_status_line(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    binding = sync_binding(db, chat_id, session_id)
    enabled = "on" if binding.get("realtime_enabled") else "off"
    configured = "configured" if _st_api.phase3_api_configured() else "not configured"
    return f"Live API sync: {enabled} ({configured})"


_SYNC_POLL_SAFETY = _SyncPollSafetyAdapter(
    sync_now=(
        lambda db, chat_id, session_id:
        phase3_sync_now(
            db,
            chat_id,
            session_id,
        )
    ),
    chat_lock=(
        lambda chat_id:
        chat_job_lock(chat_id)
    ),
    disable_realtime=(
        lambda db, chat_id, session_id, error:
        _phase3_disable(
            db,
            chat_id,
            session_id,
            error,
        )
    ),
    expected_errors=(
        _st_api.SillyTavernApiError,
        ValueError,
    ),
    sync_interval=(
        lambda:
        PHASE3_SYNC_INTERVAL_SECONDS
    ),
    now=(
        lambda:
        time.time()
    ),
    log_warning=(
        lambda message, *args, **kwargs:
        logging.warning(
            message,
            *args,
            **kwargs,
        )
    ),
)


def phase3_sync_poll(
    db: sqlite3.Connection,
) -> None:
    _SYNC_POLL_SAFETY.poll(db)


def _phase3_worker_loop(sync_service: _SyncService) -> None:
    db = None
    try:
        while not _PHASE3_STOP_EVENT.wait(PHASE3_SYNC_INTERVAL_SECONDS):
            try:
                if db is None:
                    db = db_connect()
                sync_service.poll(db)
            except Exception as exc:
                if db is not None and db.in_transaction:
                    try:
                        db.rollback()
                    except sqlite3.Error:
                        logging.debug("Could not rollback realtime sync transaction", exc_info=True)
                # A SQLite-level failure may leave the handle unsuitable for
                # reuse. Close it and let the next interval reconnect.
                if db is not None and isinstance(exc, sqlite3.Error):
                    db.close()
                    db = None
                logging.warning("Phase 3 realtime sync worker failed", exc_info=True)
    finally:
        if db is not None:
            db.close()


def start_phase3_sync_worker(*, sync_service: _SyncService) -> bool:
    """Start one daemon worker when the loopback API is configured."""
    global _PHASE3_WORKER
    if not _st_api.phase3_api_configured():
        return False
    with _PHASE3_WORKER_LOCK:
        if _PHASE3_WORKER is not None and _PHASE3_WORKER.is_alive():
            return True
        _PHASE3_STOP_EVENT.clear()
        _PHASE3_WORKER = threading.Thread(
            target=_phase3_worker_loop,
            args=(sync_service,),
            name="sillytavern-phase3-sync",
            daemon=True,
        )
        _PHASE3_WORKER.start()
        return True


def stop_phase3_sync_worker(timeout: float = 5.0) -> bool:
    """Stop the realtime sync worker and wait briefly for it to exit."""
    global _PHASE3_WORKER
    _PHASE3_STOP_EVENT.set()
    worker = _PHASE3_WORKER
    if worker is None:
        return True
    if worker is threading.current_thread():
        return False
    worker.join(timeout=max(0.0, float(timeout)))
    stopped = not worker.is_alive()
    if stopped:
        _PHASE3_WORKER = None
    return stopped


# Explicit late imports replace transitional dependency injection.
from bridge.card_content import card_fields_from_file
from bridge.common import chat_job_lock
from bridge.config import DEFAULT_MODEL
from bridge.database import (
    db_connect,
    run_write_txn,
    sync_transcript_hash,
)
from bridge.group_core import group_state
from bridge.sync_core import (
    apply_sync_snapshot,
    build_sync_records,
    set_sync_state,
    sync_binding,
    sync_file_id,
    sync_local_rows,
)
from bridge.telegram import load_session

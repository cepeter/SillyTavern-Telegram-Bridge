"""Optional near-real-time sync through SillyTavern's loopback HTTP API."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from functools import partial as _partial

import bridge.sillytavern_api as _st_api
from bridge.background import chat_job_lock
from bridge.card_content import card_fields_from_file
from bridge.database import sync_transcript_hash
from bridge.group_core import group_state
from bridge.port_contracts import RetainSessionMemory
from bridge.session_core import load_session
from bridge.settings import AppSettings
from bridge.sqlite_store import db_connect, run_write_txn
from bridge.sync_core import (
    apply_sync_snapshot,
    build_sync_records,
    set_sync_state,
    sync_binding,
    sync_file_id,
    sync_local_rows,
)
from bridge.sync_poll_safety import SyncPollSafetyAdapter as _SyncPollSafetyAdapter
from bridge.sync_service import SyncService as _SyncService

_LIVE_SYNC_WORKER_LOCK = threading.Lock()
_LIVE_SYNC_WORKER = None
_LIVE_SYNC_STOP_EVENT = threading.Event()
_LIVE_SYNC_STOP_RESULTS = {
    "sync ID mismatch; realtime stopped",
    "initial divergence; realtime stopped",
    "conflict detected; realtime stopped",
}
_LIVE_SYNC_MAX_MESSAGE_CHARS = 12000
_LIVE_SYNC_MAX_TOTAL_CHARS = 200000


def _live_sync_records(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
    binding: dict[str, object],
    rows: list[tuple],
    *,
    app_settings: AppSettings,
) -> list[dict]:
    return build_sync_records(db, chat_id, session, fields, str(binding["sync_id"]), rows, app_settings=app_settings)


def _live_sync_snapshot(records: list[dict]) -> tuple[dict, list[tuple[str, str]], dict[int, tuple[list[str], int]]]:
    metadata = records[0].get("chat_metadata", {}) if records and isinstance(records[0], dict) else {}
    messages = []
    total_chars = 0
    variants = {}
    message_index = 0
    for record in records[1:] if metadata else records:
        if not isinstance(record, dict) or record.get("is_system") or "mes" not in record:
            continue
        content = str(record.get("mes") or "").strip()
        if len(content) > _LIVE_SYNC_MAX_MESSAGE_CHARS:
            raise ValueError("SillyTavern API message exceeds the sync limit")
        if not content:
            continue
        total_chars += len(content)
        if total_chars > _LIVE_SYNC_MAX_TOTAL_CHARS:
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


def _live_sync_reset_failures(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    def write():
        db.execute(
            (
                "UPDATE sync_bindings SET "
                "realtime_failures=0,realtime_next_retry_at=0,last_error='' WHERE "
                "chat_id=? AND session_id=?"
            ),
            (chat_id, session_id),
        )
        db.commit()

    run_write_txn(db, write)


def _live_sync_disable(db: sqlite3.Connection, chat_id: str, session_id: str, error: str) -> None:
    def write():
        db.execute(
            (
                "UPDATE sync_bindings SET "
                "realtime_enabled=0,last_error=?,realtime_next_retry_at=0 WHERE chat_id=? "
                "AND session_id=?"
            ),
            (error[:1000], chat_id, session_id),
        )
        db.commit()

    run_write_txn(db, write)


def live_sync_now(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    app_settings: AppSettings,
    retain_memory: RetainSessionMemory,
) -> str:
    """Synchronize one binding through SillyTavern's supported chat API."""
    client = _st_api.live_sync_client(app_settings=app_settings)
    session = load_session(db, chat_id, session_id, app_settings.default_model, app_settings=app_settings)
    binding = sync_binding(db, chat_id, session_id)
    file_id = sync_file_id(binding)
    group = group_state(db, chat_id, session_id)
    is_group = bool(group.get("enabled"))
    rows = sync_local_rows(db, chat_id, session_id)
    local_hash = sync_transcript_hash([(role, content) for _rowid, role, content, _created_at in rows])
    remote_records = client.get_chat(session, file_id, is_group)
    fields = card_fields_from_file(session["character_file"], app_settings=app_settings)
    if not remote_records:
        client.save_chat(
            session,
            fields,
            file_id,
            is_group,
            _live_sync_records(db, chat_id, session, fields, binding, rows, app_settings=app_settings),
        )
        set_sync_state(db, chat_id, session_id, local_hash, "bridge_to_sillytavern_api")
        _live_sync_reset_failures(db, chat_id, session_id)
        return "created SillyTavern API chat"
    metadata, remote_messages, remote_variants = _live_sync_snapshot(remote_records)
    remote_sync = metadata.get("bridge_sync") if isinstance(metadata.get("bridge_sync"), dict) else {}
    if remote_sync.get("sync_id") and remote_sync.get("sync_id") != binding["sync_id"]:
        set_sync_state(
            db, chat_id, session_id, local_hash, "", "sync_id_mismatch", "API chat sync ID does not match this session"
        )
        _live_sync_disable(db, chat_id, session_id, "sync ID mismatch")
        return "sync ID mismatch; realtime stopped"
    remote_hash = sync_transcript_hash(remote_messages)
    baseline = str(binding.get("last_hash") or "")
    if remote_hash == local_hash:
        set_sync_state(db, chat_id, session_id, local_hash, str(binding.get("last_direction") or ""))
        _live_sync_reset_failures(db, chat_id, session_id)
        return "unchanged"
    if not baseline:
        set_sync_state(
            db, chat_id, session_id, local_hash, "", "initial_divergence", "API chat has no common checkpoint"
        )
        _live_sync_disable(db, chat_id, session_id, "initial divergence")
        return "initial divergence; realtime stopped"
    if local_hash == baseline and remote_hash != baseline:
        imported_hash = apply_sync_snapshot(
            db,
            chat_id,
            session,
            metadata,
            remote_messages,
            remote_variants,
            app_settings=app_settings,
            retain_memory=retain_memory,
        )
        set_sync_state(db, chat_id, session_id, imported_hash, "sillytavern_api_to_bridge")
        _live_sync_reset_failures(db, chat_id, session_id)
        return "imported SillyTavern API changes"
    if remote_hash == baseline and local_hash != baseline:
        client.save_chat(
            session,
            fields,
            file_id,
            is_group,
            _live_sync_records(db, chat_id, session, fields, binding, rows, app_settings=app_settings),
        )
        set_sync_state(db, chat_id, session_id, local_hash, "bridge_to_sillytavern_api")
        _live_sync_reset_failures(db, chat_id, session_id)
        return "exported bridge changes through API"
    set_sync_state(db, chat_id, session_id, local_hash, "", "conflict", "both sides changed since the last checkpoint")
    _live_sync_disable(db, chat_id, session_id, "conflict detected")
    return "conflict detected; realtime stopped"


def live_sync_toggle_realtime(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    *,
    app_settings: AppSettings,
    retain_memory: RetainSessionMemory,
) -> str:
    binding = sync_binding(db, chat_id, session_id)
    if binding.get("realtime_enabled"):
        _live_sync_disable(db, chat_id, session_id, "")
        return "realtime API sync disabled"
    if not _st_api.live_sync_api_configured(app_settings=app_settings):
        return "realtime API sync is not configured"
    try:
        result = live_sync_now(db, chat_id, session_id, app_settings=app_settings, retain_memory=retain_memory)
    except (_st_api.SillyTavernApiError, ValueError) as exc:
        _live_sync_disable(db, chat_id, session_id, str(exc))
        return f"realtime API unavailable: {exc}"
    if result in _LIVE_SYNC_STOP_RESULTS:
        return result

    def mark_enabled():
        db.execute(
            (
                "UPDATE sync_bindings SET "
                "realtime_enabled=1,realtime_failures=0,realtime_next_retry_at=0,last_err"
                "or='' WHERE chat_id=? AND session_id=?"
            ),
            (chat_id, session_id),
        )
        db.commit()

    run_write_txn(db, mark_enabled)
    return "realtime API sync enabled; " + result


def live_sync_status_line(db: sqlite3.Connection, chat_id: str, session_id: str, *, app_settings: AppSettings) -> str:
    binding = sync_binding(db, chat_id, session_id)
    enabled = "on" if binding.get("realtime_enabled") else "off"
    configured = "configured" if _st_api.live_sync_api_configured(app_settings=app_settings) else "not configured"
    return f"Live API sync: {enabled} ({configured})"


def _make_sync_poll_safety(*, app_settings: AppSettings, retain_memory: RetainSessionMemory):
    return _SyncPollSafetyAdapter(
        sync_now=(
            lambda db, chat_id, session_id: live_sync_now(
                db, chat_id, session_id, app_settings=app_settings, retain_memory=retain_memory
            )
        ),
        chat_lock=(lambda chat_id: chat_job_lock(chat_id)),
        disable_realtime=(
            lambda db, chat_id, session_id, error: _live_sync_disable(
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
        sync_interval=(lambda: app_settings.live_sync_interval_seconds),
        now=(lambda: time.time()),
        log_warning=(
            lambda message, *args, **kwargs: logging.warning(
                message,
                *args,
                **kwargs,
            )
        ),
    )


def live_sync_poll(db: sqlite3.Connection, *, app_settings: AppSettings, retain_memory: RetainSessionMemory) -> None:
    _make_sync_poll_safety(app_settings=app_settings, retain_memory=retain_memory).poll(db)


def _live_sync_worker_loop(sync_service: _SyncService, *, app_settings: AppSettings) -> None:
    db = None
    try:
        while not _LIVE_SYNC_STOP_EVENT.wait(app_settings.live_sync_interval_seconds):
            try:
                if db is None:
                    db = db_connect(app_settings=app_settings)
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
                logging.warning("Live Sync worker failed", exc_info=True)
    finally:
        if db is not None:
            db.close()


def start_live_sync_worker(*, sync_service: _SyncService, app_settings: AppSettings) -> bool:
    """Start one daemon worker when the loopback API is configured."""
    global _LIVE_SYNC_WORKER
    if not _st_api.live_sync_api_configured(app_settings=app_settings):
        return False
    with _LIVE_SYNC_WORKER_LOCK:
        if _LIVE_SYNC_WORKER is not None and _LIVE_SYNC_WORKER.is_alive():
            return True
        _LIVE_SYNC_STOP_EVENT.clear()
        _LIVE_SYNC_WORKER = threading.Thread(
            target=_partial(_live_sync_worker_loop, app_settings=app_settings),
            args=(sync_service,),
            name="sillytavern-live-sync",
            daemon=True,
        )
        _LIVE_SYNC_WORKER.start()
        return True


def stop_live_sync_worker(timeout: float = 5.0) -> bool:
    """Stop the realtime sync worker and wait briefly for it to exit."""
    global _LIVE_SYNC_WORKER
    _LIVE_SYNC_STOP_EVENT.set()
    worker = _LIVE_SYNC_WORKER
    if worker is None:
        return True
    if worker is threading.current_thread():
        return False
    worker.join(timeout=max(0.0, float(timeout)))
    stopped = not worker.is_alive()
    if stopped:
        _LIVE_SYNC_WORKER = None
    return stopped

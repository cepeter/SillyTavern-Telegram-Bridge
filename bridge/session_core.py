"""Session lifecycle and recovery policy, independent of Telegram delivery."""

from __future__ import annotations

import json
import logging
import sqlite3
import time

from bridge.card_content import active_world_files, encode_world_files, safe_world_path
from bridge.expressions import expression_last_key, expression_mode_key
from bridge.generation_settings import get_generation_settings
from bridge.memory_backend import hindsight_session_lock
from bridge.memory_service import MemoryService
from bridge.meta_repository import store_meta_value
from bridge.metadata import get_meta
from bridge.operation_repository import claim_operation, mark_operation_applied
from bridge.persona_sync import default_persona_id, get_persona
from bridge.response_variants import swipe_state_key
from bridge.session_repository import (
    SESSION_COLUMNS,
    delete_session_rows,
    insert_session_row,
    list_session_rows,
    load_session_row,
    session_has_active_jobs,
    update_session_row,
)
from bridge.session_titles import normalize_session_title
from bridge.settings import AppSettings
from bridge.sqlite_store import optimize_database, write_transaction


def _default_session_persona(db: sqlite3.Connection, *, app_settings: AppSettings) -> str:
    return default_persona_id(app_settings=app_settings)


def _native_default_world(*, app_settings: AppSettings) -> str:
    try:
        settings = json.loads(app_settings.native_persona_settings_file.read_text(encoding="utf-8"))
        selected = ((settings.get("world_info_settings") or {}).get("world_info") or {}).get("globalSelect") or []
        if isinstance(selected, list):
            return encode_world_files([str(name) for name in selected])
    except (OSError, ValueError, TypeError, AttributeError):
        logging.warning("Could not read native SillyTavern World Info defaults", exc_info=True)
    return ""


def _default_session_world(db: sqlite3.Connection, *, app_settings: AppSettings) -> str:
    return _native_default_world(app_settings=app_settings)


def _normalize_session_defaults(
    db: sqlite3.Connection, session: dict[str, str], *, app_settings: AppSettings
) -> dict[str, str]:
    persona_id = session.get("persona_id") or ""
    world_file = session.get("world_file") or ""
    replacement_persona = (
        persona_id
        if not persona_id or get_persona(persona_id, app_settings=app_settings)
        else _default_session_persona(db, app_settings=app_settings)
    )
    replacement_world = (
        world_file
        if not world_file
        or any(
            safe_world_path(name, app_settings=app_settings)
            for name in active_world_files(world_file, app_settings=app_settings)
        )
        else _default_session_world(db, app_settings=app_settings)
    )
    changes = {}
    if replacement_persona != persona_id:
        changes["persona_id"] = replacement_persona
    if replacement_world != world_file:
        changes["world_file"] = replacement_world
    if changes:
        with write_transaction(db):
            update_session_row(db, session["chat_id"], session["session_id"], changes, time.time())
        session.update(changes)
    return session


def _new_session_values(
    db: sqlite3.Connection, chat_id: str, session_id: str, title: str, default_model: str, *, app_settings: AppSettings
) -> dict[str, str]:
    return dict(
        zip(
            SESSION_COLUMNS,
            (
                chat_id,
                session_id,
                title,
                app_settings.default_character_file,
                get_meta(db, "model", default_model),
                _default_session_persona(db, app_settings=app_settings),
                _default_session_world(db, app_settings=app_settings),
                "",
                "",
                "auto",
                "off",
            ),
            strict=True,
        )
    )


def load_session(
    db: sqlite3.Connection, chat_id: str, session_id: str, default_model: str, *, app_settings: AppSettings
) -> dict[str, str]:
    session = load_session_row(db, chat_id, session_id)
    if session is None:
        raise ValueError(f"queued session no longer exists: {session_id}")
    get_generation_settings(db, chat_id, session_id)
    return _normalize_session_defaults(db, session, app_settings=app_settings)


def ensure_session(
    db: sqlite3.Connection, chat_id: str, default_model: str, *, app_settings: AppSettings
) -> dict[str, str]:
    active_id = get_meta(db, f"active_session:{chat_id}", "default")
    session = load_session_row(db, chat_id, active_id)
    if session is None:
        values = _new_session_values(
            db, chat_id, active_id, "Default session", default_model, app_settings=app_settings
        )
        with write_transaction(db):
            insert_session_row(db, values, time.time())
        session = load_session_row(db, chat_id, active_id) or values
    get_generation_settings(db, chat_id, active_id)
    return _normalize_session_defaults(db, session, app_settings=app_settings)


def update_session(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    operation_id: int | str | None = None,
    operation_kind: str = "session_update",
    **values: object,
) -> None:
    allowed = set(SESSION_COLUMNS) - {"chat_id", "session_id"}
    values = {key: value for key, value in values.items() if key in allowed}
    if not values:
        return
    with write_transaction(db):
        if not claim_operation(db, operation_id, operation_kind, time.time()):
            return
        update_session_row(db, chat_id, session_id, values, time.time())
        mark_operation_applied(db, operation_id, operation_kind, time.time())


def create_session(
    db: sqlite3.Connection,
    chat_id: str,
    default_model: str,
    session_id: str | None = None,
    title: str = "New session",
    *,
    app_settings: AppSettings,
) -> dict[str, str]:
    session_id = session_id or ("s" + str(int(time.time() * 1000)))
    values = _new_session_values(
        db, chat_id, session_id, normalize_session_title(title), default_model, app_settings=app_settings
    )
    with write_transaction(db):
        insert_session_row(db, values, time.time())
        store_meta_value(db, f"active_session:{chat_id}", session_id)
    return load_session_row(db, chat_id, session_id) or values


def list_sessions(db: sqlite3.Connection, chat_id: str) -> list[dict[str, str]]:
    return list_session_rows(db, chat_id)


def delete_session_data(
    db: sqlite3.Connection,
    chat_id: str,
    target_session_id: str,
    active_session_id: str,
    operation_id: int | str | None = None,
    *,
    memory_service: MemoryService,
) -> tuple[bool, str]:
    if target_session_id == active_session_id:
        return False, "active session"
    if load_session_row(db, chat_id, target_session_id) is None:
        return False, "session not found"
    if session_has_active_jobs(db, chat_id, target_session_id):
        return False, "session has active jobs"
    with hindsight_session_lock(chat_id, target_session_id):
        if load_session_row(db, chat_id, target_session_id) is None:
            return False, "session not found"
        if session_has_active_jobs(db, chat_id, target_session_id):
            return False, "session has active jobs"
        try:
            memory_service.purge_session(db, chat_id, target_session_id)
        except RuntimeError:
            return False, "Hindsight cleanup failed; session was preserved"
        with write_transaction(db):
            if not claim_operation(db, operation_id, "session_delete", time.time()):
                return False, "already processed"
            delete_session_rows(
                db,
                chat_id,
                target_session_id,
                (
                    swipe_state_key(chat_id, target_session_id),
                    f"swipe_message:{chat_id}:{target_session_id}",
                    expression_mode_key(chat_id, target_session_id),
                    expression_last_key(chat_id, target_session_id),
                ),
            )
            mark_operation_applied(db, operation_id, "session_delete", time.time())
        optimize_database(db)
    return True, "deleted"

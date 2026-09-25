"""Shared transcript and checkpoint primitives for Live API Sync."""

from __future__ import annotations

import logging
import sqlite3
import time
from functools import partial as _partial
from pathlib import Path

from bridge.card_content import (
    active_world_files,
    card_fields_from_file,
    encode_world_files,
    safe_character_path,
    safe_world_path,
)
from bridge.config import GENERATION_DEFAULTS
from bridge.generation import save_response_variant
from bridge.generation_settings import get_generation_settings, update_generation_settings
from bridge.generation_settings_values import parse_generation_setting
from bridge.language import normalize_response_language
from bridge.limits import SYNC_MAX_BYTES
from bridge.memory import get_session_summary
from bridge.persona_sync import get_persona, persona_name
from bridge.port_contracts import RetainSessionMemory
from bridge.session_core import load_session, update_session
from bridge.settings import AppSettings
from bridge.sync_integrity import SyncSnapshotIntegrityAdapter as _SyncSnapshotIntegrityAdapter
from bridge.sync_state import ensure_sync_binding, sync_transcript_hash

SYNC_MAX_PAYLOAD_BYTES = SYNC_MAX_BYTES


def sync_binding(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, object]:
    """Return the stable binding and Live API state for one bridge session."""
    base = ensure_sync_binding(db, chat_id, session_id)
    row = db.execute(
        "SELECT conflict,last_error,last_checked_at,realtime_enabled,"
        "realtime_failures,realtime_next_retry_at FROM sync_bindings "
        "WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ).fetchone()
    if row is None:
        raise ValueError("sync binding could not be created")
    base.update(
        dict(
            zip(
                (
                    "conflict",
                    "last_error",
                    "last_checked_at",
                    "realtime_enabled",
                    "realtime_failures",
                    "realtime_next_retry_at",
                ),
                row,
                strict=False,
            )
        )
    )
    return base


def sync_file_id(binding: dict[str, object]) -> str:
    """Build a stable SillyTavern API chat id without a filesystem path."""
    return "bridge-" + str(binding["sync_id"])


def sync_local_rows(db: sqlite3.Connection, chat_id: str, session_id: str) -> list[tuple[int, str, str, float]]:
    """Load the complete ordered bridge transcript."""
    return db.execute(
        "SELECT rowid,role,content,created_at FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid",
        (chat_id, session_id),
    ).fetchall()


def _sync_swipe_records(db: sqlite3.Connection, chat_id: str, session_id: str, user_rowid: int) -> list[str]:
    rows = db.execute(
        "SELECT response FROM response_variants WHERE chat_id=? AND session_id=? "
        "AND user_rowid=? ORDER BY variant_index",
        (chat_id, session_id, user_rowid),
    ).fetchall()
    return [str(row[0]) for row in rows if str(row[0] or "")]


def build_sync_records(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
    sync_id: str,
    rows: list[tuple[int, str, str, float]],
    *,
    app_settings: AppSettings,
) -> list[dict]:
    """Build API chat records with compatible SillyTavern swipes."""
    user_name = (
        persona_name(session["persona_id"], app_settings=app_settings)
        if session["persona_id"]
        else app_settings.default_user_name
    )
    transcript_hash = sync_transcript_hash([(role, content) for _rowid, role, content, _created_at in rows])
    header = {
        "chat_metadata": {
            "name": session["title"],
            "session_id": session["session_id"],
            "character": fields["name"],
            "character_file": session["character_file"],
            "model": session["model_id"],
            "response_language": session.get("response_language") or "auto",
            "persona": session["persona_id"],
            "world_info": active_world_files(session["world_file"], app_settings=app_settings),
            "author_note": session["author_note"],
            "generation_settings": get_generation_settings(db, chat_id, session["session_id"]),
            "session_summary": get_session_summary(db, chat_id, session["session_id"])[0],
            "bridge_sync": {
                "version": 3,
                "transport": "api",
                "sync_id": sync_id,
                "transcript_hash": transcript_hash,
            },
        },
        "user_name": user_name,
        "character_name": fields["name"],
    }
    records = [header]
    previous_user_rowid = None
    for rowid, role, content, created_at in rows:
        record = {
            "name": user_name if role == "user" else fields["name"],
            "is_user": role == "user",
            "is_system": False,
            "send_date": time.strftime("%Y-%m-%d @ %H:%M:%S", time.localtime(created_at)),
            "mes": content,
            "extra": {},
        }
        if role == "user":
            previous_user_rowid = rowid
        elif previous_user_rowid is not None:
            variants = _sync_swipe_records(db, chat_id, session["session_id"], previous_user_rowid)
            if len(variants) > 1:
                selected = variants.index(content) if content in variants else len(variants)
                if content not in variants:
                    variants.append(content)
                record["swipes"], record["swipe_id"] = variants, selected
        records.append(record)
    return records


def _apply_sync_snapshot_backend(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    metadata: dict,
    messages: list[tuple[str, str]],
    variants: dict[int, tuple[list[str], int]],
    *,
    app_settings: AppSettings,
) -> str:
    """Replace one bridge transcript from a validated Live API snapshot."""
    values = {"title": str(metadata.get("name") or session["title"])[:120]}
    character_file = str(metadata.get("character_file") or "")
    if character_file and safe_character_path(character_file, app_settings=app_settings):
        values["character_file"] = Path(character_file).name
    if metadata.get("model"):
        values["model_id"] = str(metadata["model"])[:200]
    persona_id = str(metadata.get("persona") or "")
    if persona_id and get_persona(persona_id, app_settings=app_settings):
        values["persona_id"] = persona_id
    raw_worlds = metadata.get("world_info")
    candidates = raw_worlds if isinstance(raw_worlds, list) else ([raw_worlds] if raw_worlds else [])
    valid_worlds = [
        Path(str(name)).name for name in candidates if safe_world_path(str(name), app_settings=app_settings)
    ]
    if valid_worlds:
        values["world_file"] = encode_world_files(valid_worlds)
    values["author_note"] = str(metadata.get("author_note") or "")[:2000]
    values["system_prompt"] = str(metadata.get("system_prompt") or "")[:8000]
    try:
        values["response_language"] = normalize_response_language(str(metadata.get("response_language") or "auto"))
    except ValueError:
        pass
    update_session(db, chat_id, session["session_id"], **values)
    imported_settings = metadata.get("generation_settings")
    if isinstance(imported_settings, dict):
        normalized = {}
        for key, raw_value in imported_settings.items():
            if key in GENERATION_DEFAULTS:
                try:
                    normalized[key] = parse_generation_setting(key, str(raw_value))[1]
                except ValueError:
                    pass
        if normalized:
            update_generation_settings(db, chat_id, session["session_id"], **normalized)
    db.execute("DELETE FROM response_variants WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"]))
    db.execute("DELETE FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"]))
    user_rowids = {}
    for index, (role, content) in enumerate(messages):
        cursor = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (chat_id, session["session_id"], role, content, time.time() + index * 0.001),
        )
        if role == "user":
            user_rowids[index] = (int(cursor.lastrowid), content)
    for index, (swipes, selected) in variants.items():
        user_index = next((item for item in range(index - 1, -1, -1) if item in user_rowids), None)
        if user_index is None:
            continue
        user_rowid, user_content = user_rowids[user_index]
        ordered = [value for pos, value in enumerate(swipes) if pos != selected] + [swipes[selected]]
        for response in ordered:
            save_response_variant(
                db,
                chat_id,
                session["session_id"],
                user_content,
                response,
                user_rowid=user_rowid,
                commit=False,
            )
    db.commit()
    return sync_transcript_hash(messages)


def _make_sync_snapshot_integrity(*, app_settings: AppSettings, retain_memory: RetainSessionMemory):
    return _SyncSnapshotIntegrityAdapter(
        apply_backend=_partial(_apply_sync_snapshot_backend, app_settings=app_settings),
        update_session=(
            lambda db, chat_id, session_id, **updates: update_session(
                db,
                chat_id,
                session_id,
                **updates,
            )
        ),
        load_session=(
            lambda db, chat_id, session_id, default_model: load_session(
                db, chat_id, session_id, default_model, app_settings=app_settings
            )
        ),
        retain_memory=retain_memory,
        card_fields=(lambda character_file: card_fields_from_file(character_file, app_settings=app_settings)),
        default_model=app_settings.default_model,
        log_warning=(lambda message, **kwargs: logging.warning(message, **kwargs)),
    )


def apply_sync_snapshot(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    metadata: dict,
    messages: list[tuple[str, str]],
    variants: dict[int, tuple[list[str], int]],
    *,
    app_settings: AppSettings,
    retain_memory: RetainSessionMemory,
) -> str:
    return _make_sync_snapshot_integrity(app_settings=app_settings, retain_memory=retain_memory).apply(
        db,
        chat_id,
        session,
        metadata,
        messages,
        variants,
    )


def set_sync_state(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    local_hash: str,
    direction: str,
    conflict: str = "",
    error: str = "",
) -> None:
    """Persist a Live API checkpoint or stopped conflict state."""
    ensure_sync_binding(db, chat_id, session_id)
    db.execute(
        "UPDATE sync_bindings SET last_hash=?,last_direction=?,last_synced_at=?,"
        "conflict=?,last_error=?,last_checked_at=? WHERE chat_id=? AND session_id=?",
        (local_hash, direction, time.time(), conflict, error[:1000], time.time(), chat_id, session_id),
    )
    db.commit()

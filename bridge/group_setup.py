"""Canonical group setup owner."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from bridge.callbacks import close_panel_message, discard_panel_binding
from bridge.group_service import GroupService
from bridge.limits import PENDING_SETTINGS_TTL_SECONDS
from bridge.metadata import set_meta
from bridge.session_core import create_session, load_session, update_session
from bridge.settings import AppSettings
from bridge.world_panels import send_world_menu


def start_group_session(
    db: sqlite3.Connection,
    chat_id: str,
    default_model: str,
    title: str = "New group session",
    session_id: str | None = None,
    *,
    group_service: GroupService,
    app_settings: AppSettings,
) -> dict[str, str]:
    """Create a clean topic-local session for the New group session wizard."""
    session_id = session_id or f"group-{time.time_ns()}"
    create_session(db, chat_id, default_model, session_id=session_id, title=title, app_settings=app_settings)
    update_session(db, chat_id, session_id, character_file=app_settings.default_character_file, world_file="")
    group_service.save(
        db,
        chat_id,
        session_id,
        {
            "title": title,
            "enabled": False,
            "turn_index": 0,
            "mode": "round_robin",
            "forced_speaker": "",
            "members": [],
            "turn_user_id": "",
            "turn_users": [],
        },
    )
    set_meta(
        db,
        f"group_setup:{chat_id}",
        json.dumps(
            {"session_id": session_id, "stage": "character", "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
        ),
    )
    return load_session(db, chat_id, session_id, default_model, app_settings=app_settings)


def apply_group_setup_character(
    db: sqlite3.Connection,
    token: str,
    callback: dict,
    answer_callback,
    chat_id: str,
    message: dict,
    session_id: str,
    operation_id: int | str | None,
    filename: str,
    character_name: str,
    *,
    group_service: GroupService,
    request_context,
) -> bool:
    """Apply the wizard character and open the topic-local World Info picker."""
    update_session(
        db,
        chat_id,
        session_id,
        operation_id=operation_id,
        operation_kind="group_setup_character",
        character_file=Path(filename).name,
    )
    setup = group_service.setup_state(db, chat_id, session_id)
    setup.update({"stage": "world", "character_file": Path(filename).name, "character_name": character_name})
    set_meta(db, f"group_setup:{chat_id}", json.dumps(setup))
    answer_callback(token, str(callback.get("id", "")), "Choose World Info")
    discard_panel_binding(db, chat_id, message.get("message_id"))
    close_panel_message(db, token, chat_id, callback)
    send_world_menu(token, chat_id, "", None, 0, request_context=request_context)
    return True

"""Canonical preset actions owner."""

from __future__ import annotations

import re
import sqlite3

from bridge.generation_settings import (
    delete_generation_preset,
    get_generation_settings,
    load_generation_preset,
    update_generation_settings,
)
from bridge.generation_settings_values import format_generation_settings
from bridge.telegram import send_text


def apply_preset_action(
    db: sqlite3.Connection, token: str, chat_id: str, session_id: str, action: str, name: str
) -> None:
    name = str(name).strip()
    if action not in {"use", "delete"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        send_text(token, chat_id, "Invalid preset panel action.")
        return
    if action == "use":
        settings = load_generation_preset(db, chat_id, name)
        if not settings:
            send_text(token, chat_id, f"Preset not found: {name}")
            return
        update_generation_settings(db, chat_id, session_id, **settings)
        send_text(
            token,
            chat_id,
            (
                "Preset applied to this session: "
                f"""{name}"""
                "\n"
                f"""{format_generation_settings(get_generation_settings(db, chat_id, session_id))}"""
            ),
        )
        return
    send_text(
        token,
        chat_id,
        f"Preset deleted: {name}" if delete_generation_preset(db, chat_id, name) else f"Preset not found: {name}",
    )

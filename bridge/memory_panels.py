"""Canonical memory panels owner."""

from __future__ import annotations

import sqlite3

from bridge.cards import send_panel_message
from bridge.memory_backend import memory_mode


def send_memory_menu(
    token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None, *, request_context
) -> None:
    mode = memory_mode(db, chat_id)
    rows = [
        [
            {"text": ("✅ " if mode == "on" else "") + "Memory on", "callback_data": "enum:memory:on"},
            {"text": ("✅ " if mode == "off" else "") + "Memory off", "callback_data": "enum:memory:off"},
        ],
        [{"text": "🔎 Search memories", "callback_data": "enum:memory:search"}],
        [{"text": "❌ Close", "callback_data": "enum:close"}],
    ]
    send_panel_message(
        token,
        chat_id,
        f"Hindsight memory: {mode}\nScope: active session only (fixed)",
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )

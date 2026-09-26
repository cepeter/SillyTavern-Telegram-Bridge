"""Dedicated mode control, without adding Light Novel settings to /settings."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from bridge.conversation_lifecycle import configure_conversation, conversation_state, is_group_conversation
from bridge.light_novel_panels import send_light_novel_menu
from bridge.metadata import get_meta
from bridge.request_types import RequestContext
from bridge.telegram import send_text


def handle_light_novel_mode_callback(
    db: sqlite3.Connection,
    token: str,
    callback: dict,
    answer_callback: Callable,
    data: str,
    chat_id: str,
    session: dict,
    *,
    request_context: RequestContext,
) -> bool:
    if not data.startswith("novelmode:"):
        return False
    try:
        parts = data.split(":")
        if len(parts) != 3 or is_group_conversation(db, chat_id, session["session_id"]):
            raise ValueError("Light Novel mode is available for standard sessions only.")
        state = conversation_state(db, chat_id, session["session_id"])
        if (
            get_meta(db, f"active_session:{chat_id}", "default") != session["session_id"]
            or int(parts[1]) != state.epoch
        ):
            raise ValueError("Mode panel expired; use /lightnovel again.")
        configure_conversation(
            db, chat_id, session["session_id"], "normal" if parts[2] == "normal" else "lightnovel", parts[2]
        )
        answer_callback(token, str(callback.get("id") or ""), "Mode updated")
        send_light_novel_menu(token, chat_id, session, request_context=request_context)
    except (ValueError, TypeError) as exc:
        send_text(token, chat_id, str(exc))
    return True

"""Canonical pending input owner."""

from __future__ import annotations

import json
import time

from bridge.card_content import safe_character_path
from bridge.metadata import get_meta, set_meta
from bridge.settings import AppSettings
from bridge.telegram import delete_pending_input_prompts


def _decode_pending_state(raw: str, meta_key: str) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def _pending_state(db, meta_key: str, session_id: str, token: str, chat_id: str) -> dict:
    raw = get_meta(db, meta_key, "")
    state = _decode_pending_state(raw, meta_key)
    if state and (
        state.get("session_id") not in {None, "", session_id} or float(state.get("expires_at", 0) or 0) < time.time()
    ):
        delete_pending_input_prompts(token, chat_id, state)
        set_meta(db, meta_key, "")
        return {}
    return state


def _cancel_pending(db, token: str, chat_id: str, meta_key: str, state: dict) -> None:
    delete_pending_input_prompts(token, chat_id, state)
    set_meta(db, meta_key, "")


def pending_character_for_session(db, chat_id: str, *, app_settings: AppSettings) -> dict | None:
    """Load and validate a character waiting for session assignment."""
    meta_key = f"character_session_input:{chat_id}"
    state = _decode_pending_state(get_meta(db, meta_key, ""), meta_key)
    filename = str(state.get("character_file") or "")
    if (
        not state
        or float(state.get("expires_at", 0) or 0) < time.time()
        or not safe_character_path(filename, app_settings=app_settings)
    ):
        if state:
            set_meta(db, meta_key, "")
        return None
    return state

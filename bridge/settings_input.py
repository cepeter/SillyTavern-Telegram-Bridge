"""Canonical settings input owner."""

from __future__ import annotations

import re

from bridge.generation_settings import get_generation_settings, save_generation_preset, update_generation_settings
from bridge.generation_settings_values import parse_generation_setting
from bridge.language import normalize_stt_language, stt_language_label
from bridge.message_commands import send_pending_input_message
from bridge.metadata import set_meta
from bridge.note_panels import send_note_menu
from bridge.pending_input import _cancel_pending
from bridge.preset_panels import send_preset_menu
from bridge.session_core import update_session
from bridge.settings_panels import send_settings_menu
from bridge.telegram import send_text
from bridge.voice_panels import send_stt_language_menu, send_voice_input_menu


def _handle_settings_input(
    db, token: str, chat_id: str, session_id: str, stripped: str, state: dict, *, request_context
) -> bool:
    if stripped.casefold() in {"/cancel", "cancel"}:
        _cancel_pending(db, token, chat_id, f"settings_input:{chat_id}", state)
        send_settings_menu(token, chat_id, db, session_id, request_context=request_context)
        return True
    try:
        key, value = parse_generation_setting(str(state.get("key") or ""), stripped)
        update_generation_settings(db, chat_id, session_id, **{key: value})
    except ValueError as exc:
        send_pending_input_message(
            db,
            token,
            chat_id,
            f"settings_input:{chat_id}",
            state,
            f"Invalid {state.get('key')}: {exc} Try again or send /cancel.",
        )
        return True
    _cancel_pending(db, token, chat_id, f"settings_input:{chat_id}", state)
    send_settings_menu(token, chat_id, db, session_id, request_context=request_context)
    return True


def _handle_preset_input(
    db, token: str, chat_id: str, session_id: str, stripped: str, state: dict, *, request_context
) -> bool:
    meta_key = f"preset_save_input:{chat_id}"
    if stripped.casefold() in {"/cancel", "cancel"}:
        _cancel_pending(db, token, chat_id, meta_key, state)
        send_preset_menu(token, chat_id, db, request_context=request_context)
        return True
    preset_name = stripped.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", preset_name):
        send_pending_input_message(
            db,
            token,
            chat_id,
            meta_key,
            state,
            "Preset name must use 1–64 letters, numbers, hyphens, or underscores. Try again or send /cancel.",
        )
        return True
    save_generation_preset(db, chat_id, preset_name, get_generation_settings(db, chat_id, session_id))
    _cancel_pending(db, token, chat_id, meta_key, state)
    send_text(token, chat_id, f"Preset saved: {preset_name}")
    send_preset_menu(token, chat_id, db, request_context=request_context)
    return True


def _handle_stt_input(db, token: str, chat_id: str, stripped: str, state: dict, *, request_context) -> bool:
    meta_key = f"stt_language_input:{chat_id}"
    if stripped.casefold() in {"/cancel", "cancel"}:
        _cancel_pending(db, token, chat_id, meta_key, state)
        send_stt_language_menu(token, chat_id, db, request_context=request_context)
        return True
    try:
        language = normalize_stt_language(stripped)
    except ValueError as exc:
        send_pending_input_message(
            db, token, chat_id, meta_key, state, f"Invalid STT language: {exc} Try again or send /cancel."
        )
        return True
    _cancel_pending(db, token, chat_id, meta_key, state)
    set_meta(db, f"stt_language:{chat_id}", language)
    send_text(token, chat_id, f"STT language set to {stt_language_label(language)}.")
    send_voice_input_menu(token, chat_id, db, request_context=request_context)
    return True


def _handle_note_input(
    db,
    token: str,
    chat_id: str,
    session: dict,
    stripped: str,
    state: dict,
    operation_id: int | None,
    *,
    request_context,
) -> bool:
    meta_key = f"note_input:{chat_id}"
    if stripped.casefold() in {"/cancel", "cancel"}:
        _cancel_pending(db, token, chat_id, meta_key, state)
        send_note_menu(token, chat_id, session.get("author_note") or "", request_context=request_context)
        return True
    note = stripped
    if not note or len(note) > 2000:
        send_pending_input_message(
            db,
            token,
            chat_id,
            meta_key,
            state,
            "Author's Note must contain 1–2,000 characters. Try again or send /cancel.",
        )
        return True
    update_session(
        db,
        chat_id,
        session["session_id"],
        author_note=note,
        operation_id=operation_id,
        operation_kind="author_note_input",
    )
    _cancel_pending(db, token, chat_id, meta_key, state)
    send_text(token, chat_id, "Author's Note updated for this session.")
    send_note_menu(token, chat_id, note, request_context=request_context)
    return True

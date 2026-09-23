"""Scoped session-name input flow shared by all new-session entry points."""
from __future__ import annotations

import json
from pathlib import Path
import time


from bridge import session_titles as _session_titles
from bridge.group_service import GroupService

_SESSION_PENDING_PREFIXES = (
    "settings_input", "preset_save_input", "stt_language_input", "persona_input", "note_input",
)


def _is_cancel_input(value: str) -> bool:
    text = " ".join(str(value or "").split()).casefold()
    if text in {"/cancel", "cancel"}:
        return True
    if " " not in text and text.startswith("/cancel@"):
        return bool(text.split("@", 1)[1])
    return False


def _clear_conflicting_inputs(db, token: str, chat_id: str) -> None:
    for prefix in _SESSION_PENDING_PREFIXES:
        key = f"{prefix}:{chat_id}"
        raw = get_meta(db, key, "")
        try:
            state = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            state = {}
        if state:
            delete_pending_input_prompts(token, chat_id, state)
        set_meta(db, key, "")


def start_session_name_input(db, token: str, chat_id: str, session: dict[str, str], kind: str = "standard", message: dict | None = None, *, group_service: GroupService) -> None:
    """Close the old panel and request a scoped name without creating a session."""
    meta_key = f"session_name_input:{chat_id}"
    old_raw = get_meta(db, meta_key, "")
    try:
        old_state = json.loads(old_raw) if old_raw else {}
    except json.JSONDecodeError:
        old_state = {}
    if old_state:
        delete_pending_input_prompts(token, chat_id, old_state)
    _clear_conflicting_inputs(db, token, chat_id)
    if message:
        discard_panel_binding(db, chat_id, message.get("message_id"))
        close_panel_message(db, token, chat_id, {"message": message})
    state = {
        "session_id": session["session_id"],
        "kind": "group" if kind == "group" else "standard",
        "model_id": session.get("model_id") or DEFAULT_MODEL,
        "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS,
    }
    prompt = "Send a name for the new group session" if state["kind"] == "group" else "Send a name for the new session"
    state["prompt_message_ids"] = send_text(token, chat_id, f"{prompt} (1–{_session_titles.SESSION_TITLE_MAX_CHARS} characters). Send /cancel to cancel.")
    set_meta(db, meta_key, json.dumps(state, ensure_ascii=False))


def _cancel_session_name_input(db, token: str, chat_id: str, state: dict) -> None:
    _cancel_pending(db, token, chat_id, f"session_name_input:{chat_id}", state)
    set_meta(db, f"character_session_input:{chat_id}", "")


def handle_session_name_input(db, token: str, chat_id: str, session: dict[str, str], stripped: str, state: dict, operation_id: int | None, *, group_service: GroupService, request_context) -> bool:
    """Validate the title, then atomically create and activate the requested session."""
    meta_key = f"session_name_input:{chat_id}"
    if _is_cancel_input(stripped):
        _cancel_session_name_input(db, token, chat_id, state)
        send_text(token, chat_id, "New session cancelled.")
        return True
    try:
        title = _session_titles.normalize_session_title(stripped)
    except ValueError as exc:
        send_pending_input_message(db, token, chat_id, meta_key, state, f"{exc} Try again or send /cancel.")
        return True
    model_id = str(state.get("model_id") or session.get("model_id") or DEFAULT_MODEL)
    kind = str(state.get("kind") or "standard")
    if kind == "group":
        new_id = f"group-{operation_id}" if operation_id is not None else f"group-{time.time_ns()}"
        new_session = start_group_session(db, chat_id, model_id, title=title, session_id=new_id, group_service=group_service)
        _cancel_pending(db, token, chat_id, meta_key, state)
        new_request_context = RequestContext(
            db,
            new_session["session_id"],
            request_context.actor_id,
        )
        send_text(token, chat_id, f"New group session started: {title}")
        send_character_menu(token, chat_id, new_session["character_file"], request_context=new_request_context)
        return True
    new_id = f"job-{operation_id}" if operation_id is not None else None
    pending_character = pending_character_for_session(db, chat_id)
    new_session = create_session(db, chat_id, model_id, session_id=new_id, title=title)
    if pending_character:
        update_session(db, chat_id, new_session["session_id"], operation_id=operation_id, operation_kind="character_select", character_file=Path(pending_character["character_file"]).name)
        set_meta(db, f"character_session_input:{chat_id}", "")
    _cancel_pending(db, token, chat_id, meta_key, state)
    suffix = f"\nCharacter: {pending_character.get('character_name') or Path(pending_character['character_file']).stem}" if pending_character else ""
    send_text(token, chat_id, f"New session started: {title}{suffix}")
    return True


# Explicit late imports replace transitional dependency injection.
from bridge.callbacks import (
    close_panel_message,
    discard_panel_binding,
)
from bridge.cards import send_character_menu
from bridge.config import (
    DEFAULT_MODEL,
    PENDING_SETTINGS_TTL_SECONDS,
)
from bridge.database import (
    get_meta,
    set_meta,
)
from bridge.groups import start_group_session
from bridge.input_flows import (
    _cancel_pending,
    pending_character_for_session,
)
from bridge.message_commands import send_pending_input_message
from bridge.composition import RequestContext
from bridge.telegram import (
    create_session,
    delete_pending_input_prompts,
    send_text,
    update_session,
)

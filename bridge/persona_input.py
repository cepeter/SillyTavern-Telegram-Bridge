"""Canonical persona input owner."""

from __future__ import annotations

import json
import logging
import re
import time

from bridge.callbacks import close_panel_message, discard_panel_binding
from bridge.cards import send_persona_menu
from bridge.limits import PENDING_SETTINGS_TTL_SECONDS
from bridge.message_commands import send_pending_input_message
from bridge.metadata import set_meta
from bridge.pending_input import _cancel_pending
from bridge.persona_panels import _persona_input_prompt
from bridge.persona_service import PersonaService
from bridge.telegram import send_text


def start_persona_input(
    db,
    token: str,
    chat_id: str,
    session_id: str,
    mode: str,
    persona_id: str,
    callback: dict,
    *,
    persona_service: PersonaService,
) -> None:
    """Close the persona panel and start a scoped create/edit text input."""
    state = {
        "session_id": session_id,
        "mode": mode,
        "persona_id": persona_id,
        "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS,
    }
    meta_key = f"persona_input:{chat_id}"
    discard_panel_binding(db, chat_id, (callback.get("message") or {}).get("message_id"))
    close_panel_message(db, token, chat_id, callback)
    state["prompt_message_ids"] = send_text(
        token,
        chat_id,
        _persona_input_prompt(
            mode,
            persona_service.name(persona_id),
            persona_id,
            persona_service=persona_service,
        ),
    )
    set_meta(db, meta_key, json.dumps(state, ensure_ascii=False))


def _handle_persona_input(
    db,
    token: str,
    chat_id: str,
    session: dict,
    stripped: str,
    state: dict,
    operation_id: int | None,
    *,
    persona_service: PersonaService,
    request_context,
) -> bool:
    """Validate Persona input and delegate lifecycle changes to PersonaService."""
    meta_key = f"persona_input:{chat_id}"
    if stripped.casefold() in {"/cancel", "cancel"}:
        _cancel_pending(db, token, chat_id, meta_key, state)
        send_persona_menu(
            token,
            chat_id,
            session.get("persona_id") or "",
            persona_service=persona_service,
            request_context=request_context,
        )
        return True
    mode = str(state.get("mode") or "")
    persona_id = str(state.get("persona_id") or "")
    current = persona_service.get(persona_id) if persona_id else None
    if mode == "create":
        parts = [part.strip() for part in stripped.split("|", 2)]
        if len(parts) != 3 or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", parts[0]):
            send_pending_input_message(
                db,
                token,
                chat_id,
                meta_key,
                state,
                "Use: id | display name | persona description. Try again or send /cancel.",
            )
            return True
        requested_id, name, description = parts
        if not 1 <= len(name) <= 120 or not 1 <= len(description) <= 4000:
            send_pending_input_message(
                db,
                token,
                chat_id,
                meta_key,
                state,
                (
                    "Persona ID must be new; name 1–120 characters; description 1–4,000 "
                    "characters. Try again or send /cancel."
                ),
            )
            return True
        target_id = requested_id
    elif mode in {"edit", "edit_all", "edit_name", "edit_description"} and current:
        current_name = str(current.get("name") or persona_id)
        current_description = str(current.get("description") or "")
        if mode == "edit_name":
            name, description = stripped, current_description
        elif mode == "edit_description":
            name, description = current_name, stripped
        else:
            parts = [part.strip() for part in stripped.split("|", 1)]
            name = current_name if len(parts) == 1 else parts[0]
            description = parts[0] if len(parts) == 1 else parts[1]
        if not 1 <= len(name) <= 120 or not 1 <= len(description) <= 4000:
            send_pending_input_message(
                db,
                token,
                chat_id,
                meta_key,
                state,
                "Display name must be 1–120 characters and description 1–4,000 characters. Try again or send /cancel.",
            )
            return True
        target_id = persona_id
    else:
        _cancel_pending(db, token, chat_id, meta_key, state)
        send_persona_menu(
            token,
            chat_id,
            session.get("persona_id") or "",
            persona_service=persona_service,
            request_context=request_context,
        )
        return True
    try:
        if mode == "create":
            native_avatar = persona_service.create_and_select(
                db,
                chat_id,
                session["session_id"],
                target_id,
                name,
                description,
                operation_id=operation_id,
            )
        else:
            native_avatar = persona_service.update(
                target_id,
                name,
                description,
            )
    except ValueError as exc:
        if mode == "create" and (
            "already exists" in str(exc)
            or "Persona ID" in str(exc)
            or "Persona name" in str(exc)
            or "Persona description" in str(exc)
        ):
            send_pending_input_message(
                db,
                token,
                chat_id,
                meta_key,
                state,
                (
                    "Persona ID must be new; name 1–120 characters; description 1–4,000 "
                    "characters. Try again or send /cancel."
                ),
            )
            return True
        logging.warning("Native SillyTavern Persona save failed", exc_info=True)
        send_pending_input_message(
            db, token, chat_id, meta_key, state, f"Native Persona could not be saved: {exc}. Try again or send /cancel."
        )
        return True
    except Exception as exc:
        logging.warning("Native SillyTavern Persona save failed", exc_info=True)
        send_pending_input_message(
            db, token, chat_id, meta_key, state, f"Native Persona could not be saved: {exc}. Try again or send /cancel."
        )
        return True
    _cancel_pending(db, token, chat_id, meta_key, state)
    send_text(
        token,
        chat_id,
        f"Persona {'created and selected' if mode == 'create' else 'updated'}: {persona_service.name(native_avatar)}",
    )
    send_persona_menu(
        token,
        chat_id,
        native_avatar if mode == "create" else session.get("persona_id") or "",
        persona_service=persona_service,
        request_context=request_context,
    )
    return True

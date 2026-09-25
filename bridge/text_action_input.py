"""Canonical text action input owner."""

from __future__ import annotations

import json
import time

from bridge.callbacks import close_panel_message, discard_panel_binding
from bridge.databank_panels import send_databank_menu
from bridge.director_goal_panel import director_goal_panel
from bridge.director_goals import set_director_goal
from bridge.edit_messages import edit_last_user
from bridge.image_generation import handle_imagine_prompt
from bridge.limits import PENDING_SETTINGS_TTL_SECONDS
from bridge.macro_commands import handle_macro_command
from bridge.memory import handle_memory_command
from bridge.memory_backend import remember_fact
from bridge.memory_panels import send_memory_menu
from bridge.memory_service import MemoryService
from bridge.message_commands import send_pending_input_message
from bridge.metadata import set_meta
from bridge.pending_input import _cancel_pending
from bridge.persona_service import PersonaService
from bridge.provider_port import ProviderPort
from bridge.rag import handle_data_bank_command
from bridge.telegram import send_panel_request, send_text


def start_text_action_input(
    db, token: str, chat_id: str, session_id: str, action: str, prompt: str, callback: dict | None = None
) -> None:
    state = {"session_id": session_id, "action": action, "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
    meta_key = f"text_action_input:{chat_id}"
    if callback:
        message_id = (callback.get("message") or {}).get("message_id")
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(db, token, chat_id, callback)
    state["prompt_message_ids"] = send_text(token, chat_id, prompt + "\n\nSend /cancel to cancel.")
    set_meta(db, meta_key, json.dumps(state, ensure_ascii=False))


def handle_inline_text_action(
    db,
    token: str,
    api_key: str,
    chat_id: str,
    session: dict,
    fields: dict,
    action: str,
    value: str,
    operation_id: int | None = None,
    *,
    provider_port: ProviderPort,
    memory_service: MemoryService,
    persona_service: PersonaService,
    request_context,
) -> bool:
    """Run a bounded text action immediately when a command includes its value."""
    state = {
        "session_id": session["session_id"],
        "action": action,
        "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS,
    }
    return _handle_text_action_input(
        db,
        token,
        api_key,
        chat_id,
        session,
        fields,
        value,
        state,
        operation_id,
        provider_port=provider_port,
        memory_service=memory_service,
        persona_service=persona_service,
        request_context=request_context,
    )


def _handle_text_action_input(
    db,
    token: str,
    api_key: str,
    chat_id: str,
    session: dict,
    fields: dict,
    stripped: str,
    state: dict,
    operation_id: int | None,
    *,
    provider_port: ProviderPort,
    memory_service: MemoryService,
    persona_service: PersonaService,
    request_context,
) -> bool:
    meta_key = f"text_action_input:{chat_id}"
    if stripped.casefold() in {"/cancel", "cancel"}:
        _cancel_pending(db, token, chat_id, meta_key, state)
        send_text(token, chat_id, "Cancelled.")
        return True
    action = str(state.get("action") or "")
    value = stripped.strip()
    if not value:
        send_pending_input_message(
            db, token, chat_id, meta_key, state, "Input cannot be empty. Try again or send /cancel."
        )
        return True
    try:
        if action == "edit":
            edit_last_user(
                db,
                token,
                api_key,
                session,
                fields,
                chat_id,
                value[:12000],
                operation_id=operation_id,
                provider_port=provider_port,
                memory_service=memory_service,
                persona_service=persona_service,
                app_settings=request_context.app_settings,
            )
        elif action == "remember":
            if len(value) > 4000 or not remember_fact(
                db, chat_id, session, fields, value, app_settings=request_context.app_settings
            ):
                raise ValueError("Hindsight memory is unavailable or exceeds 4,000 characters")
            send_text(token, chat_id, "Memory queued for Hindsight.")
        elif action == "macro":
            handle_macro_command(
                db, token, chat_id, session, fields, "/macro " + value, request_context=request_context
            )
        elif action == "imagine":
            handle_imagine_prompt(token, chat_id, value, app_settings=request_context.app_settings)
        elif action == "memory_search":
            handle_memory_command(
                db,
                token,
                chat_id,
                session,
                fields,
                "/memory search " + value,
                send_text_fn=send_text,
                app_settings=request_context.app_settings,
            )
            send_memory_menu(token, chat_id, db, request_context=request_context)
        elif action == "databank_search":
            handle_data_bank_command(
                db, token, chat_id, "/databank search " + value, app_settings=request_context.app_settings
            )
            send_databank_menu(token, chat_id, db, request_context=request_context)
        elif action == "director_goal":
            if len(value) > 1200:
                raise ValueError("Director objective exceeds 1,200 characters")
            saved_goal = set_director_goal(
                db,
                chat_id,
                session["session_id"],
                value,
            )
            panel_text, panel_markup = director_goal_panel(saved_goal)
            send_panel_request(
                token,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": panel_text,
                    "reply_markup": panel_markup,
                },
                request_context=request_context,
            )
        else:
            raise ValueError("Unknown text action")
    except ValueError as exc:
        send_pending_input_message(db, token, chat_id, meta_key, state, f"{exc}. Try again or send /cancel.")
        return True
    _cancel_pending(db, token, chat_id, meta_key, state)
    return True

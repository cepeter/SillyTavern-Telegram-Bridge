"""Thin Telegram adapters for the conversation setup coordinator."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import close_panel_message
from bridge.conversation_setup import ConversationSetupService, setup_key
from bridge.conversation_setup_panels import send_setup_panel
from bridge.metadata import get_meta
from bridge.persona_service import PersonaService
from bridge.request_types import RequestContext
from bridge.telegram import send_text


def handle_setup_callback(
    db: sqlite3.Connection,
    token: str,
    callback: dict,
    answer_callback: Callable,
    data: str,
    chat_id: str,
    message: dict,
    session: dict,
    *,
    persona_service: PersonaService,
    request_context: RequestContext,
) -> bool:
    if not data.startswith("setup:"):
        return False
    service = ConversationSetupService(request_context.app_settings, persona_service)
    try:
        raw = resolve_dynamic_callback_token(data.split(":", 1)[1], "conversation_setup", chat_id, db=db)
        command = json.loads(raw or "{}")
        nonce, stage, action = command["nonce"], command["stage"], command["action"]
        state = service.load(db, chat_id, session["session_id"], request_context.actor_id, nonce)
        if stage != state["stage"]:
            raise ValueError("This setup step is no longer active")
        if action == "cancel":
            service.cancel(db, chat_id, session, request_context.actor_id, nonce)
            close_panel_message(db, token, chat_id, {"message": message})
        elif action == "apply":
            result = service.apply(db, chat_id, session, request_context.actor_id, nonce)
            close_panel_message(db, token, chat_id, {"message": message})
            send_text(
                token, chat_id, f"Session configured: {result['title']}. Use /start to choose the opening message."
            )
        else:
            if action == "back":
                state = service.back(db, chat_id, session, request_context.actor_id, nonce)
            elif action == "page":
                state["page"] = max(0, int(command["value"]))
            else:
                state = service.choose(
                    db,
                    chat_id,
                    session,
                    request_context.actor_id,
                    nonce,
                    stage,
                    action,
                    str(command.get("value") or ""),
                )
            send_setup_panel(
                token,
                chat_id,
                state,
                message.get("message_id"),
                persona_service=persona_service,
                request_context=request_context,
            )
        answer_callback(token, str(callback.get("id") or ""), "Setup updated")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        answer_callback(token, str(callback.get("id") or ""), "Setup unavailable")
        send_text(token, chat_id, str(exc) if isinstance(exc, ValueError) else "Setup expired; run /character again.")
    return True


def handle_setup_name_input(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict,
    text: str,
    *,
    persona_service: PersonaService,
    request_context: RequestContext,
) -> bool:
    raw = get_meta(db, setup_key(chat_id, request_context.actor_id), "")
    if not raw:
        return False
    try:
        state = json.loads(raw)
    except (ValueError, TypeError):
        return False
    if not isinstance(state, dict) or state.get("stage") != "session_name":
        return False
    service = ConversationSetupService(request_context.app_settings, persona_service)
    try:
        if text.strip().casefold() in {"/cancel", "cancel"}:
            service.cancel(db, chat_id, session, request_context.actor_id, state["nonce"])
            send_text(token, chat_id, "Conversation setup cancelled.")
        else:
            state = service.set_title(db, chat_id, session, request_context.actor_id, text)
            send_setup_panel(token, chat_id, state, persona_service=persona_service, request_context=request_context)
    except ValueError as exc:
        send_text(token, chat_id, str(exc))
    return True

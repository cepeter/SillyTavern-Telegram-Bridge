"""Canonical settings callbacks owner."""

from __future__ import annotations

import json
import time

from bridge.callbacks import close_panel_message, discard_panel_binding
from bridge.card_content import get_system_prompt_choice
from bridge.commands import send_note_menu
from bridge.delivery_port import DeliveryPort
from bridge.expressions import (
    discover_expression_assets,
    expression_last_key,
    expression_mode_key,
    send_expression_menu,
)
from bridge.help import send_system_prompt_menu
from bridge.language import response_language_label, send_language_menu, set_response_language
from bridge.limits import PENDING_SETTINGS_TTL_SECONDS
from bridge.media import remove_inline_keyboard
from bridge.metadata import set_meta
from bridge.session_core import update_session
from bridge.telegram import send_text


def handle_system_prompt_callback(
    db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id, *, request_context
):
    if data.startswith("systemprompt:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            page = int(value.split(":", 1)[1])
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_system_prompt_menu(
                token,
                chat_id,
                session.get("system_prompt") or "",
                message.get("message_id"),
                page,
                request_context=request_context,
            )
            return True
        if value == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            remove_inline_keyboard(db, token, callback)
        elif value == "off":
            update_session(db, chat_id, session_id, system_prompt="")
            answer_callback(token, str(callback.get("id", "")), "System Prompt off")
            remove_inline_keyboard(db, token, callback)
            send_text(token, chat_id, "Session System Prompt disabled.")
        else:
            prompt = get_system_prompt_choice(value, app_settings=request_context.app_settings)
            if prompt is None:
                answer_callback(token, str(callback.get("id", "")), "Choice not found")
            else:
                update_session(db, chat_id, session_id, system_prompt=prompt)
                answer_callback(token, str(callback.get("id", "")), "System Prompt selected")
                remove_inline_keyboard(db, token, callback)
                send_text(token, chat_id, f"System Prompt selected: {value}")
        return True
    return False


def handle_note_callback(
    db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id, *, request_context
):
    """Handle Author's Note cancel, disable, and input callbacks."""
    if data.startswith("note:"):
        action = data.split(":", 1)[1]
        if action == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            discard_panel_binding(db, chat_id, message.get("message_id"))
            close_panel_message(db, token, chat_id, callback)
            return True
        if action == "off":
            update_session(
                db, chat_id, session_id, operation_id=operation_id, operation_kind="author_note_off", author_note=""
            )
            answer_callback(token, str(callback.get("id", "")), "Author's Note off")
            send_note_menu(token, chat_id, "", message.get("message_id"), request_context=request_context)
            return True
        if action == "input":
            pending_note = {"session_id": session_id, "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
            set_meta(db, f"note_input:{chat_id}", json.dumps(pending_note))
            answer_callback(token, str(callback.get("id", "")), "User input")
            discard_panel_binding(db, chat_id, message.get("message_id"))
            close_panel_message(db, token, chat_id, callback)
            pending_note["prompt_message_ids"] = send_text(
                token, chat_id, "Send Author's Note text (1–2,000 characters). Send /cancel to cancel."
            )
            set_meta(db, f"note_input:{chat_id}", json.dumps(pending_note))
            return True
        answer_callback(token, str(callback.get("id", "")), "Unknown note action")
        return True
    return False


def handle_language_callback(
    db,
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    message,
    session,
    session_id,
    operation_id,
    *,
    delivery_port: DeliveryPort,
    request_context,
):
    """Handle model response language selection and pagination callbacks."""
    if data.startswith("language:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            page = int(value.split(":", 1)[1])
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_language_menu(
                token,
                chat_id,
                session.get("response_language") or "auto",
                message.get("message_id"),
                page,
                delivery_port=delivery_port,
                request_context=request_context,
            )
            return True
        if value == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            remove_inline_keyboard(db, token, callback)
            return True
        try:
            language = set_response_language(
                db, chat_id, session_id, value, operation_id=operation_id, update_session=update_session
            )
        except ValueError:
            answer_callback(token, str(callback.get("id", "")), "Language choice expired")
            return True
        answer_callback(token, str(callback.get("id", "")), "Language selected")
        remove_inline_keyboard(db, token, callback)
        send_text(token, chat_id, f"Model response language set to: {response_language_label(language)}.")
        return True
    return False


def handle_expression_callback(
    db,
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    message,
    session,
    session_id,
    operation_id,
    *,
    delivery_port: DeliveryPort,
    request_context,
):
    """Handle manual, automatic, and disabled expression modes."""
    if not data.startswith("expression:"):
        return False
    value = data.split(":", 1)[1]
    if value.startswith("page:"):
        try:
            page = max(0, int(value.split(":", 1)[1]))
        except ValueError:
            page = 0
        answer_callback(token, str(callback.get("id", "")), "Page updated")
        send_expression_menu(
            token,
            chat_id,
            session,
            db,
            message.get("message_id"),
            page,
            delivery_port=delivery_port,
            request_context=request_context,
        )
        return True
    if value == "cancel":
        answer_callback(token, str(callback.get("id", "")), "Cancelled")
        remove_inline_keyboard(db, token, callback)
        return True
    if value not in {"auto", "off"} and value not in discover_expression_assets(
        session["character_file"], app_settings=request_context.app_settings
    ):
        answer_callback(token, str(callback.get("id", "")), "Expression unavailable")
        return True
    set_meta(db, expression_mode_key(chat_id, session_id), value)
    set_meta(db, expression_last_key(chat_id, session_id), "")
    db.commit()
    answer_callback(token, str(callback.get("id", "")), "Expression updated")
    send_expression_menu(
        token,
        chat_id,
        session,
        db,
        message.get("message_id"),
        delivery_port=delivery_port,
        request_context=request_context,
    )
    return True

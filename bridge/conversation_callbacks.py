"""Canonical conversation callbacks owner."""

from __future__ import annotations

import json
import logging

from bridge.callbacks import close_panel_message, discard_panel_binding, remove_inline_keyboard
from bridge.card_content import card_fields_from_file
from bridge.conversation_lifecycle import ALREADY_STARTED, conversation_state, is_group_conversation
from bridge.delivery_port import DeliveryPort
from bridge.greetings import greeting_choice_label, greeting_options, send_character_greeting, send_greeting_menu
from bridge.message_commands import reset_session
from bridge.metadata import get_meta
from bridge.response_delivery import delete_outgoing_messages
from bridge.response_variants import keep_swipe_variant, last_user_variants, swipe_state_key
from bridge.swipe_panels import edit_swipe_menu
from bridge.telegram import send_text, telegram_request


def handle_reset_callback(
    db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id, *, memory_service
):
    """Handle reset confirmation and cancellation callbacks."""
    if data.startswith("reset:"):
        action = data.split(":", 1)[1]
        if action == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            remove_inline_keyboard(db, token, callback)
            return True
        if action != "confirm":
            answer_callback(token, str(callback.get("id", "")), "Unknown reset action")
            return True
        try:
            reset_session(db, token, chat_id, session, operation_id=operation_id, memory_service=memory_service)
        except Exception:
            logging.error("Reset failed for chat %s/session %s", chat_id, session_id, exc_info=True)
            answer_callback(token, str(callback.get("id", "")), "Reset failed; memory and session were preserved")
            send_text(
                token, chat_id, "Reset cancelled because Hindsight memory purge failed. No session data was deleted."
            )
            return True
        answer_callback(token, str(callback.get("id", "")), "Reset complete")
        remove_inline_keyboard(db, token, callback)
        send_text(token, chat_id, "Reset complete. The active session was cleared.")
        return True
    return False


def handle_swipe_callback(
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
    """Handle response variant browsing and keep/cancel callbacks."""
    if data.startswith("swipe:"):
        action = data.split(":", 1)[1]
        user_row, variants = last_user_variants(db, chat_id, session_id)
        if not variants:
            answer_callback(token, str(callback.get("id", "")), "No variants")
            remove_inline_keyboard(db, token, callback)
            return True
        current = int(get_meta(db, swipe_state_key(chat_id, session_id), str(variants[-1][0])))
        indexes = [int(row[0]) for row in variants]
        if action == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            remove_inline_keyboard(db, token, callback)
            return True
        if action in {"prev", "next"}:
            position = indexes.index(current) if current in indexes else 0
            position = (position - 1) % len(indexes) if action == "prev" else (position + 1) % len(indexes)
            answer_callback(token, str(callback.get("id", "")), f"Variant {indexes[position]}")
            edit_swipe_menu(
                token,
                db,
                callback,
                session_id,
                indexes[position],
                variants,
                delivery_port=delivery_port,
                request_context=request_context,
            )
            return True
        if action == "keep":
            if user_row:
                delete_outgoing_messages(db, token, chat_id, session_id, int(user_row[0]))
            selected = keep_swipe_variant(db, chat_id, session_id, current)
            if selected is None:
                answer_callback(token, str(callback.get("id", "")), "Variant not found")
                return True
            answer_callback(token, str(callback.get("id", "")), "Kept")
            telegram_request(
                token,
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message.get("message_id"),
                    "text": f"✅ Kept variant {current}\n\n{selected[:3900]}",
                },
            )
            return True
        return True
    return False


def handle_greeting_callback(
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
    persona_service,
    request_context,
):
    """Preview and commit a selected character-card opening greeting."""
    if not data.startswith("greeting:"):
        return False

    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    message_id = message.get("message_id")
    fields = card_fields_from_file(session["character_file"], app_settings=request_context.app_settings)
    options = greeting_options(fields)
    persona_id = str(session.get("persona_id") or "")
    user_name = (
        persona_service.name(persona_id) if persona_id else ""
    ) or request_context.app_settings.default_user_name

    if action == "cancel":
        answer_callback(token, str(callback.get("id", "")), "Cancelled")
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(db, token, chat_id, callback)
        return True

    if not is_group_conversation(db, chat_id, session_id):
        state = conversation_state(db, chat_id, session_id)
        try:
            supplied_epoch = int(parts[-1]) if len(parts) >= (5 if action == "page" else 4) else -1
        except ValueError:
            supplied_epoch = -1
        if get_meta(db, f"active_session:{chat_id}", "default") != session_id or supplied_epoch != state.epoch:
            send_text(token, chat_id, "Opening choice expired; use /start again.")
            return True
        if state.started:
            from_opening = get_meta(db, f"conversation_opening:{chat_id}:{session_id}", "")
            try:
                opening = json.loads(from_opening or "{}")
                opening_operation = opening.get("operation_id")
                pending = db.execute(
                    "SELECT rowid,telegram_message_ids FROM messages WHERE chat_id=? "
                    "AND session_id=? ORDER BY rowid DESC LIMIT 1",
                    (chat_id, session_id),
                ).fetchone()
                undelivered = bool(
                    pending and pending[0] == opening.get("rowid") and not json.loads(pending[1] or "[]")
                )
            except (ValueError, TypeError, AttributeError):
                opening_operation, undelivered = None, False
            if action != "use" or operation_id is None or opening_operation is None:
                send_text(token, chat_id, ALREADY_STARTED)
                return True
            if str(opening_operation) != str(operation_id):
                if not undelivered:
                    send_text(token, chat_id, ALREADY_STARTED)
                    return True
                # Re-deliver the durable original, even if the card has since changed.
                operation_id = opening_operation

    if not options:
        answer_callback(token, str(callback.get("id", "")), "Greeting unavailable")
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(db, token, chat_id, callback)
        send_text(token, chat_id, "This character has no opening greeting.")
        return True

    if action == "page":
        try:
            page = max(0, int(parts[2]))
            selected_index = int(parts[3]) if len(parts) > 3 else 0
        except (ValueError, IndexError):
            answer_callback(token, str(callback.get("id", "")), "Greeting panel expired")
            return True
        if selected_index < 0 or selected_index >= len(options):
            selected_index = 0
        answer_callback(token, str(callback.get("id", "")), "Page")
        send_greeting_menu(
            token,
            chat_id,
            fields,
            user_name,
            message_id=message_id,
            selected_index=selected_index,
            page=page,
            request_context=request_context,
        )
        return True

    if action in {"preview", "use"}:
        try:
            selected_index = int(parts[2])
        except (ValueError, IndexError):
            answer_callback(token, str(callback.get("id", "")), "Greeting choice expired")
            return True
        if selected_index < 0 or selected_index >= len(options):
            answer_callback(token, str(callback.get("id", "")), "Greeting choice expired")
            return True
        label = greeting_choice_label(selected_index)
        if action == "preview":
            answer_callback(token, str(callback.get("id", "")), label)
            send_greeting_menu(
                token,
                chat_id,
                fields,
                user_name,
                message_id=message_id,
                selected_index=selected_index,
                request_context=request_context,
            )
            return True

        started = send_character_greeting(
            db,
            token,
            chat_id,
            fields,
            session_id,
            user_name,
            selected_index,
            operation_id,
            "start_greeting",
            app_settings=request_context.app_settings,
            expected_epoch=conversation_state(db, chat_id, session_id).epoch,
            actor_id=request_context.actor_id,
        )
        answer_callback(
            token,
            str(callback.get("id", "")),
            f"Started with {label}" if started else "Greeting already processed",
        )
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(db, token, chat_id, callback)
        return True

    answer_callback(token, str(callback.get("id", "")), "Unknown greeting action")
    return True

"""Canonical persona callbacks owner."""

from __future__ import annotations

import logging

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import remove_inline_keyboard
from bridge.cards import send_persona_menu
from bridge.persona_delete_panel import send_persona_delete_menu
from bridge.persona_input import start_persona_input
from bridge.persona_panels import send_persona_delete_confirm, send_persona_edit_menu
from bridge.persona_service import PersonaService
from bridge.telegram import send_text


def handle_persona_callback(
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
    persona_service: PersonaService,
    request_context,
):
    """Handle persona selection, review, field editing, disable, and deletion callbacks."""
    message_id = message.get("message_id")
    if data.startswith("persona:delete_page:"):
        page = max(0, int(data.rsplit(":", 1)[1]))
        send_persona_delete_menu(
            token,
            chat_id,
            session.get("persona_id") or "",
            message_id,
            page,
            persona_service=persona_service,
            request_context=request_context,
        )
        return True
    if data.startswith("persona:delete:"):
        target = resolve_dynamic_callback_token(data.split(":", 2)[2], "persona", chat_id, db=request_context.db) or ""
        if not target or target == session.get("persona_id"):
            answer_callback(token, str(callback.get("id", "")), "Cannot delete the current Persona")
            return True
        send_persona_delete_confirm(
            token,
            chat_id,
            target,
            message_id,
            persona_service=persona_service,
            request_context=request_context,
        )
        return True
    if data.startswith("personadeleteconfirm:"):
        persona_id = (
            resolve_dynamic_callback_token(data.split(":", 1)[1], "persona", chat_id, db=request_context.db) or ""
        )
        if not persona_id:
            answer_callback(token, str(callback.get("id", "")), "Persona not found")
            return True
        if persona_id == session.get("persona_id"):
            answer_callback(token, str(callback.get("id", "")), "Disable or switch the current Persona first")
            return True
        try:
            deleted = persona_service.delete_if_unused(db, persona_id)
        except ValueError as exc:
            if "used by another session" in str(exc):
                answer_callback(
                    token, str(callback.get("id", "")), "Deletion refused: Persona is used by another session"
                )
                return True
            logging.warning("Native Persona deletion failed", exc_info=True)
            answer_callback(token, str(callback.get("id", "")), "Persona deletion failed")
            return True
        except Exception:
            logging.warning("Native Persona deletion failed", exc_info=True)
            answer_callback(token, str(callback.get("id", "")), "Persona deletion failed")
            return True
        answer_callback(token, str(callback.get("id", "")), "Deleted" if deleted else "Persona not found")
        send_persona_menu(
            token,
            chat_id,
            session.get("persona_id") or "",
            message_id,
            persona_service=persona_service,
            request_context=request_context,
        )
        return True
    if not data.startswith("persona:"):
        return False
    value = data.split(":", 1)[1]
    message_id = message.get("message_id")
    field_actions = {"create", "edit", "edit_name", "edit_description", "edit_all", "delete"}
    if not value.startswith("page:") and value not in {"cancel", "off", "menu", *field_actions}:
        value = resolve_dynamic_callback_token(value, "persona", chat_id, db=request_context.db) or ""
    if value.startswith("page:"):
        answer_callback(token, str(callback.get("id", "")), "Page")
        send_persona_menu(
            token,
            chat_id,
            session["persona_id"],
            message_id,
            int(value.split(":", 1)[1]),
            persona_service=persona_service,
            request_context=request_context,
        )
        return True
    if value == "menu":
        answer_callback(token, str(callback.get("id", "")), "Personas")
        send_persona_menu(
            token,
            chat_id,
            session.get("persona_id") or "",
            message_id,
            persona_service=persona_service,
            request_context=request_context,
        )
        return True
    if value == "edit":
        persona_id = session.get("persona_id") or ""
        if not persona_service.get(persona_id):
            answer_callback(token, str(callback.get("id", "")), "Current persona not found")
            return True
        answer_callback(token, str(callback.get("id", "")), "Review persona")
        send_persona_edit_menu(
            token,
            chat_id,
            persona_id,
            message_id,
            persona_service=persona_service,
            request_context=request_context,
        )
        return True
    if value in {"create", "edit_name", "edit_description", "edit_all"}:
        persona_id = session.get("persona_id") or "" if value != "create" else ""
        if value != "create" and not persona_service.get(persona_id):
            answer_callback(token, str(callback.get("id", "")), "Current persona not found")
            return True
        answer_callback(token, str(callback.get("id", "")), "Enter persona text")
        start_persona_input(
            db,
            token,
            chat_id,
            session_id,
            value,
            persona_id,
            callback,
            persona_service=persona_service,
        )
        return True
    if value == "delete":
        answer_callback(token, str(callback.get("id", "")), "Choose an inactive Persona")
        send_persona_delete_menu(
            token,
            chat_id,
            session.get("persona_id") or "",
            message_id,
            persona_service=persona_service,
            request_context=request_context,
        )
        return True
    if value == "cancel":
        answer_callback(token, str(callback.get("id", "")), "Cancelled")
        remove_inline_keyboard(db, token, callback)
        return True
    if value == "off":
        persona_service.disable(
            db,
            chat_id,
            session_id,
            operation_id=operation_id,
        )
        answer_callback(token, str(callback.get("id", "")), "Persona off")
        remove_inline_keyboard(db, token, callback)
        send_text(token, chat_id, "Persona disabled for this session.")
        return True
    if persona_service.select(
        db,
        chat_id,
        session_id,
        value,
        operation_id=operation_id,
    ):
        answer_callback(token, str(callback.get("id", "")), "Persona selected")
        remove_inline_keyboard(db, token, callback)
        send_text(token, chat_id, f"Persona selected: {persona_service.name(value)}")
        return True
    answer_callback(token, str(callback.get("id", "")), "Persona not found")
    return True

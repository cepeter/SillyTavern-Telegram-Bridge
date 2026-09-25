"""Canonical session callbacks owner."""

from __future__ import annotations

from pathlib import Path

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.cards import send_session_menu
from bridge.group_service import GroupService
from bridge.input_flows import pending_character_for_session
from bridge.media import remove_inline_keyboard
from bridge.metadata import set_meta
from bridge.session_core import delete_session_data, list_sessions, update_session
from bridge.session_naming import start_session_name_input
from bridge.session_panels import send_session_delete_confirm, send_session_delete_menu
from bridge.telegram import send_text


def handle_session_callback(
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
    group_service: GroupService,
    memory_service,
    request_context,
):
    """Handle session selection, creation, and deletion callbacks."""
    if data == "session:protected":
        answer_callback(token, str(callback.get("id", "")), "Active session is protected")
        send_session_menu(
            token,
            chat_id,
            list_sessions(db, chat_id),
            session_id,
            message.get("message_id"),
            request_context=request_context,
        )
        return True
    if data.startswith("sessiondeleteconfirm:"):
        target_session_id = resolve_dynamic_callback_token(data.split(":", 1)[1], "session", chat_id, db=db) or ""
        target = next((item for item in list_sessions(db, chat_id) if item["session_id"] == target_session_id), None)
        if target is None:
            answer_callback(token, str(callback.get("id", "")), "Session choice expired")
            return True
        deleted, reason = delete_session_data(
            db, chat_id, target_session_id, session_id, operation_id=operation_id, memory_service=memory_service
        )
        if not deleted:
            answer_callback(token, str(callback.get("id", "")), f"Deletion refused: {reason}")
            return True
        answer_callback(token, str(callback.get("id", "")), "Session deleted")
        remove_inline_keyboard(db, token, callback)
        send_session_menu(
            token,
            chat_id,
            list_sessions(db, chat_id),
            session_id,
            message.get("message_id"),
            request_context=request_context,
        )
        return True
    if data.startswith("sessiondelete:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_session_delete_menu(
                token,
                chat_id,
                list_sessions(db, chat_id),
                session_id,
                message.get("message_id"),
                int(value.split(":", 1)[1]),
                request_context=request_context,
            )
            return True
        target_session_id = resolve_dynamic_callback_token(value, "session", chat_id, db=db) or ""
        target = next((item for item in list_sessions(db, chat_id) if item["session_id"] == target_session_id), None)
        if target is None or target_session_id == session_id:
            answer_callback(token, str(callback.get("id", "")), "Only an inactive session can be deleted")
            return True
        answer_callback(token, str(callback.get("id", "")), "Confirm deletion")
        send_session_delete_confirm(
            token,
            chat_id,
            target_session_id,
            target["title"],
            message.get("message_id"),
            request_context=request_context,
        )
        return True
    if data.startswith("session:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_session_menu(
                token,
                chat_id,
                list_sessions(db, chat_id),
                session_id,
                message.get("message_id"),
                int(value.split(":", 1)[1]),
                request_context=request_context,
            )
            return True
        if value == "delete":
            answer_callback(token, str(callback.get("id", "")), "Delete session")
            send_session_delete_menu(
                token,
                chat_id,
                list_sessions(db, chat_id),
                session_id,
                message.get("message_id"),
                request_context=request_context,
            )
            return True
        if value == "back":
            answer_callback(token, str(callback.get("id", "")), "Back")
            send_session_menu(
                token,
                chat_id,
                list_sessions(db, chat_id),
                session_id,
                message.get("message_id"),
                request_context=request_context,
            )
            return True
        if value == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            set_meta(db, f"character_session_input:{chat_id}", "")
            remove_inline_keyboard(db, token, callback)
        elif value == "new":
            answer_callback(token, str(callback.get("id", "")), "Enter session name")
            start_session_name_input(
                db,
                token,
                chat_id,
                session,
                message=message,
                group_service=group_service,
                app_settings=request_context.app_settings,
            )
        else:
            available = {item["session_id"] for item in list_sessions(db, chat_id)}
            if value in available:
                set_meta(db, f"active_session:{chat_id}", value)
                answer_callback(token, str(callback.get("id", "")), "Session selected")
                pending_character = pending_character_for_session(
                    db, chat_id, app_settings=request_context.app_settings
                )
                if pending_character:
                    target_title = next(
                        (item["title"] for item in list_sessions(db, chat_id) if item["session_id"] == value), value
                    )
                    update_session(
                        db,
                        chat_id,
                        value,
                        operation_id=operation_id,
                        operation_kind="character_select",
                        character_file=Path(pending_character["character_file"]).name,
                    )
                    set_meta(db, f"character_session_input:{chat_id}", "")
                remove_inline_keyboard(db, token, callback)
                if pending_character:
                    character_label = (
                        pending_character.get("character_name") or Path(pending_character["character_file"]).stem
                    )
                    send_text(
                        token,
                        chat_id,
                        (
                            "Character selected for session '"
                            f"""{target_title}"""
                            "': "
                            f"{character_label}"
                        ),
                    )
                else:
                    send_text(token, chat_id, f"Session selected: {value}")
            else:
                answer_callback(token, str(callback.get("id", "")), "Session not found")
        return True
    return False

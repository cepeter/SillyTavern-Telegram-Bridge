"""Canonical group callbacks owner."""

from __future__ import annotations

import sqlite3

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import remove_inline_keyboard
from bridge.group_commands import handle_group_command
from bridge.group_panels import (
    send_group_character_menu,
    send_group_menu,
    send_group_mode_menu,
    send_group_remove_confirm,
    send_group_speaker_menu,
)
from bridge.group_service import GroupService
from bridge.input_flow_service import InputFlowService
from bridge.telegram import send_text


def handle_group_panel_callback(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    data: str,
    message: dict,
    operation_id: int | str | None = None,
    sender_id: str = "",
    *,
    group_service: GroupService,
    input_flow_service: InputFlowService,
    request_context,
) -> None:
    message_id = message.get("message_id")
    if data == "group:new_session":
        input_flow_service.start_session_name(
            db, token, chat_id, session, kind="group", message=message, group_service=group_service
        )
    elif data == "group:menu":
        send_group_menu(
            db, token, chat_id, session, message_id, group_service=group_service, request_context=request_context
        )
    elif data == "group:add":
        send_group_character_menu(
            db, token, chat_id, session, "add", message_id, group_service=group_service, request_context=request_context
        )
    elif data == "group:remove":
        send_group_character_menu(
            db,
            token,
            chat_id,
            session,
            "remove",
            message_id,
            group_service=group_service,
            request_context=request_context,
        )
    elif data == "group:speak":
        send_group_speaker_menu(
            db, token, chat_id, session, message_id, group_service=group_service, request_context=request_context
        )
    elif data == "group:mode":
        send_group_mode_menu(
            db, token, chat_id, session, message_id, group_service=group_service, request_context=request_context
        )
    elif data in {"group:claim", "group:pass"}:
        success = (
            group_service.claim_user_turn(db, chat_id, session["session_id"], sender_id)
            if data == "group:claim"
            else group_service.pass_user_turn(db, chat_id, session["session_id"], sender_id)
        )
        send_text(
            token,
            chat_id,
            "User turn claimed."
            if data == "group:claim" and success
            else "User turn passed."
            if data == "group:pass" and success
            else "You cannot change the current user turn.",
        )
        send_group_menu(
            db, token, chat_id, session, message_id, group_service=group_service, request_context=request_context
        )
    elif data in {"group:on", "group:off", "group:next"}:
        handle_group_command(
            db, token, chat_id, session, "/" + data.replace(":", " "), operation_id, group_service=group_service
        )
        send_group_menu(
            db, token, chat_id, session, message_id, group_service=group_service, request_context=request_context
        )
    elif data == "group:close":
        remove_inline_keyboard(db, token, {"message": message})
    elif data.startswith("groupremoveconfirm:"):
        filename = (
            resolve_dynamic_callback_token(data.split(":", 1)[1], "group_character", chat_id, db=request_context.db)
            or ""
        )
        state = group_service.state(db, chat_id, session["session_id"])
        if filename in state["members"]:
            handle_group_command(
                db, token, chat_id, session, f"/group remove {filename}", operation_id, group_service=group_service
            )
        send_group_menu(
            db, token, chat_id, session, message_id, group_service=group_service, request_context=request_context
        )
    elif data.startswith("groupremove:"):
        filename = (
            resolve_dynamic_callback_token(data.split(":", 1)[1], "group_character", chat_id, db=request_context.db)
            or ""
        )
        if filename in group_service.state(db, chat_id, session["session_id"])["members"]:
            send_group_remove_confirm(token, chat_id, filename, message_id, request_context=request_context)
        else:
            send_group_menu(
                db, token, chat_id, session, message_id, group_service=group_service, request_context=request_context
            )
    elif data.startswith("groupchars:page:"):
        _, _, action, page = data.split(":", 3)
        send_group_character_menu(
            db,
            token,
            chat_id,
            session,
            action,
            message_id,
            int(page),
            group_service=group_service,
            request_context=request_context,
        )
    elif data.startswith("groupchars:"):
        _, action, callback_token = data.split(":", 2)
        filename = (
            resolve_dynamic_callback_token(callback_token, "group_character", chat_id, db=request_context.db) or ""
        )
        if filename:
            handle_group_command(
                db, token, chat_id, session, f"/group {action} {filename}", operation_id, group_service=group_service
            )
        send_group_menu(
            db, token, chat_id, session, message_id, group_service=group_service, request_context=request_context
        )
    elif data.startswith("groupmode:"):
        handle_group_command(
            db,
            token,
            chat_id,
            session,
            f"/group mode {data.split(':', 1)[1]}",
            operation_id,
            group_service=group_service,
        )
        send_group_mode_menu(
            db, token, chat_id, session, message_id, group_service=group_service, request_context=request_context
        )

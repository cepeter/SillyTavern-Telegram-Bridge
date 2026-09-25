"""Canonical world callbacks owner."""

from __future__ import annotations

import json
import time
from pathlib import Path

from bridge.callback_tokens import dynamic_callback_token, resolve_dynamic_callback_token
from bridge.callbacks import discard_panel_binding
from bridge.card_content import active_world_files, encode_world_files, safe_world_path
from bridge.cards import send_panel_message
from bridge.catalog import delete_world_info_file, send_world_menu
from bridge.database import set_meta
from bridge.group_service import GroupService
from bridge.groups import send_group_menu
from bridge.limits import PENDING_SETTINGS_TTL_SECONDS
from bridge.media import remove_inline_keyboard
from bridge.session_core import update_session
from bridge.telegram import send_text


def handle_world_callback(
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
    request_context,
):
    """Handle World Info selection, upload, deletion, and pagination callbacks."""
    message_id = message.get("message_id")
    if data.startswith("worlddeleteconfirm:"):
        value = resolve_dynamic_callback_token(data.split(":", 1)[1], "world", chat_id, db=db) or ""
        try:
            delete_world_info_file(db, chat_id, value, app_settings=request_context.app_settings)
        except (ValueError, OSError) as exc:
            answer_callback(token, str(callback.get("id", "")), "Delete refused")
            send_text(token, chat_id, str(exc))
        else:
            answer_callback(token, str(callback.get("id", "")), "World Info deleted")
            send_world_menu(token, chat_id, session["world_file"], message_id, 0, request_context=request_context)
        return True
    if data.startswith("worlddelete:"):
        value = resolve_dynamic_callback_token(data.split(":", 1)[1], "world", chat_id, db=db) or ""
        if not safe_world_path(value, app_settings=request_context.app_settings):
            answer_callback(token, str(callback.get("id", "")), "World Info file not found")
            return True
        answer_callback(token, str(callback.get("id", "")), "Confirm deletion")
        send_panel_message(
            token,
            chat_id,
            f"Delete World Info '{Path(value).name}'? This cannot be undone.",
            {
                "inline_keyboard": [
                    [
                        {
                            "text": "🗑️ Delete",
                            "callback_data": "worlddeleteconfirm:"
                            + dynamic_callback_token("world", value, chat_id, db=db),
                        },
                        {"text": "Cancel", "callback_data": "world:cancel"},
                    ]
                ]
            },
            message_id,
            request_context=request_context,
        )
        return True
    if data.startswith("world:"):
        setup = group_service.setup_state(db, chat_id, session_id)
        value = data.split(":", 1)[1]
        if not value.startswith("page:") and value not in {"cancel", "done", "off", "upload"}:
            value = resolve_dynamic_callback_token(value, "world", chat_id, db=db) or ""
        if value.startswith("page:"):
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_world_menu(
                token,
                chat_id,
                session["world_file"],
                message.get("message_id"),
                int(value.split(":", 1)[1]),
                request_context=request_context,
            )
            return True
        if value == "upload":
            pending = {"session_id": session_id, "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
            set_meta(db, f"world_upload:{chat_id}", json.dumps(pending))
            answer_callback(token, str(callback.get("id", "")), "Send JSON document")
            discard_panel_binding(db, chat_id, message_id)
            send_text(token, chat_id, "Send the World Info JSON as a Telegram document. Use /cancel to abort.")
        elif value == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            if setup:
                set_meta(db, f"group_setup:{chat_id}", "")
            remove_inline_keyboard(db, token, callback)
        elif value == "done":
            answer_callback(token, str(callback.get("id", "")), "Saved")
            if setup:
                set_meta(db, f"group_setup:{chat_id}", "")
            remove_inline_keyboard(db, token, callback)
            if setup:
                send_group_menu(
                    db, token, chat_id, session, group_service=group_service, request_context=request_context
                )
        elif value == "off":
            update_session(
                db, chat_id, session_id, operation_id=operation_id, operation_kind="world_clear", world_file=""
            )
            session["world_file"] = ""
            answer_callback(token, str(callback.get("id", "")), "All World Info cleared")
            send_world_menu(token, chat_id, "", message.get("message_id"), 0, request_context=request_context)
        elif safe_world_path(value, app_settings=request_context.app_settings):
            selected = active_world_files(session["world_file"], app_settings=request_context.app_settings)
            filename = Path(value).name
            if filename in selected:
                selected.remove(filename)
                status = "World Info disabled"
            else:
                selected.append(filename)
                status = "World Info enabled"
            update_session(
                db,
                chat_id,
                session_id,
                operation_id=operation_id,
                operation_kind="world_select",
                world_file=encode_world_files(selected),
            )
            session["world_file"] = encode_world_files(selected)
            answer_callback(token, str(callback.get("id", "")), status)
            send_world_menu(
                token,
                chat_id,
                encode_world_files(selected),
                message.get("message_id"),
                0,
                request_context=request_context,
            )
        else:
            answer_callback(token, str(callback.get("id", "")), "World Info file not found")
        return True
    return False

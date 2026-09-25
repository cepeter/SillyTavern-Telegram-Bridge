"""Route Telegram callbacks through explicit application dependencies."""

from __future__ import annotations

import sqlite3

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import close_panel_message, discard_panel_binding, is_session_scoped_panel_callback
from bridge.catalog import answer_callback
from bridge.composition import BridgeServices
from bridge.database import panel_owner_for_message, panel_session_for_message
from bridge.groups import handle_group_panel_callback
from bridge.help import handle_enum_callback
from bridge.media import remove_inline_keyboard
from bridge.panel_callback_routes import (
    handle_entity_panel_callback,
    handle_primary_panel_callback,
    handle_provider_model_callback,
)
from bridge.request_types import RequestContext
from bridge.telegram import ensure_session, load_session, send_text
from bridge.topic_scope import parse_topic_scope


def process_callback(
    db: sqlite3.Connection,
    token: str,
    callback: dict,
    operation_id: int | None = None,
    *,
    actor_id: str = "",
    services: BridgeServices,
) -> None:
    sender = str(actor_id or (callback.get("from") or {}).get("id", ""))
    message = callback.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    data = str(callback.get("data") or "")
    if not chat_id:
        return

    callback_answer = answer_callback
    if callback.get("_queued"):

        def callback_answer(*_args, **_kwargs):
            return None

    message_id = message.get("message_id")
    bound_session_id = panel_session_for_message(db, chat_id, message_id) if message_id else None
    bound_owner_id = panel_owner_for_message(db, chat_id, message_id) if message_id else ""

    if message_id and bound_owner_id and sender != bound_owner_id:
        feedback = "This panel belongs to another user"
        if callback.get("_queued"):
            send_text(token, chat_id, feedback)
        else:
            callback_answer(token, str(callback.get("id", "")), feedback)
        return

    if message_id and is_session_scoped_panel_callback(data) and not bound_session_id:
        feedback = "Panel expired; reopen it"
        discard_panel_binding(db, chat_id, message_id)
        if callback.get("_queued"):
            send_text(token, chat_id, feedback)
        else:
            callback_answer(token, str(callback.get("id", "")), feedback)
        close_panel_message(db, token, chat_id, callback)
        return

    session = (
        load_session(db, chat_id, bound_session_id, services.config.default_model, app_settings=services.config)
        if bound_session_id
        else ensure_session(db, chat_id, services.config.default_model, app_settings=services.config)
    )
    session_id = session["session_id"]
    request_context = RequestContext(db, session_id, sender, app_settings=services.config)
    memory_service = services.memory
    persona_service = services.persona
    sync_service = services.sync

    if handle_primary_panel_callback(
        db,
        token,
        callback,
        callback_answer,
        data,
        chat_id,
        message,
        session,
        session_id,
        operation_id,
        group_service=services.group,
        provider_port=services.provider,
        delivery_port=services.delivery,
        memory_service=memory_service,
        persona_service=persona_service,
        sync_service=sync_service,
        request_context=request_context,
    ):
        return
    if handle_entity_panel_callback(
        db,
        token,
        callback,
        callback_answer,
        data,
        chat_id,
        message,
        session,
        session_id,
        operation_id,
        group_service=services.group,
        memory_service=memory_service,
        persona_service=persona_service,
        request_context=request_context,
    ):
        return
    if data.startswith("enum:"):
        handle_enum_callback(
            db,
            token,
            chat_id,
            session,
            data,
            message,
            input_flow_service=services.input_flow,
            request_context=request_context,
        )
        return
    if data.startswith("group:") or data.startswith("groupchars:") or data.startswith("groupmode:"):
        if parse_topic_scope(chat_id)[1] is None:
            callback_answer(
                token,
                str(callback.get("id", "")),
                "Forum Topic required",
            )
            remove_inline_keyboard(db, token, callback)
            return
        if data.startswith("groupchars:") and not data.startswith("groupchars:page:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                resolved = resolve_dynamic_callback_token(parts[2], "group_character", chat_id, db=db)
                data = f"groupchars:{parts[1]}:{resolved or ''}"
        handle_group_panel_callback(
            db,
            token,
            chat_id,
            session,
            data,
            message,
            operation_id,
            sender_id=sender,
            group_service=services.group,
            input_flow_service=services.input_flow,
            request_context=request_context,
        )
        return

    handle_provider_model_callback(
        db,
        token,
        callback,
        callback_answer,
        data,
        chat_id,
        message,
        session,
        session_id,
        operation_id,
        request_context=request_context,
    )

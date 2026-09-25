"""Ordered callback dispatch only; feature behavior lives in canonical domain handlers."""

from __future__ import annotations

from bridge.character_callbacks import handle_character_callback
from bridge.conversation_callbacks import handle_greeting_callback, handle_reset_callback, handle_swipe_callback
from bridge.delivery_port import DeliveryPort
from bridge.feature_callbacks import handle_prompt_and_feature_callback
from bridge.group_service import GroupService
from bridge.help_details import handle_help_callback
from bridge.persona_callbacks import handle_persona_callback
from bridge.provider_port import ProviderPort
from bridge.session_callbacks import handle_session_callback
from bridge.settings_callbacks import (
    handle_expression_callback,
    handle_language_callback,
    handle_note_callback,
    handle_system_prompt_callback,
)
from bridge.sync_callbacks import handle_sync_callback
from bridge.sync_service import SyncService
from bridge.update import handle_update_callback
from bridge.world_callbacks import handle_world_callback


def handle_primary_panel_callback(
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
    provider_port: ProviderPort,
    delivery_port: DeliveryPort,
    memory_service,
    persona_service,
    sync_service: SyncService,
    request_context,
):
    """Dispatch System Prompt, Note, language, reset, help, swipe, and expression callbacks."""
    if data.startswith("update:"):
        return handle_update_callback(db, token, callback, data, chat_id, app_settings=request_context.app_settings)
    if handle_greeting_callback(
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
        persona_service=persona_service,
        request_context=request_context,
    ):
        return True
    if handle_expression_callback(
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
        delivery_port=delivery_port,
        request_context=request_context,
    ):
        return True
    if handle_system_prompt_callback(
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
        request_context=request_context,
    ):
        return True
    if handle_note_callback(
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
        request_context=request_context,
    ):
        return True
    if handle_language_callback(
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
        delivery_port=delivery_port,
        request_context=request_context,
    ):
        return True
    if handle_reset_callback(
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
        memory_service=memory_service,
    ):
        return True
    if handle_prompt_and_feature_callback(
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
        group_service=group_service,
        provider_port=provider_port,
        memory_service=memory_service,
        request_context=request_context,
    ):
        return True
    if handle_help_callback(
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
        delivery_port=delivery_port,
        request_context=request_context,
    ):
        return True
    if handle_sync_callback(
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
        sync_service=sync_service,
        request_context=request_context,
    ):
        return True
    return handle_swipe_callback(
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
        delivery_port=delivery_port,
        request_context=request_context,
    )


def handle_entity_panel_callback(
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
    persona_service,
    provider_port: ProviderPort,
    request_context,
):
    """Dispatch character, session, persona, and World Info callbacks."""
    if handle_character_callback(
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
        group_service=group_service,
        provider_port=provider_port,
        request_context=request_context,
    ):
        return True
    if handle_session_callback(
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
        group_service=group_service,
        memory_service=memory_service,
        request_context=request_context,
    ):
        return True
    if handle_persona_callback(
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
        persona_service=persona_service,
        request_context=request_context,
    ):
        return True
    return handle_world_callback(
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
        group_service=group_service,
        request_context=request_context,
    )

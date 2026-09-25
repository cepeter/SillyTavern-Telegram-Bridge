"""Panel-first generation, memory, voice, and sync command handlers."""

from __future__ import annotations

from bridge.databank_commands import handle_data_bank_command
from bridge.databank_panels import send_databank_menu
from bridge.expressions import send_expression_menu
from bridge.feature_panels import send_summary_menu
from bridge.group_commands import handle_group_command
from bridge.group_panels import send_group_menu
from bridge.image_generation import handle_imagine_prompt
from bridge.language import handle_language_command, send_language_menu
from bridge.macro_commands import send_stscript_menu
from bridge.memory import handle_memory_command
from bridge.memory_panels import send_memory_menu
from bridge.note_panels import send_note_menu
from bridge.preset_panels import send_preset_menu
from bridge.rag_service import RagService
from bridge.session_core import update_session
from bridge.settings_panels import send_settings_menu, send_stream_menu
from bridge.sync_panels import send_sync_menu
from bridge.telegram import send_text
from bridge.text_action_input import handle_inline_text_action, start_text_action_input
from bridge.topic_scope import parse_topic_scope
from bridge.update import send_update_menu
from bridge.voice_panels import send_stt_language_menu, send_voice_input_menu, send_voice_menu


def _handle_generation_panels(
    db,
    token,
    fields,
    chat_id,
    stripped,
    command,
    session,
    session_id,
    operation_id,
    *,
    delivery_port,
    provider_port,
    memory_service,
    persona_service,
    request_context,
    rag_service: RagService,
):
    """Handle generation, preset, settings, and response-language panels."""
    if command == "/update":
        send_update_menu(token, chat_id, request_context=request_context)
        return True
    if command == "/imagine":
        start_text_action_input(db, token, chat_id, session_id, "imagine", "Send an image prompt (1–4,000 characters).")
        return True
    if command.startswith("/imagine "):
        try:
            handle_imagine_prompt(token, chat_id, stripped.split(None, 1)[1], app_settings=request_context.app_settings)
        except ValueError as exc:
            send_text(token, chat_id, f"Image generation unavailable: {exc}")
        return True
    if command == "/expression":
        send_expression_menu(token, chat_id, session, db, delivery_port=delivery_port, request_context=request_context)
        return True
    if command == "/stream" or command.startswith("/stream "):
        send_stream_menu(token, chat_id, db, request_context=request_context)
        return True
    if command == "/macro":
        start_text_action_input(
            db, token, chat_id, session_id, "macro", "Send text to preview with supported SillyTavern macros."
        )
        return True
    if command.startswith("/macro "):
        return handle_inline_text_action(
            db,
            token,
            "",
            chat_id,
            session,
            fields,
            "macro",
            stripped.split(None, 1)[1],
            operation_id,
            provider_port=provider_port,
            memory_service=memory_service,
            persona_service=persona_service,
            request_context=request_context,
            rag_service=rag_service,
        )
    if command == "/stscript" or command.startswith("/stscript "):
        send_stscript_menu(token, chat_id, request_context=request_context)
        return True
    if (
        command == "/preset"
        or command in {"/preset list", "/preset use", "/preset delete"}
        or command.startswith("/preset ")
    ):
        send_preset_menu(token, chat_id, db, request_context=request_context)
        return True
    if command == "/settings" or command == "/settings reasoning" or command.startswith("/settings "):
        send_settings_menu(token, chat_id, db, session_id, request_context=request_context)
        return True
    if command == "/language" or command in {"/language list", "/language status"}:
        send_language_menu(
            token,
            chat_id,
            session.get("response_language") or "auto",
            delivery_port=delivery_port,
            request_context=request_context,
        )
        return True
    if command.startswith("/language "):
        handle_language_command(
            db,
            token,
            chat_id,
            session,
            stripped,
            operation_id=operation_id,
            delivery_port=delivery_port,
            update_session=update_session,
            request_context=request_context,
        )
        return True
    return False


def _handle_memory_media(
    db,
    token,
    api_key,
    chat_id,
    stripped,
    command,
    session,
    fields,
    operation_id,
    *,
    request_context,
    delivery_port,
    group_service,
    memory_service,
    persona_service,
    provider_port,
    sync_service,
    rag_service: RagService,
):
    """Handle memory, RAG, group, and synchronization commands."""
    if command == "/memory" or command in {"/memory on", "/memory off", "/memory status", "/memory scope"}:
        send_memory_menu(token, chat_id, db, request_context=request_context)
        return True
    if command == "/memory search":
        send_memory_menu(token, chat_id, db, request_context=request_context)
        return True
    if command.startswith("/memory search "):
        handle_memory_command(
            db,
            token,
            chat_id,
            session,
            fields,
            stripped,
            send_text_fn=delivery_port.send_text,
            app_settings=request_context.app_settings,
        )
        return True
    if command.startswith("/memory "):
        send_memory_menu(token, chat_id, db, request_context=request_context)
        return True
    if command == "/remember":
        start_text_action_input(
            db,
            token,
            chat_id,
            session["session_id"],
            "remember",
            "Send the explicit memory fact to store in the active session.",
        )
        return True
    if command.startswith("/remember "):
        return handle_inline_text_action(
            db,
            token,
            api_key,
            chat_id,
            session,
            fields,
            "remember",
            stripped.split(None, 1)[1],
            operation_id,
            provider_port=provider_port,
            memory_service=memory_service,
            persona_service=persona_service,
            request_context=request_context,
            rag_service=rag_service,
        )
    if command == "/summarize":
        send_summary_menu(token, chat_id, db, session, request_context=request_context)
        return True
    if command == "/databank" or command in {
        "/databank on",
        "/databank off",
        "/databank status",
        "/databank list",
        "/databank remove",
    }:
        send_databank_menu(token, chat_id, db, request_context=request_context)
        return True
    if command in {"/databank search", "/databank versions", "/databank activate", "/databank reindex"}:
        send_databank_menu(token, chat_id, db, request_context=request_context)
        return True
    if (
        command.startswith("/databank search ")
        or command.startswith("/databank versions ")
        or command.startswith("/databank activate ")
        or command.startswith("/databank reindex ")
        or command.startswith("/databank remove ")
    ):
        handle_data_bank_command(
            db, token, chat_id, stripped, app_settings=request_context.app_settings, rag_service=rag_service
        )
        return True
    if command.startswith("/databank "):
        send_databank_menu(token, chat_id, db, request_context=request_context)
        return True
    if command == "/sync":
        send_sync_menu(token, chat_id, db, session, sync_service=sync_service, request_context=request_context)
        return True
    if command == "/group":
        if parse_topic_scope(chat_id)[1] is None:
            send_text(token, chat_id, "Group sessions are available only inside a Telegram Forum Topic.")
        else:
            send_group_menu(db, token, chat_id, session, group_service=group_service, request_context=request_context)
        return True
    if command.startswith("/group "):
        if parse_topic_scope(chat_id)[1] is None:
            send_text(token, chat_id, "Group sessions are available only inside a Telegram Forum Topic.")
        else:
            handle_group_command(db, token, chat_id, session, stripped, operation_id, group_service=group_service)
        return True

    return False


def _handle_voice_panels(db, token, chat_id, command, session, *, request_context):
    """Handle automatic voice, STT, and Author's Note panels."""
    if (
        command == "/voice"
        or command in {"/voice status", "/voice on", "/voice off", "/voice tts"}
        or command.startswith("/voice ")
    ):
        send_voice_menu(token, chat_id, db, request_context=request_context)
        return True
    if (
        command == "/voice_input"
        or command == "/voice_input status"
        or command.startswith("/voice_input model ")
        or command == "/voice_input on"
        or command == "/voice_input off"
    ):
        send_voice_input_menu(token, chat_id, db, request_context=request_context)
        return True
    if command == "/voice_input language" or command.startswith("/voice_input language "):
        send_stt_language_menu(token, chat_id, db, request_context=request_context)
        return True
    if command.startswith("/voice_input "):
        send_text(token, chat_id, "Use /voice_input on, /voice_input off, or /voice_input status.")
        return True
    if command == "/note" or command.startswith("/note "):
        send_note_menu(token, chat_id, session.get("author_note") or "", request_context=request_context)
        return True
    return False


def _handle_panels(
    db,
    token,
    api_key,
    model,
    fields,
    chat_id,
    stripped,
    command,
    session,
    session_id,
    current_model,
    current_persona,
    operation_id,
    *,
    request_context,
    delivery_port,
    group_service,
    memory_service,
    persona_service,
    provider_port,
    sync_service,
    rag_service: RagService,
):
    """Dispatch generation, memory, voice, and panel-first commands."""
    if _handle_generation_panels(
        db,
        token,
        fields,
        chat_id,
        stripped,
        command,
        session,
        session_id,
        operation_id,
        delivery_port=delivery_port,
        provider_port=provider_port,
        memory_service=memory_service,
        request_context=request_context,
        persona_service=persona_service,
        rag_service=rag_service,
    ):
        return True
    if _handle_memory_media(
        db,
        token,
        api_key,
        chat_id,
        stripped,
        command,
        session,
        fields,
        operation_id,
        request_context=request_context,
        delivery_port=delivery_port,
        group_service=group_service,
        memory_service=memory_service,
        persona_service=persona_service,
        provider_port=provider_port,
        sync_service=sync_service,
        rag_service=rag_service,
    ):
        return True
    return _handle_voice_panels(db, token, chat_id, command, session, request_context=request_context)

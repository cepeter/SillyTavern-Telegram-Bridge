"""Handle onboarding, status, retry, and prompt inspection commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import bridge.command_panels as _command_panels
from bridge.card_content import active_world_files
from bridge.cards import send_character_menu, send_persona_menu, send_session_menu
from bridge.continuation import continue_last
from bridge.extension_registry import dispatch_command_routes as _dispatch_extension_command_routes
from bridge.failed_turns import clear_failed_turn, latest_failed_turn, record_failed_turn
from bridge.greetings import send_greeting_menu
from bridge.help_details import send_help_command, send_help_menu
from bridge.model_selection import task_model_for_session
from bridge.prompt_diagnostics import prompt_diagnostics
from bridge.prompt_panels import send_prompt_menu
from bridge.provider_panels import send_model_target_menu
from bridge.rag_service import RagService
from bridge.regeneration import regenerate_last
from bridge.response_delivery import send_reply
from bridge.session_core import list_sessions
from bridge.session_naming import start_session_name_input
from bridge.status_panels import status_text
from bridge.swipe_panels import send_swipe_menu
from bridge.system_prompt_panels import send_system_prompt_menu
from bridge.telegram import send_text
from bridge.text_action_input import handle_inline_text_action, start_text_action_input
from bridge.transcript_repository import committed_assistant_for_message
from bridge.world_panels import send_world_menu

if TYPE_CHECKING:
    pass


_START_MODEL_PLACEHOLDERS = frozenset(
    {
        "provider-one::provider-one/model-a",
        "example-provider::example-model",
    }
)


def _start_model_readiness_error(provider_port, api_key, current_model, session_id):
    selected_model = str(current_model or "").strip()
    if not selected_model or selected_model in _START_MODEL_PLACEHOLDERS:
        return "please set your model first in /providers command"
    try:
        provider_port.generate(
            api_key,
            selected_model,
            [{"role": "user", "content": "Reply OK."}],
            session_id=f"start-check:{session_id}",
            settings={"max_tokens": 8, "temperature": 0},
            force_non_stream=True,
            request_timeout=30,
        )
    except Exception as exc:
        detail = " ".join(str(exc).split()).strip() or type(exc).__name__
        return f"Model check failed: {detail[:800]}"
    return ""


def _handle_basic(
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
    user_name,
    operation_id,
    *,
    request_context,
    conversation_service,
    delivery_port,
    group_service,
    memory_service,
    provider_port,
):
    if command.startswith("/help "):
        send_help_command(
            token,
            chat_id,
            stripped,
            delivery_port=delivery_port,
            request_context=request_context,
        )
        return True
    if command in {"/start", "start"}:
        readiness_error = _start_model_readiness_error(provider_port, api_key, current_model, session_id)
        if readiness_error:
            send_text(token, chat_id, readiness_error)
            return True
    if command == "/start":
        persona_ready = bool(current_persona)
        world_ready = bool(
            active_world_files(session.get("world_file") or "", app_settings=request_context.app_settings)
        )
        system_prompt_ready = bool(session.get("system_prompt") or "")
        if persona_ready and world_ready and system_prompt_ready:
            send_greeting_menu(token, chat_id, fields, user_name, request_context=request_context)
        else:
            missing = []
            if not persona_ready:
                missing.append("Persona: off")
            if not world_ready:
                missing.append("World Info: off")
            if not system_prompt_ready:
                missing.append("System Prompt: off")
            send_text(
                token,
                chat_id,
                (
                    "Setup recommendation — Persona, World Info, and System Prompt are "
                    "optional. Current unavailable selections: "
                )
                + ", ".join(missing)
                + (
                    ". Use /persona, /world, or /systemprompt if you want to enable them. "
                    "Type `start` to choose the character greeting message."
                ),
            )
        return True
    if command == "start":
        send_greeting_menu(token, chat_id, fields, user_name, request_context=request_context)
        return True
    if command == "/help":
        send_help_menu(
            token,
            chat_id,
            delivery_port=delivery_port,
            request_context=request_context,
        )
        return True
    if command == "/new":
        start_session_name_input(
            db, token, chat_id, session, group_service=group_service, app_settings=request_context.app_settings
        )
        return True
    if command == "/status":
        send_text(
            token,
            chat_id,
            status_text(
                db,
                chat_id,
                session,
                fields,
                current_model,
                current_persona,
                group_service=group_service,
                app_settings=request_context.app_settings,
            ),
        )
        return True
    if command == "/retry":
        failed = latest_failed_turn(db, chat_id)
        if not failed:
            send_text(token, chat_id, "No failed turn is waiting for retry.")
            return True
        failed_message_id = int(failed[0])
        existing = committed_assistant_for_message(db, chat_id, failed_message_id)
        if existing:
            try:
                send_reply(
                    token,
                    chat_id,
                    str(existing[1]),
                    db,
                    session_id,
                    int(existing[0]),
                    app_settings=request_context.app_settings,
                )
                clear_failed_turn(db, chat_id, failed_message_id)
            except Exception as exc:
                logging.error("Retry delivery failed: %s", exc, exc_info=True)
                send_text(token, chat_id, "Retry delivery failed; the turn remains queued for /retry.")
            return True
        failed_session_id = str(failed[5] or "") or session_id
        try:
            conversation_service.process_message(
                db,
                token,
                api_key,
                str(failed[2]),
                fields,
                chat_id,
                str(failed[1]),
                failed_message_id,
                queued_session_id=failed_session_id,
                actor_id=request_context.actor_id,
            )
            clear_failed_turn(db, chat_id, failed_message_id)
        except Exception as exc:
            record_failed_turn(
                db, chat_id, failed_message_id, str(failed[1]), str(failed[2]), str(exc), failed_session_id
            )
            send_text(token, chat_id, "Retry failed again; the turn remains queued for /retry.")
        return True
    if command == "/prompt":
        send_prompt_menu(
            token,
            chat_id,
            db,
            session,
            fields,
            group_service=group_service,
            memory_service=memory_service,
            request_context=request_context,
        )
        return True
    if command == "/prompt text":
        send_text(
            token,
            chat_id,
            prompt_diagnostics(
                db,
                chat_id,
                session,
                fields,
                group_service=group_service,
                memory_service=memory_service,
                app_settings=request_context.app_settings,
            ),
        )
        return True
    return False


def _handle_entities(
    db,
    token,
    model,
    fields,
    chat_id,
    command,
    session,
    session_id,
    current_model,
    current_persona,
    *,
    request_context,
    persona_service,
):
    """Handle character, session, persona, world, prompt, and provider panels."""
    if command == "/systemprompt":
        send_system_prompt_menu(token, chat_id, session.get("system_prompt") or "", request_context=request_context)
        return True
    if command.startswith("/systemprompt "):
        send_text(
            token, chat_id, "System Prompt is panel-only. Use /systemprompt and choose a TXT file from the panel."
        )
        return True
    if command == "/character":
        send_character_menu(token, chat_id, session["character_file"], request_context=request_context)
        return True
    if command.startswith("/character "):
        send_text(
            token, chat_id, "Character management is panel-only. Use /character and choose Info, Delete, or Upload."
        )
        return True
    if command == "/session":
        send_session_menu(token, chat_id, list_sessions(db, chat_id), session_id, request_context=request_context)
        return True
    if command == "/persona" or command.startswith("/persona "):
        send_persona_menu(
            token, chat_id, current_persona, persona_service=persona_service, request_context=request_context
        )
        return True
    if command == "/world" or command.startswith("/world "):
        send_world_menu(token, chat_id, session["world_file"], request_context=request_context)
        return True
    if command == "/providers":
        send_model_target_menu(
            token,
            chat_id,
            current_model,
            task_model_for_session(db, chat_id, session, "utility", app_settings=request_context.app_settings),
            request_context=request_context,
        )
        return True
    if command.startswith("/providers "):
        send_text(token, chat_id, "Use /providers and choose an action from the panel.")
        return True
    return False


def _handle_chat(
    db,
    token,
    api_key,
    model,
    fields,
    chat_id,
    stripped,
    command,
    session,
    operation_id,
    *,
    request_context,
    delivery_port,
    memory_service,
    persona_service,
    provider_port,
    rag_service: RagService,
):
    """Handle edit, continuation, swipe, branch, and regeneration commands."""
    if command == "/edit":
        start_text_action_input(
            db, token, chat_id, session["session_id"], "edit", "Send the replacement text for the latest user message."
        )
        return True
    if command.startswith("/edit "):
        return handle_inline_text_action(
            db,
            token,
            api_key,
            chat_id,
            session,
            fields,
            "edit",
            stripped.split(None, 1)[1],
            operation_id,
            provider_port=provider_port,
            memory_service=memory_service,
            persona_service=persona_service,
            request_context=request_context,
            rag_service=rag_service,
        )
    if command == "/continue":
        continue_last(
            db,
            token,
            api_key,
            session,
            fields,
            chat_id,
            operation_id=operation_id,
            provider_port=provider_port,
            delivery_port=delivery_port,
            memory_service=memory_service,
            persona_service=persona_service,
            app_settings=request_context.app_settings,
            rag_service=rag_service,
        )
        return True
    if command == "/swipe" or command == "/branch" or command.startswith("/branch "):
        send_swipe_menu(
            token, db, chat_id, session["session_id"], delivery_port=delivery_port, request_context=request_context
        )
        return True
    if command == "/regen":
        regenerate_last(
            db,
            token,
            api_key,
            session,
            fields,
            chat_id,
            operation_id=operation_id,
            provider_port=provider_port,
            delivery_port=delivery_port,
            memory_service=memory_service,
            persona_service=persona_service,
            app_settings=request_context.app_settings,
            rag_service=rag_service,
        )
        return True
    return False


def handle_command_route(
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
    user_name,
    operation_id=None,
    *,
    request_context,
    conversation_service,
    delivery_port,
    group_service,
    memory_service,
    persona_service,
    provider_port,
    sync_service,
    rag_service: RagService,
):
    """Dispatch a normalized slash command without entering normal generation."""
    if _dispatch_extension_command_routes(
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
        user_name,
        operation_id=operation_id,
        request_context=request_context,
        delivery_port=delivery_port,
        provider_port=provider_port,
    ):
        return True
    if _handle_basic(
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
        user_name,
        operation_id,
        request_context=request_context,
        conversation_service=conversation_service,
        delivery_port=delivery_port,
        group_service=group_service,
        memory_service=memory_service,
        provider_port=provider_port,
    ):
        return True
    if _command_panels._handle_panels(
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
    if _handle_entities(
        db,
        token,
        model,
        fields,
        chat_id,
        command,
        session,
        session_id,
        current_model,
        current_persona,
        request_context=request_context,
        persona_service=persona_service,
    ):
        return True
    if _handle_chat(
        db,
        token,
        api_key,
        model,
        fields,
        chat_id,
        stripped,
        command,
        session,
        operation_id,
        request_context=request_context,
        delivery_port=delivery_port,
        memory_service=memory_service,
        persona_service=persona_service,
        provider_port=provider_port,
        rag_service=rag_service,
    ):
        return True
    if command.startswith("/"):
        send_text(token, chat_id, "Unknown or removed command. Use /help to see available commands.")
        return True
    return False

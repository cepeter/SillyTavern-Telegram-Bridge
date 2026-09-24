"""Handle onboarding, status, retry, and prompt inspection commands."""
from __future__ import annotations

from typing import TYPE_CHECKING

from bridge.extension_registry import dispatch_command_routes as _dispatch_extension_command_routes

if TYPE_CHECKING:
    from bridge.composition import BridgeServices


_START_MODEL_PLACEHOLDERS = frozenset({
    "provider-one::provider-one/model-a",
    "example-provider::example-model",
})


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


def _handle_basic(db, token, api_key, model, fields, chat_id, stripped, command, session, session_id, current_model, current_persona, user_name, operation_id, services, *, request_context):
    if command.startswith("/help "):
        send_help_command(request_context, token, chat_id, stripped)
        return True
    if command in {"/start", "start"}:
        readiness_error = _start_model_readiness_error(
            services.provider, api_key, current_model, session_id
        )
        if readiness_error:
            send_text(token, chat_id, readiness_error)
            return True
    if command == "/start":
        persona_ready = bool(current_persona)
        world_ready = bool(active_world_files(session.get("world_file") or ""))
        system_prompt_ready = bool(session.get("system_prompt") or "")
        if persona_ready and world_ready and system_prompt_ready:
            send_greeting_menu( token, chat_id, fields, user_name, request_context=request_context)
        else:
            missing = []
            if not persona_ready:
                missing.append("Persona: off")
            if not world_ready:
                missing.append("World Info: off")
            if not system_prompt_ready:
                missing.append("System Prompt: off")
            send_text(token, chat_id, "Setup recommendation — Persona, World Info, and System Prompt are optional. Current unavailable selections: " + ", ".join(missing) + ". Use /persona, /world, or /systemprompt if you want to enable them. Type `start` to choose the character greeting message.")
        return True
    if command == "start":
        send_greeting_menu( token, chat_id, fields, user_name, request_context=request_context)
        return True
    if command == "/help":
        send_help_menu( token, chat_id, request_context=request_context)
        return True
    if command == "/new":
        start_session_name_input(db, token, chat_id, session, group_service=services.group)
        return True
    if command == "/status":
        send_text(token, chat_id, status_text(db, chat_id, session, fields, current_model, current_persona, group_service=services.group))
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
                send_reply(token, chat_id, str(existing[1]), db, session_id, int(existing[0]))
                clear_failed_turn(db, chat_id, failed_message_id)
            except Exception as exc:
                logging.error("Retry delivery failed: %s", exc, exc_info=True)
                send_text(token, chat_id, "Retry delivery failed; the turn remains queued for /retry.")
            return True
        failed_session_id = str(failed[5] or "") or session_id
        try:
            services.conversation.process_message(
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
                services=services,
            )
            clear_failed_turn(db, chat_id, failed_message_id)
        except Exception as exc:
            record_failed_turn(db, chat_id, failed_message_id, str(failed[1]), str(failed[2]), str(exc), failed_session_id)
            send_text(token, chat_id, "Retry failed again; the turn remains queued for /retry.")
        return True
    memory_service = services.memory
    if command == "/prompt":
        send_prompt_menu(
            token,
            chat_id,
            db,
            session,
            fields,
            group_service=services.group, memory_service=memory_service, request_context=request_context
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
                group_service=services.group,
                memory_service=memory_service,
            ),
        )
        return True
    return False


def _handle_generation_panels(db, token, fields, chat_id, stripped, command, session, session_id, operation_id, *, delivery_port, provider_port, memory_service, persona_service, request_context):
    """Handle generation, preset, settings, and response-language panels."""
    if command == "/update":
        send_update_menu( token, chat_id, request_context=request_context)
        return True
    if command == "/imagine":
        start_text_action_input(db, token, chat_id, session_id, "imagine", "Send an image prompt (1–4,000 characters).")
        return True
    if command.startswith("/imagine "):
        try:
            handle_imagine_prompt(token, chat_id, stripped.split(None, 1)[1])
        except ValueError as exc:
            send_text(token, chat_id, f"Image generation unavailable: {exc}")
        return True
    if command == "/expression":
        send_expression_menu( token, chat_id, session, db, delivery_port=delivery_port, request_context=request_context)
        return True
    if command == "/stream" or command.startswith("/stream "):
        send_stream_menu( token, chat_id, db, request_context=request_context)
        return True
    if command == "/macro":
        start_text_action_input(db, token, chat_id, session_id, "macro", "Send text to preview with supported SillyTavern macros.")
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
        )
    if command == "/stscript" or command.startswith("/stscript "):
        send_stscript_menu( token, chat_id, request_context=request_context)
        return True
    if command == "/preset" or command in {"/preset list", "/preset use", "/preset delete"} or command.startswith("/preset "):
        send_preset_menu( token, chat_id, db, request_context=request_context)
        return True
    if command == "/settings" or command == "/settings reasoning" or command.startswith("/settings "):
        send_settings_menu( token, chat_id, db, session_id, request_context=request_context)
        return True
    if command == "/language" or command in {"/language list", "/language status"}:
        send_language_menu( token, chat_id, session.get("response_language") or "auto", delivery_port=delivery_port, request_context=request_context)
        return True
    if command.startswith("/language "):
        handle_language_command(db, token, chat_id, session, stripped, operation_id=operation_id, delivery_port=delivery_port, update_session=update_session, request_context=request_context)
        return True
    return False


def _handle_memory_media(db, token, api_key, chat_id, stripped, command, session, fields, operation_id, services, *, request_context):
    """Handle memory, RAG, group, and synchronization commands."""
    memory_service = services.memory
    persona_service = services.persona
    if command == "/memory" or command in {"/memory on", "/memory off", "/memory status", "/memory scope"}:
        send_memory_menu( token, chat_id, db, request_context=request_context)
        return True
    if command == "/memory search":
        send_memory_menu( token, chat_id, db, request_context=request_context)
        return True
    if command.startswith("/memory search "):
        handle_memory_command(db, token, chat_id, session, fields, stripped)
        return True
    if command.startswith("/memory "):
        send_memory_menu( token, chat_id, db, request_context=request_context)
        return True
    if command == "/remember":
        start_text_action_input(db, token, chat_id, session["session_id"], "remember", "Send the explicit memory fact to store in the active session.")
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
            provider_port=services.provider,
            memory_service=memory_service,
            persona_service=persona_service,
            request_context=request_context,
        )
    if command == "/summarize":
        send_summary_menu( token, chat_id, db, session, request_context=request_context)
        return True
    if command == "/databank" or command in {"/databank on", "/databank off", "/databank status", "/databank list", "/databank remove"}:
        send_databank_menu( token, chat_id, db, request_context=request_context)
        return True
    if command in {"/databank search", "/databank versions", "/databank activate", "/databank reindex"}:
        send_databank_menu( token, chat_id, db, request_context=request_context)
        return True
    if command.startswith("/databank search ") or command.startswith("/databank versions ") or command.startswith("/databank activate ") or command.startswith("/databank reindex ") or command.startswith("/databank remove "):
        handle_data_bank_command(db, token, chat_id, stripped)
        return True
    if command.startswith("/databank "):
        send_databank_menu( token, chat_id, db, request_context=request_context)
        return True
    if command == "/sync":
        send_sync_menu(
            token,
            chat_id,
            db,
            session,
            sync_service=services.sync, request_context=request_context
        )
        return True
    if command == "/group":
        if parse_topic_scope(chat_id)[1] is None:
            send_text(token, chat_id, "Group sessions are available only inside a Telegram Forum Topic.")
        else:
            send_group_menu( db, token, chat_id, session, group_service=services.group, request_context=request_context)
        return True
    if command.startswith("/group "):
        if parse_topic_scope(chat_id)[1] is None:
            send_text(token, chat_id, "Group sessions are available only inside a Telegram Forum Topic.")
        else:
            handle_group_command(db, token, chat_id, session, stripped, operation_id, group_service=services.group)
        return True

    return False


def _handle_voice_panels(db, token, chat_id, command, session, *, request_context):
    """Handle automatic voice, STT, and Author's Note panels."""
    if command == "/voice" or command in {"/voice status", "/voice on", "/voice off", "/voice tts"} or command.startswith("/voice "):
        send_voice_menu( token, chat_id, db, request_context=request_context)
        return True
    if command == "/voice_input" or command == "/voice_input status" or command.startswith("/voice_input model ") or command == "/voice_input on" or command == "/voice_input off":
        send_voice_input_menu( token, chat_id, db, request_context=request_context)
        return True
    if command == "/voice_input language" or command.startswith("/voice_input language "):
        send_stt_language_menu( token, chat_id, db, request_context=request_context)
        return True
    if command.startswith("/voice_input "):
        send_text(token, chat_id, "Use /voice_input on, /voice_input off, or /voice_input status.")
        return True
    if command == "/note" or command.startswith("/note "):
        send_note_menu( token, chat_id, session.get("author_note") or "", request_context=request_context)
        return True
    return False


def _handle_panels(db, token, api_key, model, fields, chat_id, stripped, command, session, session_id, current_model, current_persona, operation_id, services, *, request_context):
    """Dispatch generation, memory, voice, and panel-first commands."""
    memory_service = services.memory
    persona_service = services.persona
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
        delivery_port=services.delivery,
        provider_port=services.provider,
        memory_service=memory_service,
        request_context=request_context,
        persona_service=persona_service,
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
        services,
        request_context=request_context,
    ):
        return True
    return _handle_voice_panels(db, token, chat_id, command, session, request_context=request_context)


def _handle_entities(db, token, model, fields, chat_id, command, session, session_id, current_model, current_persona, services, *, request_context):
    """Handle character, session, persona, world, prompt, and provider panels."""
    if command == "/systemprompt":
        send_system_prompt_menu( token, chat_id, session.get("system_prompt") or "", request_context=request_context)
        return True
    if command.startswith("/systemprompt "):
        send_text(token, chat_id, "System Prompt is panel-only. Use /systemprompt and choose a TXT file from the panel.")
        return True
    if command == "/character":
        send_character_menu( token, chat_id, session["character_file"], request_context=request_context)
        return True
    if command.startswith("/character "):
        send_text(token, chat_id, "Character management is panel-only. Use /character and choose Info, Delete, or Upload.")
        return True
    if command == "/session":
        send_session_menu( token, chat_id, list_sessions(db, chat_id), session_id, request_context=request_context)
        return True
    if command == "/persona" or command.startswith("/persona "):
        send_persona_menu(
            token,
            chat_id,
            current_persona,
            persona_service=services.persona, request_context=request_context
        )
        return True
    if command == "/world" or command.startswith("/world "):
        send_world_menu( token, chat_id, session["world_file"], request_context=request_context)
        return True
    if command in {"/providers", "/providers health", "/providers refresh"}:
        send_model_target_menu( token, chat_id, current_model, task_model_for_session(db, chat_id, session, "utility"), request_context=request_context)
        return True
    if command.startswith("/providers "):
        send_text(token, chat_id, "Unknown /providers action. Use /providers, /providers health, or /providers refresh.")
        return True
    return False


def _handle_chat(db, token, api_key, model, fields, chat_id, stripped, command, session, operation_id, services, *, request_context):
    """Handle edit, continuation, swipe, branch, and regeneration commands."""
    if command == "/edit":
        start_text_action_input(db, token, chat_id, session["session_id"], "edit", "Send the replacement text for the latest user message.")
        return True
    memory_service = services.memory
    persona_service = services.persona
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
            provider_port=services.provider,
            memory_service=memory_service,
            persona_service=persona_service,
            request_context=request_context,
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
            provider_port=services.provider,
            delivery_port=services.delivery,
            memory_service=memory_service,
            persona_service=persona_service,
        )
        return True
    if command == "/swipe" or command == "/branch" or command.startswith("/branch "):
        send_swipe_menu( token, db, chat_id, session["session_id"], delivery_port=services.delivery, request_context=request_context)
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
            provider_port=services.provider,
            delivery_port=services.delivery,
            memory_service=memory_service,
            persona_service=persona_service,
        )
        return True
    return False


def handle_command_route(db, token, api_key, model, fields, chat_id, stripped, command, session, session_id, current_model, current_persona, user_name, operation_id=None, *, request_context, services: BridgeServices):
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
        services=services,
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
        services,
        request_context=request_context,
    ):
        return True
    if _handle_panels(
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
        services,
        request_context=request_context,
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
        services,
        request_context=request_context,
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
        services,
        request_context=request_context,
    ):
        return True
    if command.startswith("/"):
        send_text(token, chat_id, "Unknown or removed command. Use /help to see available commands.")
        return True
    return False


# Explicit late imports replace transitional dependency injection.
import logging
from bridge.card_content import active_world_files
from bridge.cards import (
    send_character_menu,
    send_persona_menu,
    send_session_menu,
)
from bridge.catalog import (
    send_model_target_menu,
    send_world_menu,
)
from bridge.commands import (
    prompt_diagnostics,
    send_note_menu,
    send_stscript_menu,
)
from bridge.common import parse_topic_scope
from bridge.database import (
    clear_failed_turn,
    committed_assistant_for_message,
    latest_failed_turn,
    record_failed_turn,
    task_model_for_session,
)
from bridge.expressions import send_expression_menu
from bridge.generation import (
    continue_last,
    regenerate_last,
    send_swipe_menu,
)
from bridge.greetings import send_greeting_menu
from bridge.groups import (
    handle_group_command,
    send_group_menu,
)
from bridge.help import (
    send_databank_menu,
    send_help_menu,
    send_memory_menu,
    send_preset_menu,
    send_settings_menu,
    send_stream_menu,
    send_stt_language_menu,
    send_system_prompt_menu,
    send_voice_input_menu,
    send_voice_menu,
)
from bridge.help_details import send_help_command
from bridge.image_generation import handle_imagine_prompt
from bridge.input_flows import (
    handle_inline_text_action,
    start_text_action_input,
)
from bridge.language import (
    handle_language_command,
    send_language_menu,
)
from bridge.media import send_reply
from bridge.memory import handle_memory_command
from bridge.rag import handle_data_bank_command
from bridge.session_naming import start_session_name_input
from bridge.status_panels import (
    send_prompt_menu,
    send_summary_menu,
    send_sync_menu,
    status_text,
)
from bridge.telegram import (
    list_sessions,
    send_text,
    update_session,
)
from bridge.update import send_update_menu

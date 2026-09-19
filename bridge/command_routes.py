from bridge.extension_registry import dispatch_command_routes as _dispatch_extension_command_routes

def _handle_basic(db, token, api_key, model, fields, chat_id, stripped, command, session, session_id, current_model, current_persona, user_name, operation_id):
    """Handle onboarding, status, retry, and prompt inspection commands."""
    if command.startswith("/help "):
        send_help_command(token, chat_id, stripped)
        return True
    if command == "/start":
        persona_ready = bool(current_persona)
        world_ready = bool(active_world_files(session.get("world_file") or ""))
        system_prompt_ready = bool(session.get("system_prompt") or "")
        first_start = db.execute("SELECT 1 FROM messages WHERE chat_id=? AND session_id=? LIMIT 1", (chat_id, session_id)).fetchone() is None
        if persona_ready and world_ready and system_prompt_ready:
            send_character_greeting(db, token, chat_id, fields, session_id, user_name, None if first_start else 0, operation_id, "start_greeting")
        else:
            missing = []
            if not persona_ready:
                missing.append("Persona: off")
            if not world_ready:
                missing.append("World Info: off")
            if not system_prompt_ready:
                missing.append("System Prompt: off")
            send_text(token, chat_id, "Setup recommendation — Persona, World Info, and System Prompt are optional. Current unavailable selections: " + ", ".join(missing) + ". Use /persona, /world, or /systemprompt if you want to enable them. Type `start` to show the character greeting message.")
        return True
    if command == "start":
        first_start = db.execute("SELECT 1 FROM messages WHERE chat_id=? AND session_id=? LIMIT 1", (chat_id, session_id)).fetchone() is None
        send_character_greeting(db, token, chat_id, fields, session_id, user_name, None if first_start else 0, operation_id, "start_greeting")
        return True
    if command == "/help":
        send_help_menu(token, chat_id)
        return True
    if command == "/new":
        start_session_name_input(db, token, chat_id, session)
        return True
    if command == "/status":
        send_text(token, chat_id, status_text(db, chat_id, session, fields, current_model, current_persona))
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
            process_message(db, token, api_key, str(failed[2]), fields, chat_id, str(failed[1]), failed_message_id, queued_session_id=failed_session_id)
            clear_failed_turn(db, chat_id, failed_message_id)
        except Exception as exc:
            record_failed_turn(db, chat_id, failed_message_id, str(failed[1]), str(failed[2]), str(exc), failed_session_id)
            send_text(token, chat_id, "Retry failed again; the turn remains queued for /retry.")
        return True
    if command == "/prompt":
        send_prompt_menu(token, chat_id, db, session, fields)
        return True
    if command == "/prompt text":
        send_text(token, chat_id, prompt_diagnostics(db, chat_id, session, fields))
        return True
    return False


def _handle_generation_panels(db, token, fields, chat_id, stripped, command, session, session_id, operation_id):
    """Handle generation, preset, settings, and response-language panels."""
    if command == "/update":
        send_update_menu(token, chat_id)
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
        send_expression_menu(token, chat_id, session, db)
        return True
    if command == "/stream" or command.startswith("/stream "):
        send_stream_menu(token, chat_id, db)
        return True
    if command == "/macro":
        start_text_action_input(db, token, chat_id, session_id, "macro", "Send text to preview with supported SillyTavern macros.")
        return True
    if command.startswith("/macro "):
        return handle_inline_text_action(db, token, "", chat_id, session, fields, "macro", stripped.split(None, 1)[1], operation_id)
    if command == "/stscript" or command.startswith("/stscript "):
        send_stscript_menu(token, chat_id)
        return True
    if command == "/preset" or command in {"/preset list", "/preset use", "/preset delete"} or command.startswith("/preset "):
        send_preset_menu(token, chat_id, db)
        return True
    if command == "/settings" or command == "/settings reasoning" or command.startswith("/settings "):
        send_settings_menu(token, chat_id, db, session_id)
        return True
    if command == "/language" or command in {"/language list", "/language status"}:
        send_language_menu(token, chat_id, session.get("response_language") or "auto")
        return True
    if command.startswith("/language "):
        handle_language_command(db, token, chat_id, session, stripped, operation_id=operation_id)
        return True
    return False


def _handle_memory_media(db, token, api_key, chat_id, stripped, command, session, fields, operation_id):
    """Handle memory, RAG, group, and synchronization commands."""
    if command == "/memory" or command in {"/memory on", "/memory off", "/memory status", "/memory scope"}:
        send_memory_menu(token, chat_id, db)
        return True
    if command == "/memory search":
        send_memory_menu(token, chat_id, db)
        return True
    if command.startswith("/memory search "):
        handle_memory_command(db, token, chat_id, session, fields, stripped)
        return True
    if command.startswith("/memory "):
        send_memory_menu(token, chat_id, db)
        return True
    if command == "/remember":
        start_text_action_input(db, token, chat_id, session["session_id"], "remember", "Send the explicit memory fact to store in the active session.")
        return True
    if command.startswith("/remember "):
        return handle_inline_text_action(db, token, api_key, chat_id, session, fields, "remember", stripped.split(None, 1)[1], operation_id)
    if command == "/summarize":
        send_summary_menu(token, chat_id, db, session)
        return True
    if command == "/databank" or command in {"/databank on", "/databank off", "/databank status", "/databank list", "/databank remove"}:
        send_databank_menu(token, chat_id, db)
        return True
    if command in {"/databank search", "/databank versions", "/databank activate", "/databank reindex"}:
        send_databank_menu(token, chat_id, db)
        return True
    if command.startswith("/databank search ") or command.startswith("/databank versions ") or command.startswith("/databank activate ") or command.startswith("/databank reindex ") or command.startswith("/databank remove "):
        handle_data_bank_command(db, token, chat_id, stripped)
        return True
    if command.startswith("/databank "):
        send_databank_menu(token, chat_id, db)
        return True
    if command == "/sync":
        send_sync_menu(token, chat_id, db, session)
        return True
    if command == "/group":
        if parse_topic_scope(chat_id)[1] is None:
            send_text(token, chat_id, "Group sessions are available only inside a Telegram Forum Topic.")
        else:
            send_group_menu(db, token, chat_id, session)
        return True
    if command.startswith("/group "):
        if parse_topic_scope(chat_id)[1] is None:
            send_text(token, chat_id, "Group sessions are available only inside a Telegram Forum Topic.")
        else:
            handle_group_command(db, token, chat_id, session, stripped, operation_id)
        return True

    return False


def _handle_voice_panels(db, token, chat_id, command, session):
    """Handle automatic voice, STT, and Author's Note panels."""
    if command == "/voice" or command in {"/voice status", "/voice on", "/voice off", "/voice tts"} or command.startswith("/voice "):
        send_voice_menu(token, chat_id, db)
        return True
    if command == "/voice_input" or command == "/voice_input status" or command.startswith("/voice_input model ") or command == "/voice_input on" or command == "/voice_input off":
        send_voice_input_menu(token, chat_id, db)
        return True
    if command == "/voice_input language" or command.startswith("/voice_input language "):
        send_stt_language_menu(token, chat_id, db)
        return True
    if command.startswith("/voice_input "):
        send_text(token, chat_id, "Use /voice_input on, /voice_input off, or /voice_input status.")
        return True
    if command == "/note" or command.startswith("/note "):
        send_note_menu(token, chat_id, session.get("author_note") or "")
        return True
    return False


def _handle_panels(db, token, api_key, model, fields, chat_id, stripped, command, session, session_id, current_model, current_persona, operation_id):
    """Dispatch generation, memory, voice, and panel-first commands."""
    if _handle_generation_panels(db, token, fields, chat_id, stripped, command, session, session_id, operation_id):
        return True
    if _handle_memory_media(db, token, api_key, chat_id, stripped, command, session, fields, operation_id):
        return True
    return _handle_voice_panels(db, token, chat_id, command, session)


def _handle_entities(db, token, model, fields, chat_id, command, session, session_id, current_model, current_persona):
    """Handle character, session, persona, world, prompt, and provider panels."""
    if command == "/systemprompt":
        send_system_prompt_menu(token, chat_id, session.get("system_prompt") or "")
        return True
    if command.startswith("/systemprompt "):
        send_text(token, chat_id, "System Prompt is panel-only. Use /systemprompt and choose a TXT file from the panel.")
        return True
    if command == "/character":
        send_character_menu(token, chat_id, session["character_file"])
        return True
    if command.startswith("/character "):
        send_text(token, chat_id, "Character management is panel-only. Use /character and choose Info, Delete, or Upload.")
        return True
    if command == "/session":
        send_session_menu(token, chat_id, list_sessions(db, chat_id), session_id)
        return True
    if command == "/persona" or command.startswith("/persona "):
        send_persona_menu(token, chat_id, current_persona)
        return True
    if command == "/world" or command.startswith("/world "):
        send_world_menu(token, chat_id, session["world_file"])
        return True
    if command in {"/providers", "/providers health", "/providers refresh"}:
        send_model_target_menu(token, chat_id, current_model, task_model_for_session(db, chat_id, session, "utility"))
        return True
    if command.startswith("/providers "):
        send_text(token, chat_id, "Unknown /providers action. Use /providers, /providers health, or /providers refresh.")
        return True
    return False


def _handle_chat(db, token, api_key, model, fields, chat_id, stripped, command, session, operation_id):
    """Handle edit, continuation, swipe, branch, and regeneration commands."""
    if command == "/edit":
        start_text_action_input(db, token, chat_id, session["session_id"], "edit", "Send the replacement text for the latest user message.")
        return True
    if command.startswith("/edit "):
        return handle_inline_text_action(db, token, api_key, chat_id, session, fields, "edit", stripped.split(None, 1)[1], operation_id)
    if command == "/continue":
        continue_last(db, token, api_key, session, fields, chat_id, operation_id=operation_id)
        return True
    if command == "/swipe" or command == "/branch" or command.startswith("/branch "):
        send_swipe_menu(token, db, chat_id, session["session_id"])
        return True
    if command == "/regen":
        regenerate_last(db, token, api_key, session, fields, chat_id, operation_id=operation_id)
        return True
    return False


def handle_command_route(db, token, api_key, model, fields, chat_id, stripped, command, session, session_id, current_model, current_persona, user_name, operation_id=None):
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
    ):
        return True
    if _handle_basic(db, token, api_key, model, fields, chat_id, stripped, command, session, session_id, current_model, current_persona, user_name, operation_id):
        return True
    if _handle_panels(db, token, api_key, model, fields, chat_id, stripped, command, session, session_id, current_model, current_persona, operation_id):
        return True
    if _handle_entities(db, token, model, fields, chat_id, command, session, session_id, current_model, current_persona):
        return True
    if _handle_chat(db, token, api_key, model, fields, chat_id, stripped, command, session, operation_id):
        return True
    if command.startswith("/"):
        send_text(token, chat_id, "Unknown or removed command. Use /help to see available commands.")
        return True
    return False

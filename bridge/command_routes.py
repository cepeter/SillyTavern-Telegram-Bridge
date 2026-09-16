def _handle_basic(db, token, api_key, model, fields, chat_id, stripped, command, session, session_id, current_model, current_persona, user_name, operation_id):
    """Handle onboarding, status, retry, and prompt inspection commands."""
    if command.startswith("/help "):
        requested = command.split(None, 1)[1].strip().lstrip("/")
        for _category, entries in HELP_CATEGORIES.items():
            match = next((description for command_name, description in entries if command_name.split()[0].lstrip("/") == requested), None)
            if match:
                send_text(token, chat_id, f"/{requested} — {match}")
                return True
        send_help_menu(token, chat_id)
        return True
    if command == "/start":
        if operation_id is not None and (operation_was_applied(db, operation_id) or not begin_operation(db, operation_id, "start")):
            return True
        greeting = replace_macros(fields.get("first_mes") or "", fields, user_name).strip()
        if greeting:
            message_ids = send_text(token, chat_id, greeting)
            db.execute("INSERT INTO messages(chat_id,session_id,role,content,telegram_message_ids,created_at) VALUES(?,?,?,?,?,?)", (chat_id, session_id, "assistant", greeting, json.dumps(message_ids), time.time()))
            if operation_id is not None:
                record_operation(db, operation_id, "start")
            db.commit()
        return True
    if command == "/help":
        send_help_menu(token, chat_id)
        return True
    if command == "/new":
        new_session = create_session(db, chat_id, model, session_id=f"job-{operation_id}" if operation_id is not None else None)
        send_text(token, chat_id, f"New session started: {new_session['session_id']}")
        return True
    if command == "/status":
        count = db.execute("SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session_id)).fetchone()[0]
        worlds = active_world_files(session["world_file"])
        world = ", ".join(Path(name).stem for name in worlds) if worlds else "off"
        persona = persona_name(current_persona) if current_persona else "off"
        note_state = "on" if session["author_note"] else "off"
        generation = get_generation_settings(db, chat_id, session_id)
        summary, covered_until = get_session_summary(db, chat_id, session_id)
        summary_state = f"on (through message {covered_until})" if summary else "off"
        rag_docs = data_bank_documents(db, chat_id)
        group = group_state(db, chat_id, session_id)
        group_labels = group_member_labels(group["members"])
        group_state_text = f"{'on' if group['enabled'] else 'off'} ({', '.join(group_labels) if group_labels else 'none'})"
        send_text(token, chat_id, f"Character: {fields['name']}\nSession: {session_id}\nStored messages: {count}\nModel: {current_model}\nResponse language: {response_language_label(session.get('response_language') or 'auto')}\nPersona: {persona}\nWorld Info: {world}\nAuthor's Note: {note_state}\nSummary: {summary_state}\nHindsight: {memory_mode(db, chat_id)} ({memory_scope(db, chat_id)})\nData Bank RAG: {rag_mode(db, chat_id)} ({len(rag_docs)} documents)\nGroup chat: {group_state_text}\nGeneration: temperature={generation['temperature']}, max_tokens={generation['max_tokens']}, top_p={generation['top_p']}")
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
        send_text(token, chat_id, prompt_diagnostics(db, chat_id, session, fields))
        return True
    return False


def _handle_generation_panels(db, token, fields, chat_id, stripped, command, session, session_id, operation_id):
    """Handle generation, preset, settings, and response-language panels."""
    if command == "/stream" or command.startswith("/stream "):
        send_stream_menu(token, chat_id, db)
        return True
    if command == "/macro" or command.startswith("/macro ") or command == "/stscript" or command.startswith("/stscript "):
        handle_macro_command(db, token, chat_id, session, fields, stripped)
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
    """Handle memory, RAG, group, export, import, and TTS commands."""
    if command == "/memory" or command in {"/memory on", "/memory off", "/memory status"}:
        send_memory_menu(token, chat_id, db)
        return True
    if command == "/memory scope":
        send_memory_scope_menu(token, chat_id, db)
        return True
    if command.startswith("/memory search"):
        handle_memory_command(db, token, chat_id, session, fields, stripped)
        return True
    if command.startswith("/memory "):
        send_memory_menu(token, chat_id, db)
        return True
    if command == "/remember":
        send_text(token, chat_id, "Use /remember <fact> to store an explicit long-term memory.")
        return True
    if command.startswith("/remember "):
        fact = stripped.split(None, 1)[1].strip()
        if len(fact) > 4000:
            send_text(token, chat_id, "Explicit memory is limited to 4,000 characters.")
        elif remember_fact(db, chat_id, session, fields, fact):
            send_text(token, chat_id, "Memory queued for Hindsight.")
        else:
            send_text(token, chat_id, "Hindsight memory is unavailable.")
        return True
    if command == "/summarize":
        handle_summary_command(db, token, chat_id, session)
        return True
    if command == "/databank" or command in {"/databank on", "/databank off", "/databank status", "/databank list", "/databank remove"}:
        send_databank_menu(token, chat_id, db)
        return True
    if command.startswith("/databank search"):
        handle_data_bank_command(db, token, chat_id, stripped)
        return True
    if command.startswith("/databank "):
        send_databank_menu(token, chat_id, db)
        return True
    if command == "/sync":
        send_sync_menu(token, chat_id, db, session)
        return True
    if command == "/group" or command.startswith("/group "):
        if parse_topic_scope(chat_id)[1] is None:
            send_text(token, chat_id, "Group sessions are available only inside a Telegram Forum Topic.")
        else:
            send_group_menu(db, token, chat_id, session)
        return True

    if command == "/tts":
        send_text(token, chat_id, "Gunakan /tts <teks> untuk mengubah teks menjadi voice message.")
        return True
    if command.startswith("/tts "):
        tts_text = stripped.split(None, 1)[1].strip()
        if not tts_text:
            send_text(token, chat_id, "Teks TTS tidak boleh kosong.")
        elif len(tts_text) > TTS_MAX_CHARS:
            send_text(token, chat_id, f"Teks TTS dibatasi {TTS_MAX_CHARS} karakter.")
        elif not send_tts(token, chat_id, tts_text, operation_id=operation_id):
            send_text(token, chat_id, "TTS gagal dibuat. Periksa koneksi atau konfigurasi edge-tts.")
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
        send_model_menu(token, chat_id, current_model)
        return True
    if command.startswith("/providers "):
        send_text(token, chat_id, "Unknown /providers action. Use /providers, /providers health, or /providers refresh.")
        return True
    return False


def _handle_chat(db, token, api_key, model, fields, chat_id, stripped, command, session, operation_id):
    """Handle edit, continuation, swipe, branch, and regeneration commands."""
    if command == "/edit":
        send_text(token, chat_id, "Gunakan /edit <teks baru> untuk mengedit pesan user terakhir.")
        return True
    if command.startswith("/edit "):
        new_text = stripped.split(None, 1)[1].strip()
        if not new_text:
            send_text(token, chat_id, "Teks edit tidak boleh kosong.")
        elif len(new_text) > 12000:
            send_text(token, chat_id, "Teks edit dibatasi 12.000 karakter.")
        else:
            edit_last_user(db, token, api_key, session, fields, chat_id, new_text, operation_id=operation_id)
        return True
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

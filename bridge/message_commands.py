def process_message(db: sqlite3.Connection, token: str, api_key: str, model: str, fields: dict, chat_id: str, text: str, telegram_message_id: int | None = None, queued_session_id: str | None = None, operation_id: int | None = None) -> None:
    stripped = text.strip()
    command = stripped.lower()
    command_parts = command.split(None, 1)
    if command_parts and command_parts[0].startswith("@") and len(command_parts) > 1 and command_parts[1].startswith("/"):
        command_parts = command_parts[1].split(None, 1)
    if command_parts and command_parts[0].startswith("/") and "@" in command_parts[0]:
        command_parts[0] = command_parts[0].split("@", 1)[0]
    if len(command_parts) > 1 and command_parts[1].startswith("@"):
        command_parts = [command_parts[0]]
    command = " ".join(command_parts)
    session = load_session(db, chat_id, queued_session_id, model) if queued_session_id else ensure_session(db, chat_id, model)
    session_id = session["session_id"]
    set_panel_session_context(session_id)
    if operation_id is not None and operation_phase(db, operation_id) == "local_committed":
        committed = db.execute("SELECT rowid,content FROM messages WHERE chat_id=? AND session_id=? AND role='assistant' ORDER BY rowid DESC LIMIT 1", (chat_id, session_id)).fetchone()
        if committed:
            send_reply(token, chat_id, str(committed[1]), db, session_id, int(committed[0]))
            set_operation_phase(db, operation_id, "recovery_delivery", "external_delivered")
            record_operation(db, operation_id, "recovery_delivery")
            db.commit()
            return
    fields = card_fields_from_file(session["character_file"])
    pending_setting = get_meta(db, f"settings_input:{chat_id}", "")
    try:
        pending_state = json.loads(pending_setting) if pending_setting else {}
    except json.JSONDecodeError:
        pending_state = {"key": pending_setting}
    if pending_state and (pending_state.get("session_id") not in {None, "", session_id} or float(pending_state.get("expires_at", 0) or 0) < time.time()):
        set_meta(db, f"settings_input:{chat_id}", "")
        pending_state = {}
    pending_setting = str(pending_state.get("key") or "")
    if pending_setting:
        if stripped.casefold() in {"/cancel", "cancel"}:
            set_meta(db, f"settings_input:{chat_id}", "")
            send_settings_menu(token, chat_id, db, session_id)
            return
        try:
            key, value = parse_generation_setting(pending_setting, stripped)
            update_generation_settings(db, chat_id, session_id, **{key: value})
        except ValueError as exc:
            send_text(token, chat_id, f"Invalid {pending_setting}: {exc} Try again or send /cancel.")
            return
        set_meta(db, f"settings_input:{chat_id}", "")
        send_settings_menu(token, chat_id, db, session_id)
        return
    group_turn = group_current_speaker(db, chat_id, session, text)
    group_context = ""
    if group_turn:
        fields = card_fields_from_file(group_turn[0])
        group_context = group_prompt_context(db, chat_id, session, group_turn[0])
    current_model = session["model_id"] or model
    current_persona = session["persona_id"]
    user_name = persona_name(current_persona) if current_persona else "Punto"
    if command.startswith("/help "):
        requested = command.split(None, 1)[1].strip().lstrip("/")
        for category, entries in HELP_CATEGORIES.items():
            match = next((description for command_name, description in entries if command_name.split()[0].lstrip("/") == requested), None)
            if match:
                send_text(token, chat_id, f"/{requested} — {match}")
                return
        send_help_menu(token, chat_id)
        return
    if command == "/start":
        if operation_id is not None:
            if operation_was_applied(db, operation_id) or not begin_operation(db, operation_id, "start"):
                return
        greeting = replace_macros(fields.get("first_mes") or "", fields, user_name).strip()
        if greeting:
            message_ids = send_text(token, chat_id, greeting)
            db.execute("INSERT INTO messages(chat_id,session_id,role,content,telegram_message_ids,created_at) VALUES(?,?,?,?,?,?)", (chat_id, session_id, "assistant", greeting, json.dumps(message_ids), time.time()))
            if operation_id is not None:
                record_operation(db, operation_id, "start")
            db.commit()
        return
    if command == "/help":
        send_help_menu(token, chat_id)
        return
    if command == "/new":
        session_seed = f"job-{operation_id}" if operation_id is not None else None
        session = create_session(db, chat_id, model, session_id=session_seed)
        send_text(token, chat_id, f"New session started: {session['session_id']}")
        return
    if command == "/reset":
        db.execute("DELETE FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session_id))
        db.execute("DELETE FROM response_variants WHERE chat_id=? AND session_id=?", (chat_id, session_id))
        clear_session_summary(db, chat_id, session_id)
        db.commit()
        send_text(token, chat_id, "Current session reset.")
        return
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
        send_text(token, chat_id, f"Character: {fields['name']}\nSession: {session_id}\nStored messages: {count}\nModel: {current_model}\nPersona: {persona}\nWorld Info: {world}\nAuthor's Note: {note_state}\nSummary: {summary_state}\nHindsight: {memory_mode(db, chat_id)} ({memory_scope(db, chat_id)})\nData Bank RAG: {rag_mode(db, chat_id)} ({len(rag_docs)} documents)\nGroup chat: {group_state_text}\nGeneration: temperature={generation['temperature']}, max_tokens={generation['max_tokens']}, top_p={generation['top_p']}")
        return
    if command == "/retry":
        failed = latest_failed_turn(db, chat_id)
        if not failed:
            send_text(token, chat_id, "No failed turn is waiting for retry.")
            return
        failed_message_id = int(failed[0])
        existing = committed_assistant_for_message(db, chat_id, failed_message_id)
        if existing:
            try:
                send_reply(token, chat_id, str(existing[1]), db, session_id, int(existing[0]))
                clear_failed_turn(db, chat_id, failed_message_id)
            except Exception as exc:
                logging.error("Retry delivery failed: %s", exc, exc_info=True)
                send_text(token, chat_id, "Retry delivery failed; the turn remains queued for /retry.")
            return
        failed_session_id = str(failed[5] or "") or session_id
        try:
            process_message(db, token, api_key, str(failed[2]), fields, chat_id, str(failed[1]), failed_message_id, queued_session_id=failed_session_id)
            clear_failed_turn(db, chat_id, failed_message_id)
        except Exception as exc:
            record_failed_turn(db, chat_id, failed_message_id, str(failed[1]), str(failed[2]), str(exc), failed_session_id)
            send_text(token, chat_id, "Retry failed again; the turn remains queued for /retry.")
        return
    if command == "/prompt":
        send_text(token, chat_id, prompt_diagnostics(db, chat_id, session, fields))
        return
    if command == "/stream" or command.startswith("/stream "):
        send_stream_menu(token, chat_id, db)
        return
    if command == "/macro" or command.startswith("/macro ") or command == "/stscript" or command.startswith("/stscript "):
        handle_macro_command(db, token, chat_id, session, fields, stripped)
        return
    if command == "/preset" or command in {"/preset list", "/preset use", "/preset delete"}:
        send_preset_menu(token, chat_id, db)
        return
    if command.startswith("/preset "):
        handle_preset_command(db, token, chat_id, session_id, stripped)
        return
    if command == "/settings" or command == "/settings reasoning":
        send_settings_menu(token, chat_id, db, session_id)
        return
    if command.startswith("/settings ") and not command.startswith("/settings reasoning "):
        handle_generation_settings(db, token, chat_id, session_id, stripped)
        return
    if command == "/memory" or command in {"/memory on", "/memory off", "/memory status"}:
        send_memory_menu(token, chat_id, db)
        return
    if command == "/memory scope":
        send_memory_scope_menu(token, chat_id, db)
        return
    if command.startswith("/memory search"):
        handle_memory_command(db, token, chat_id, session, fields, stripped)
        return
    if command.startswith("/memory "):
        send_memory_menu(token, chat_id, db)
        return
    if command == "/remember":
        send_text(token, chat_id, "Use /remember <fact> to store an explicit long-term memory.")
        return
    if command.startswith("/remember "):
        fact = stripped.split(None, 1)[1].strip()
        if len(fact) > 4000:
            send_text(token, chat_id, "Explicit memory is limited to 4,000 characters.")
        elif remember_fact(db, chat_id, session, fields, fact):
            send_text(token, chat_id, "Memory queued for Hindsight.")
        else:
            send_text(token, chat_id, "Hindsight memory is unavailable.")
        return
    if command == "/summarize":
        handle_summary_command(db, token, chat_id, session)
        return
    if command == "/databank" or command in {"/databank on", "/databank off", "/databank status", "/databank list", "/databank remove"}:
        send_databank_menu(token, chat_id, db)
        return
    if command.startswith("/databank search"):
        handle_data_bank_command(db, token, chat_id, stripped)
        return
    if command.startswith("/databank "):
        send_databank_menu(token, chat_id, db)
        return
    if command == "/group" or command.startswith("/group "):
        send_group_menu(db, token, chat_id, session)
        return
    if command == "/export":
        export_session(token, db, session, fields, chat_id)
        return
    if command == "/import":
        send_text(token, chat_id, "Kirim file export SillyTavern berformat .jsonl ke chat ini.")
        return
    if command == "/tts":
        send_text(token, chat_id, "Gunakan /tts <teks> untuk mengubah teks menjadi voice message.")
        return
    if command.startswith("/tts "):
        tts_text = stripped.split(None, 1)[1].strip()
        if not tts_text:
            send_text(token, chat_id, "Teks TTS tidak boleh kosong.")
        elif len(tts_text) > TTS_MAX_CHARS:
            send_text(token, chat_id, f"Teks TTS dibatasi {TTS_MAX_CHARS} karakter.")
        elif not send_tts(token, chat_id, tts_text, operation_id=operation_id):
            send_text(token, chat_id, "TTS gagal dibuat. Periksa koneksi atau konfigurasi edge-tts.")
        return
    if command == "/voice" or command in {"/voice status", "/voice on", "/voice off", "/voice tts"} or command.startswith("/voice "):
        send_voice_menu(token, chat_id, db)
        return
    if command == "/voice_input" or command == "/voice_input status":
        send_voice_input_menu(token, chat_id, db)
        return
    if command.startswith("/voice_input model "):
        send_voice_input_menu(token, chat_id, db)
        return
    if command.startswith("/voice_input language "):
        language = stripped.split(None, 2)[2].casefold()
        if not re.fullmatch(r"[a-z]{2,8}", language) and language not in {"auto", "detect"}:
            send_text(token, chat_id, "Use a language code such as id, en, ja, or auto.")
        else:
            set_meta(db, f"stt_language:{chat_id}", language)
            send_text(token, chat_id, f"STT language set to {language}.")
        return
    if command == "/voice_input on" or command == "/voice_input off":
        send_voice_input_menu(token, chat_id, db)
        return
    if command.startswith("/voice_input "):
        send_text(token, chat_id, "Use /voice_input on, /voice_input off, or /voice_input status.")
        return
    if command in {"/note", "/authornote"}:
        note = session["author_note"] or "off"
        send_text(token, chat_id, f"Author's Note: {note}\nUse /note <text> to set it, or /note off to clear it.")
        return
    if command.startswith("/note ") or command.startswith("/authornote "):
        note = stripped.split(None, 1)[1].strip()
        if note.lower() in {"off", "none", "clear", "disable"}:
            update_session(db, chat_id, session_id, author_note="")
            send_text(token, chat_id, "Author's Note cleared for this session.")
        elif len(note) <= 2000:
            update_session(db, chat_id, session_id, author_note=note)
            send_text(token, chat_id, "Author's Note updated for this session.")
        else:
            send_text(token, chat_id, "Author's Note is limited to 2,000 characters.")
        return
    if command in {"/systemprompt", "/system"}:
        send_system_prompt_menu(token, chat_id, session.get("system_prompt") or "")
        return
    if command.startswith("/systemprompt ") or command.startswith("/system "):
        send_text(token, chat_id, "System Prompt is panel-only. Use /systemprompt and choose a TXT file from the panel.")
        return
    if command == "/character":
        send_character_menu(token, chat_id, session["character_file"])
        return
    if command.startswith("/character "):
        send_text(token, chat_id, "Character management is panel-only. Use /character and choose Info, Delete, or Upload.")
        return
    if command == "/session":
        send_session_menu(token, chat_id, list_sessions(db, chat_id), session_id)
        return
    if command == "/persona":
        send_persona_menu(token, chat_id, current_persona)
        return
    if command.startswith("/persona "):
        requested = stripped.split(None, 1)[1].strip()
        if requested.lower() in {"off", "none", "disable"}:
            update_session(db, chat_id, session_id, persona_id="")
            send_text(token, chat_id, "Persona disabled for this session.")
        elif get_persona(requested):
            update_session(db, chat_id, session_id, persona_id=requested)
            send_text(token, chat_id, f"Persona selected: {persona_name(requested)}")
        else:
            send_text(token, chat_id, "Unknown Persona. Use /persona to choose one.")
        return
    if command == "/world" or command.startswith("/world "):
        send_world_menu(token, chat_id, session["world_file"])
        return
    if command == "/providers health" or command.startswith("/providers health "):
        send_model_menu(token, chat_id, current_model)
        return
    if command in {"/model refresh", "/providers refresh"}:
        send_model_menu(token, chat_id, current_model)
        return
    if command == "/model" or command == "/providers":
        send_model_menu(token, chat_id, current_model)
        return
    if command.startswith("/model "):
        new_model = stripped.split(None, 1)[1].strip()
        if not new_model or len(new_model) > 200 or any(ch.isspace() for ch in new_model):
            send_text(token, chat_id, "Invalid model ID. Example: /model google/gemma-4-31b-it:free")
            return
        update_session(db, chat_id, session_id, model_id=new_model)
        send_text(token, chat_id, f"Model changed to: {new_model}")
        return

    if command == "/edit":
        send_text(token, chat_id, "Gunakan /edit <teks baru> untuk mengedit pesan user terakhir.")
        return
    if command.startswith("/edit "):
        new_text = stripped.split(None, 1)[1].strip()
        if not new_text:
            send_text(token, chat_id, "Teks edit tidak boleh kosong.")
        elif len(new_text) > 12000:
            send_text(token, chat_id, "Teks edit dibatasi 12.000 karakter.")
        else:
            edit_last_user(db, token, api_key, session, fields, chat_id, new_text, operation_id=operation_id)
        return
    if command == "/continue":
        continue_last(db, token, api_key, session, fields, chat_id, operation_id=operation_id)
        return
    if command == "/swipe" or command == "/branch":
        send_swipe_menu(token, db, chat_id, session_id)
        return
    if command.startswith("/branch "):
        try:
            branch_index = int(stripped.split(None, 1)[1])
        except ValueError:
            send_text(token, chat_id, "Use /branch <number> or /branch to open the selector.")
            return
        selected = keep_swipe_variant(db, chat_id, session_id, branch_index)
        if selected is None:
            send_text(token, chat_id, "Branch not found. Use /branch to list available branches.")
        else:
            send_text(token, chat_id, f"Branch {branch_index} selected.")
        return
    if command == "/regen":
        regenerate_last(db, token, api_key, session, fields, chat_id, operation_id=operation_id)
        return

    history_rows = db.execute(
        "SELECT role, content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at DESC LIMIT ?",
        (chat_id, session_id, MAX_HISTORY_MESSAGES),
    ).fetchall()
    history_rows = list(reversed(history_rows))
    rag_bundle = rag_retrieval_bundle(db, chat_id, text)
    messages = build_chat_messages(session, fields, text, history_rows, memory_context=recall_memory_context(db, chat_id, session, fields, text), session_summary=session_summary_for_prompt(db, chat_id, session), rag_context=rag_context_for_prompt(db, chat_id, text, rag_bundle), group_context=group_context)

    send_typing(token, chat_id)
    stream_message_id = None
    stream_enabled = get_meta(db, f"stream_mode:{chat_id}", "on") == "on"
    if stream_enabled:
        try:
            placeholder = telegram_request(token, "sendMessage", {"chat_id": chat_id, "text": "⌛ Generating…"})
            stream_message_id = int(placeholder.get("message_id")) if placeholder.get("message_id") else None
        except Exception:
            logging.info("Could not create streaming placeholder", exc_info=True)

    def stream_update(partial: str) -> None:
        if stream_message_id is None or not partial:
            return
        try:
            telegram_request(token, "editMessageText", {"chat_id": chat_id, "message_id": stream_message_id, "text": partial[-3900:]})
        except Exception:
            logging.debug("Streaming Telegram edit failed", exc_info=True)

    reply = generate_text(api_key, current_model, messages, session_id=f"telegram:{chat_id}:{session_id}", settings=get_generation_settings(db, chat_id, session_id), stream_callback=stream_update if stream_message_id else None)
    reply += rag_citation_footer(db, chat_id, text, rag_bundle)
    stored_reply = reply if group_turn and group_turn[1].get("mode") == "autonomous" else (f"{fields['name']}: {reply}" if group_turn else reply)
    now = time.time()
    db.execute("INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) VALUES(?,?,?,?,?,?)", (chat_id, session_id, "user", text, str(telegram_message_id) if telegram_message_id is not None else None, now))
    assistant_cursor = db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)", (chat_id, session_id, "assistant", stored_reply, now + 0.001))
    assistant_rowid = assistant_cursor.lastrowid
    if not group_turn:
        db.commit()
    save_response_variant(db, chat_id, session_id, text, stored_reply, commit=not bool(group_turn))
    if group_turn:
        advance_group_turn(db, chat_id, session_id, operation_id=operation_id, commit=True)
    else:
        db.commit()
    retain_session_memory(db, chat_id, session, fields)
    if telegram_message_id is not None:
        clear_failed_turn(db, chat_id, telegram_message_id)
    if stream_message_id:
        try:
            telegram_request(token, "deleteMessage", {"chat_id": chat_id, "message_id": stream_message_id})
        except Exception:
            try:
                telegram_request(token, "editMessageText", {"chat_id": chat_id, "message_id": stream_message_id, "text": "\u2063", "reply_markup": {"inline_keyboard": []}})
            except Exception:
                logging.info("Could not hide completed streaming preview", exc_info=True)
    send_reply(token, chat_id, stored_reply, db, session_id, assistant_rowid)



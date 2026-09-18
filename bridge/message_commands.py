def send_reset_confirmation_menu(token: str, chat_id: str, message_id: int | None = None) -> None:
    method = "editMessageText" if message_id else "sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "Reset active session and purge its memory?\n\nThis will:\n• Reset only the active session conversation.\n• Delete Hindsight memories for this active session only.\n• Delete session SQLite data, and session documents.\n\nThis cannot be undone.",
        "reply_markup": {"inline_keyboard": [
            [{"text": "✅ Confirm active-session reset", "callback_data": "reset:confirm"}],
            [{"text": "❌ Cancel", "callback_data": "reset:cancel"}],
        ]},
    }
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def reset_session(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], operation_id: int | str | None = None) -> None:
    if operation_id is not None:
        if operation_was_applied(db, operation_id) or not begin_operation(db, operation_id, "reset"):
            return
    phase = operation_phase(db, operation_id) if operation_id is not None else ""
    if phase == "local_committed":
        if operation_id is not None:
            record_operation(db, operation_id, "reset")
            db.commit()
        return
    if phase != "memory_purged":
        purge_hindsight_session(db, chat_id, session["session_id"])
        if operation_id is not None:
            set_operation_phase(db, operation_id, "reset", "memory_purged")
        db.commit()
    db.execute("DELETE FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"]))
    db.execute("DELETE FROM response_variants WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"]))
    db.execute("DELETE FROM failed_turns WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"]))
    clear_session_summary(db, chat_id, session["session_id"])
    db.execute("DELETE FROM meta WHERE key IN (?, ?)", (swipe_state_key(chat_id, session["session_id"]), f"swipe_message:{chat_id}:{session['session_id']}"))
    if operation_id is not None:
        set_operation_phase(db, operation_id, "reset", "local_committed")
    db.commit()
    if operation_id is not None:
        record_operation(db, operation_id, "reset")
        db.commit()
    if "optimize_database" in globals():
        optimize_database(db)


def send_pending_input_message(db: sqlite3.Connection, token: str, chat_id: str, meta_key: str, state: dict, text: str) -> None:
    message_ids = send_text(token, chat_id, text)
    existing = state.get("prompt_message_ids") or []
    if isinstance(existing, (int, str)):
        existing = [existing]
    state["prompt_message_ids"] = list(existing) + list(message_ids or [])
    set_meta(db, meta_key, json.dumps(state))


def generate_and_store_reply(db: sqlite3.Connection, token: str, api_key: str, fields: dict, chat_id: str, text: str, session: dict, session_id: str, current_model: str, group_turn, group_context: str, telegram_message_id: int | None, operation_id: int | None) -> None:
    """Assemble context, run generation, persist the reply, and deliver it."""
    history_rows = timed_call("history_load", db.execute,
        "SELECT role, content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at DESC LIMIT ?",
        (chat_id, session_id, MAX_HISTORY_MESSAGES),
    ).fetchall()
    history_rows = list(reversed(history_rows))
    rag_bundle = timed_call("rag_retrieval", rag_retrieval_bundle, db, chat_id, text)
    messages = timed_call("prompt_assembly", build_chat_messages, session, fields, text, history_rows, memory_context=recall_memory_context(db, chat_id, session, fields, text), session_summary=session_summary_for_prompt(db, chat_id, session), rag_context=rag_context_for_prompt(db, chat_id, text, rag_bundle), group_context=group_context)
    send_typing(token, chat_id)
    language = session.get("response_language") or "auto"
    fixed_language = normalize_response_language(language) != "auto"
    stream_message_id = None
    if not fixed_language and get_meta(db, f"stream_mode:{chat_id}", "on") == "on":
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

    generation_settings = get_generation_settings(db, chat_id, session_id)
    generation_session_id = f"telegram:{chat_id}:{session_id}"
    reply = timed_call("provider_generation", generate_text, api_key, current_model, messages, session_id=generation_session_id, settings=generation_settings, stream_callback=stream_update if stream_message_id else None)
    reply += rag_citation_footer(db, chat_id, text, rag_bundle)
    reply = render_response_language(api_key, current_model, reply, language, generation_session_id, generation_settings)
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
    queue_user_quote_tts(token, chat_id, text, db, session_id, telegram_message_id)
    send_reply(token, chat_id, stored_reply, db, session_id, assistant_rowid)


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
    if command == "/reset":
        send_reset_confirmation_menu(token, chat_id)
        return
    if handle_pending_input(db, token, chat_id, session, stripped, api_key=api_key, fields=fields, operation_id=operation_id):
        return
    if command == "/session":
        send_session_menu(token, chat_id, list_sessions(db, chat_id), session_id)
        return
    session = reconcile_session_character(db, chat_id, session)
    fields = card_fields_from_file(session["character_file"])
    group_turn = group_current_speaker(db, chat_id, session, text)
    group_context = ""
    if group_turn:
        fields = card_fields_from_file(group_turn[0])
        group_context = group_prompt_context(db, chat_id, session, group_turn[0])
    current_model = session["model_id"] or model
    current_persona = session["persona_id"]
    user_name = persona_name(current_persona) if current_persona else DEFAULT_USER_NAME
    if handle_command_route(db, token, api_key, model, fields, chat_id, stripped, command, session, session_id, current_model, current_persona, user_name, operation_id=operation_id):
        return


    generate_and_store_reply(db, token, api_key, fields, chat_id, text, session, session_id, current_model, group_turn, group_context, telegram_message_id, operation_id)

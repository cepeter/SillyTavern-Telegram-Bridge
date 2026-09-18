
def process_image_message(db: sqlite3.Connection, token: str, api_key: str, session: dict[str, str], fields: dict, chat_id: str, caption: str, image_bytes: bytes, mime_type: str = "image/jpeg", telegram_message_id: int | None = None) -> None:
    caption = caption.strip()[:12000] or "Please analyze this image in the context of the conversation."
    group_turn = group_current_speaker(db, chat_id, session, caption)
    group_context = ""
    if group_turn:
        fields = card_fields_from_file(group_turn[0])
        group_context = group_prompt_context(db, chat_id, session, group_turn[0])
    image_data_uri = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    rows = db.execute("SELECT role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid", (chat_id, session["session_id"])).fetchall()
    history_rows = [(row[0], row[1]) for row in rows[-MAX_HISTORY_MESSAGES:]]
    rag_bundle = rag_retrieval_bundle(db, chat_id, caption)
    messages = build_chat_messages(session, fields, caption, history_rows, image_data_uri=image_data_uri, memory_context=recall_memory_context(db, chat_id, session, fields, caption), session_summary=session_summary_for_prompt(db, chat_id, session), rag_context=rag_context_for_prompt(db, chat_id, caption, rag_bundle), group_context=group_context)
    send_typing(token, chat_id)
    reply = generate_text(api_key, session["model_id"], messages, session_id=f"telegram:{chat_id}:{session['session_id']}", settings=get_generation_settings(db, chat_id, session["session_id"]))
    reply += rag_citation_footer(db, chat_id, caption, rag_bundle)
    reply = render_session_response(api_key, session, reply, chat_id, get_generation_settings(db, chat_id, session["session_id"]))
    stored_reply = reply if group_turn and group_turn[1].get("mode") == "autonomous" else (f"{fields['name']}: {reply}" if group_turn else reply)
    stored_text = f"[Image input] {caption}"
    db.execute("INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) VALUES(?,?,?,?,?,?)", (chat_id, session["session_id"], "user", stored_text, str(telegram_message_id) if telegram_message_id is not None else None, time.time()))
    assistant_cursor = db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)", (chat_id, session["session_id"], "assistant", stored_reply, time.time()))
    assistant_rowid = assistant_cursor.lastrowid
    if not group_turn:
        db.commit()
    save_response_variant(db, chat_id, session["session_id"], stored_text, stored_reply, commit=not bool(group_turn))
    if group_turn:
        advance_group_turn(db, chat_id, session["session_id"], commit=True)
    else:
        db.commit()
    retain_session_memory(db, chat_id, session, fields)
    send_reply(token, chat_id, stored_reply, db, session["session_id"], assistant_rowid)


def regenerate_edited_turn(db: sqlite3.Connection, token: str, api_key: str, session: dict[str, str], fields: dict[str, str], chat_id: str, user_rowid: int, new_text: str, operation_id: int | str | None = None) -> None:
    if operation_id is not None:
        if operation_was_applied(db, operation_id) or not begin_operation(db, operation_id, "edit"):
            return
    session_id = session["session_id"]
    rows = db.execute("SELECT rowid,role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid", (chat_id, session_id)).fetchall()
    target_index = next((i for i, row in enumerate(rows) if int(row[0]) == int(user_rowid) and row[1] == "user"), None)
    if target_index is None:
        raise ValueError("Telegram message is not a user turn in the active session")
    history_rows = [(row[1], row[2]) for row in rows[:target_index]]
    memory_context = recall_memory_context(db, chat_id, session, fields, new_text)
    _covered_summary, covered_until = get_session_summary(db, chat_id, session_id)
    session_summary = "" if covered_until >= int(user_rowid) else session_summary_for_prompt(db, chat_id, session)
    rag_bundle = rag_retrieval_bundle(db, chat_id, new_text)
    rag_context = rag_context_for_prompt(db, chat_id, new_text, rag_bundle)
    generation_settings = get_generation_settings(db, chat_id, session_id)
    messages = build_chat_messages(session, fields, new_text, history_rows, memory_context=memory_context, session_summary=session_summary, rag_context=rag_context)
    send_typing(token, chat_id)
    reply = generate_text(api_key, session["model_id"], messages, session_id=f"telegram:{chat_id}:{session_id}", settings=generation_settings)
    reply += rag_citation_footer(db, chat_id, new_text, rag_bundle)
    reply = render_session_response(api_key, session, reply, chat_id, generation_settings)
    clear_session_summary(db, chat_id, session_id)
    db.execute("UPDATE messages SET content=? WHERE rowid=?", (new_text, user_rowid))
    db.execute("DELETE FROM messages WHERE chat_id=? AND session_id=? AND rowid>?", (chat_id, session_id, user_rowid))
    assistant_cursor = db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)", (chat_id, session_id, "assistant", reply, time.time()))
    assistant_rowid = assistant_cursor.lastrowid
    db.commit()
    if operation_id is not None:
        set_operation_phase(db, operation_id, "edit", "local_committed")
        db.commit()
    delete_outgoing_messages(db, token, chat_id, session_id, user_rowid)
    save_response_variant(db, chat_id, session_id, new_text, reply)
    retain_session_memory(db, chat_id, session, fields)
    send_reply(token, chat_id, f"✏️ Edited message regenerated.\n\n{reply}", db, session_id, assistant_rowid)
    if operation_id is not None:
        record_operation(db, operation_id, "edit")
        db.commit()


def edit_last_user(db: sqlite3.Connection, token: str, api_key: str, session: dict[str, str], fields: dict[str, str], chat_id: str, new_text: str, operation_id: int | str | None = None) -> None:
    session_id = session["session_id"]
    rows = db.execute("SELECT rowid,role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid", (chat_id, session_id)).fetchall()
    last_user = next((row for row in reversed(rows) if row[1] == "user"), None)
    if last_user is None:
        send_text(token, chat_id, "Belum ada pesan user untuk diedit.")
        return
    regenerate_edited_turn(db, token, api_key, session, fields, chat_id, int(last_user[0]), new_text, operation_id=operation_id)


def edit_telegram_user_message(db: sqlite3.Connection, token: str, api_key: str, chat_id: str, message_id: int, new_text: str, default_model: str, operation_id: int | str | None = None) -> None:
    session = ensure_session(db, chat_id, default_model)
    fields = card_fields_from_file(session["character_file"])
    row = db.execute("SELECT rowid,session_id,role FROM messages WHERE chat_id=? AND telegram_message_id=? ORDER BY rowid DESC LIMIT 1", (chat_id, str(message_id))).fetchone()
    if row is None or row[2] != "user" or row[1] != session["session_id"]:
        send_text(token, chat_id, "Edited message was not found in the active session.")
        return
    if not new_text.strip():
        send_text(token, chat_id, "Edited message cannot be empty.")
        return
    regenerate_edited_turn(db, token, api_key, session, fields, chat_id, int(row[0]), new_text.strip()[:12000], operation_id=operation_id)


def send_stscript_menu(token: str, chat_id: str, message_id: int | None = None) -> None:
    """Show the allowlisted STscript actions without accepting arbitrary scripts."""
    payload = {"chat_id": chat_id, "text": "Safe STscript actions:\n\nReset clears only the active session after confirmation.", "reply_markup": {"inline_keyboard": [[{"text": "♻️ Reset", "callback_data": "enum:stscript:reset"}], [{"text": "❌ Close", "callback_data": "enum:stscript:cancel"}]]}}
    method = "editMessageText" if message_id else "sendMessage"
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_note_menu(token: str, chat_id: str, current_note: str, message_id: int | None = None) -> None:
    state = "on" if str(current_note or "").strip() else "off"
    text = f"Author's Note — {state}\nCurrent length: {len(str(current_note or '').strip())} characters\nChoose an action:"
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": [
        [{"text": "🚫 Off", "callback_data": "note:off"}, {"text": "✏️ User input", "callback_data": "note:input"}],
        [{"text": "❌ Close", "callback_data": "note:cancel"}],
    ]}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def handle_macro_command(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], fields: dict, command_text: str) -> None:
    parts = command_text.split(None, 1)
    if parts[0].casefold() == "/macro":
        raw = parts[1] if len(parts) > 1 else ""
        send_text(token, chat_id, replace_macros(raw, fields, persona_name(session["persona_id"]) if session["persona_id"] else "user"))
        return
    script = parts[1].strip() if len(parts) > 1 else ""
    action, _, argument = script.partition(" ")
    if action.casefold() == "reset":
        send_reset_confirmation_menu(token, chat_id)
    else:
        send_text(token, chat_id, "Use /stscript to open the safe Reset action panel.")


def apply_preset_action(db: sqlite3.Connection, token: str, chat_id: str, session_id: str, action: str, name: str) -> None:
    name = str(name).strip()
    if action not in {"use", "delete"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        send_text(token, chat_id, "Invalid preset panel action.")
        return
    if action == "use":
        settings = load_generation_preset(db, chat_id, name)
        if not settings:
            send_text(token, chat_id, f"Preset not found: {name}")
            return
        update_generation_settings(db, chat_id, session_id, **settings)
        send_text(token, chat_id, f"Preset applied to this session: {name}\n{format_generation_settings(get_generation_settings(db, chat_id, session_id))}")
        return
    send_text(token, chat_id, f"Preset deleted: {name}" if delete_generation_preset(db, chat_id, name) else f"Preset not found: {name}")


def prompt_diagnostics(db: sqlite3.Connection, chat_id: str, session: dict[str, str], fields: dict[str, str]) -> str:
    message_count = db.execute("SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"])).fetchone()[0]
    summary, covered_until = get_session_summary(db, chat_id, session["session_id"])
    docs = data_bank_documents(db, chat_id)
    group = group_state(db, chat_id, session["session_id"])
    return (f"Prompt inspector\nCharacter: {fields['name']}\nMessages: {message_count}\n"
            f"Context input budget: ~{context_input_budget_tokens()} tokens\nHistory candidates: {context_history_candidate_limit()} messages\nSession summary: {len(summary)} chars (through row {covered_until})\n"
            f"Hindsight: {memory_mode(db, chat_id)} / {memory_scope(db, chat_id)}\n"
            f"Data Bank: {rag_mode(db, chat_id)} / {len(docs)} documents\n"
            f"Group: {'on' if group['enabled'] else 'off'} / mode={group['mode']} / members={len(group['members'])}\n"
            "Macro support: char, user, random, pick, time, date, weekday\nWorld Info recursion: maximum 3 passes")

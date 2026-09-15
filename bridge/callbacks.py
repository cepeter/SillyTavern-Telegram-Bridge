def close_panel_message(token: str, chat_id: str, message: dict) -> None:
    message_id = message.get("message_id")
    try:
        telegram_request(token, "editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": "\u2063", "reply_markup": {"inline_keyboard": []}})
    except Exception:
        logging.warning("Help panel hide failed; trying delete", exc_info=True)
        try:
            telegram_request(token, "deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        except Exception:
            logging.warning("Help panel delete fallback failed", exc_info=True)
        return
    try:
        telegram_request(token, "deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    except Exception:
        logging.info("Help panel hidden; Telegram delete was rejected", exc_info=True)


def is_session_scoped_panel_callback(data: str) -> bool:
    return data.startswith(("character", "persona", "session", "world", "systemprompt", "models", "provider", "model", "group", "groupchars", "groupmode", "enum:settings", "enum:preset", "enum:rag"))


def process_callback(db: sqlite3.Connection, token: str, callback: dict, operation_id: int | None = None) -> None:
    sender = str((callback.get("from") or {}).get("id", ""))
    message = callback.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    data = str(callback.get("data") or "")
    if not chat_id:
        return
    answer_callback = globals()["answer_callback"]
    if callback.get("_queued"):
        def answer_callback(*_args, **_kwargs):
            return None
    message_id = message.get("message_id")
    bound_session_id = panel_session_for_message(db, chat_id, message_id) if message_id else None
    if message_id and is_session_scoped_panel_callback(data) and not bound_session_id:
        answer_callback(token, str(callback.get("id", "")), "Panel expired; reopen it")
        return
    session = load_session(db, chat_id, bound_session_id, DEFAULT_MODEL) if bound_session_id else ensure_session(db, chat_id, DEFAULT_MODEL)
    session_id = session["session_id"]
    set_panel_session_context(session_id)

    if data.startswith("systemprompt:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            page = int(value.split(":", 1)[1])
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_system_prompt_menu(token, chat_id, session.get("system_prompt") or "", message.get("message_id"), page)
            return
        if value == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            remove_inline_keyboard(token, callback)
        elif value == "off":
            update_session(db, chat_id, session_id, system_prompt="")
            answer_callback(token, str(callback.get("id", "")), "System Prompt off")
            remove_inline_keyboard(token, callback)
            send_text(token, chat_id, "Session System Prompt disabled.")
        else:
            prompt = get_system_prompt_choice(value)
            if prompt is None:
                answer_callback(token, str(callback.get("id", "")), "Choice not found")
            else:
                update_session(db, chat_id, session_id, system_prompt=prompt)
                answer_callback(token, str(callback.get("id", "")), "System Prompt selected")
                remove_inline_keyboard(token, callback)
                send_text(token, chat_id, f"System Prompt selected: {value}")
        return
    if data.startswith("help:"):
        category = data.split(":", 1)[1]
        if category == "close":
            close_panel_message(token, chat_id, message)
            try:
                answer_callback(token, str(callback.get("id", "")), "Closed")
            except Exception:
                logging.debug("Could not acknowledge closed help panel", exc_info=True)
        elif category in HELP_CATEGORIES:
            answer_callback(token, str(callback.get("id", "")), "Help")
            send_help_menu(token, chat_id, category, message.get("message_id"))
        else:
            answer_callback(token, str(callback.get("id", "")), "Help")
            send_help_menu(token, chat_id, None, message.get("message_id"))
        return
    if data.startswith("swipe:"):
        action = data.split(":", 1)[1]
        user_row, variants = last_user_variants(db, chat_id, session_id)
        if not variants:
            answer_callback(token, str(callback.get("id", "")), "No variants")
            remove_inline_keyboard(token, callback)
            return
        current = int(get_meta(db, swipe_state_key(chat_id, session_id), str(variants[-1][0])))
        indexes = [int(row[0]) for row in variants]
        if action == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            remove_inline_keyboard(token, callback)
            return
        if action in {"prev", "next"}:
            position = indexes.index(current) if current in indexes else 0
            position = (position - 1) % len(indexes) if action == "prev" else (position + 1) % len(indexes)
            answer_callback(token, str(callback.get("id", "")), f"Variant {indexes[position]}")
            edit_swipe_menu(token, db, callback, session_id, indexes[position], variants)
            return
        if action == "keep":
            if user_row:
                delete_outgoing_messages(db, token, chat_id, session_id, int(user_row[0]))
            selected = keep_swipe_variant(db, chat_id, session_id, current)
            if selected is None:
                answer_callback(token, str(callback.get("id", "")), "Variant not found")
                return
            answer_callback(token, str(callback.get("id", "")), "Kept")
            message = callback.get("message") or {}
            telegram_request(token, "editMessageText", {
                "chat_id": chat_id,
                "message_id": message.get("message_id"),
                "text": f"✅ Kept variant {current}\n\n{selected[:3900]}",
            })
            return
        return
    if data == "character:menu":
        send_character_menu(token, chat_id, session["character_file"], message.get("message_id"))
        return
    if data == "character:info":
        answer_callback(token, str(callback.get("id", "")), "Info")
        send_character_info_menu(token, chat_id, message.get("message_id"))
        return
    if data == "character:delete":
        answer_callback(token, str(callback.get("id", "")), "Delete")
        send_character_delete_menu(token, chat_id, session["character_file"], message.get("message_id"))
        return
    if data == "character:upload":
        answer_callback(token, str(callback.get("id", "")), "Upload")
        send_text(token, chat_id, "Send the character card as a Telegram Document (PNG with SillyTavern chara metadata). The upload will be validated and queued safely.")
        return
    if data.startswith("characterinfo:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            send_character_info_menu(token, chat_id, message.get("message_id"), int(value.split(":", 1)[1]))
            return
        filename = resolve_dynamic_callback_token(value, "character", chat_id) or ""
        if not safe_character_path(filename):
            answer_callback(token, str(callback.get("id", "")), "Character choice expired")
            return
        info = card_fields_from_file(filename)
        answer_callback(token, str(callback.get("id", "")), "Info")
        telegram_request(token, "editMessageText", {"chat_id": chat_id, "message_id": message.get("message_id"), "text": f"Character: {info['name']}\nFile: {filename}\nDescription: {len(info['description'])} chars\nPersonality: {len(info['personality'])} chars\nScenario: {len(info['scenario'])} chars\nFirst message: {len(info['first_mes'])} chars", "reply_markup": {"inline_keyboard": [[{"text": "⬅️ Back", "callback_data": "character:info"}, {"text": "❌ Close", "callback_data": "character:cancel"}]]}})
        return
    if data.startswith("characterdelete:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            send_character_delete_menu(token, chat_id, session["character_file"], message.get("message_id"), int(value.split(":", 1)[1]))
            return
        filename = resolve_dynamic_callback_token(value, "character", chat_id) or ""
        if not safe_character_path(filename) or filename == session["character_file"]:
            answer_callback(token, str(callback.get("id", "")), "Character choice invalid")
            return
        answer_callback(token, str(callback.get("id", "")), "Confirm deletion")
        send_character_delete_confirm(token, chat_id, filename, message.get("message_id"))
        return
    if data.startswith("characterdeleteconfirm:"):
        filename = resolve_dynamic_callback_token(data.split(":", 1)[1], "character", chat_id) or ""
        path = safe_character_path(filename)
        references = character_delete_references(db, filename) if path else []
        is_default = filename == DEFAULT_CHARACTER_FILE or (path and path.resolve() == CARD_FILE.resolve())
        if not path or filename == session["character_file"] or is_default or references:
            reason = "active/default/referenced character" if path else "character not found"
            answer_callback(token, str(callback.get("id", "")), f"Deletion refused: {reason}")
            return
        try:
            verify_character_card_backup(path, path.read_bytes())
        except OSError:
            answer_callback(token, str(callback.get("id", "")), "Deletion refused: backup verification failed")
            return
        if operation_id is not None and not begin_operation(db, operation_id, "character_delete"):
            answer_callback(token, str(callback.get("id", "")), "Already processed")
            return
        path.unlink(missing_ok=True)
        record_operation(db, operation_id, "character_delete")
        db.commit()
        answer_callback(token, str(callback.get("id", "")), "Deleted")
        send_character_menu(token, chat_id, session["character_file"], message.get("message_id"))
        return
    if data.startswith("character:"):
        value = data.split(":", 1)[1]
        if not value.startswith("page:") and value != "cancel":
            value = resolve_dynamic_callback_token(value, "character", chat_id) or ""
        if value.startswith("page:"):
            page = int(value.split(":", 1)[1])
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_character_menu(token, chat_id, session["character_file"], message.get("message_id"), page)
            return
        if value == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            remove_inline_keyboard(token, callback)
        elif safe_character_path(value):
            update_session(db, chat_id, session_id, operation_id=operation_id, operation_kind="character_select", character_file=Path(value).name)
            answer_callback(token, str(callback.get("id", "")), "Character selected")
            remove_inline_keyboard(token, callback)
            send_text(token, chat_id, f"Character selected: {card_fields_from_file(value)['name']}")
        else:
            answer_callback(token, str(callback.get("id", "")), "Character not found")
        return

    if data.startswith("session:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            page = int(value.split(":", 1)[1])
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_session_menu(token, chat_id, list_sessions(db, chat_id), session_id, message.get("message_id"), page)
            return
        if value == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            remove_inline_keyboard(token, callback)
        elif value == "new":
            new_session = create_session(db, chat_id, DEFAULT_MODEL, session_id=f"job-{operation_id}" if operation_id is not None else None)
            answer_callback(token, str(callback.get("id", "")), "New session")
            remove_inline_keyboard(token, callback)
            send_text(token, chat_id, f"New session started: {new_session['session_id']}")
        else:
            available = {item["session_id"] for item in list_sessions(db, chat_id)}
            if value in available:
                set_meta(db, f"active_session:{chat_id}", value)
                answer_callback(token, str(callback.get("id", "")), "Session selected")
                remove_inline_keyboard(token, callback)
                send_text(token, chat_id, f"Session selected: {value}")
            else:
                answer_callback(token, str(callback.get("id", "")), "Session not found")
        return

    if data.startswith("persona:"):
        value = data.split(":", 1)[1]
        if not value.startswith("page:") and value != "cancel":
            value = resolve_dynamic_callback_token(value, "persona", chat_id) or ""
        if value.startswith("page:"):
            page = int(value.split(":", 1)[1])
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_persona_menu(token, chat_id, session["persona_id"], message.get("message_id"), page)
            return
        if value == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            remove_inline_keyboard(token, callback)
        elif value == "off":
            update_session(db, chat_id, session_id, operation_id=operation_id, operation_kind="persona_select", persona_id="")
            answer_callback(token, str(callback.get("id", "")), "Persona off")
            remove_inline_keyboard(token, callback)
            send_text(token, chat_id, "Persona disabled for this session.")
        elif get_persona(value):
            update_session(db, chat_id, session_id, operation_id=operation_id, operation_kind="persona_select", persona_id=value)
            answer_callback(token, str(callback.get("id", "")), "Persona selected")
            remove_inline_keyboard(token, callback)
            send_text(token, chat_id, f"Persona selected: {persona_name(value)}")
        else:
            answer_callback(token, str(callback.get("id", "")), "Persona not found")
        return

    if data.startswith("world:"):
        value = data.split(":", 1)[1]
        if not value.startswith("page:") and value not in {"cancel", "done", "off"}:
            value = resolve_dynamic_callback_token(value, "world", chat_id) or ""
        if value.startswith("page:"):
            page = int(value.split(":", 1)[1])
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_world_menu(token, chat_id, session["world_file"], message.get("message_id"), page)
            return
        if value == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            remove_inline_keyboard(token, callback)
        elif value == "done":
            answer_callback(token, str(callback.get("id", "")), "Saved")
            remove_inline_keyboard(token, callback)
        elif value == "off":
            update_session(db, chat_id, session_id, operation_id=operation_id, operation_kind="world_clear", world_file="")
            answer_callback(token, str(callback.get("id", "")), "All World Info cleared")
            send_world_menu(token, chat_id, "", message.get("message_id"), 0)
        elif safe_world_path(value):
            selected = active_world_files(session["world_file"])
            filename = Path(value).name
            if filename in selected:
                selected.remove(filename)
                status = "World Info disabled"
            else:
                selected.append(filename)
                status = "World Info enabled"
            update_session(db, chat_id, session_id, operation_id=operation_id, operation_kind="world_select", world_file=encode_world_files(selected))
            answer_callback(token, str(callback.get("id", "")), status)
            send_world_menu(token, chat_id, encode_world_files(selected), message.get("message_id"), 0)
        else:
            answer_callback(token, str(callback.get("id", "")), "World Info file not found")
        return

    if data.startswith("enum:"):
        handle_enum_callback(db, token, chat_id, session, data, message)
        return

    if data.startswith("group:") or data.startswith("groupchars:") or data.startswith("groupmode:"):
        if data.startswith("groupchars:") and not data.startswith("groupchars:page:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                resolved = resolve_dynamic_callback_token(parts[2], "group_character", chat_id)
                data = f"groupchars:{parts[1]}:{resolved or ''}"
        handle_group_panel_callback(db, token, chat_id, session, data, message, operation_id)
        return

    if data.startswith("models:providers:"):
        page = int(data.rsplit(":", 1)[1])
        answer_callback(token, str(callback.get("id", "")), "Page")
        send_model_menu(token, chat_id, session["model_id"] or DEFAULT_MODEL, message_id=message.get("message_id"), page=page)
        return
    if data.startswith("models:model:"):
        parts = data.split(":")
        provider_id = resolve_dynamic_callback_token(parts[2], "provider", chat_id) or ""
        page = int(parts[3])
        answer_callback(token, str(callback.get("id", "")), "Page")
        send_model_menu(token, chat_id, session["model_id"] or DEFAULT_MODEL, provider_id, message.get("message_id"), page)
        return
    if data == "models:cancel":
        answer_callback(token, str(callback.get("id", "")), "Cancelled")
        remove_inline_keyboard(token, callback)
        return
    if data == "models:back":
        current_model = session["model_id"] or DEFAULT_MODEL
        answer_callback(token, str(callback.get("id", "")), "Back to providers")
        send_model_menu(token, chat_id, current_model, message_id=message.get("message_id"))
        return
    if data == "provider:health":
        answer_callback(token, str(callback.get("id", "")), "Health")
        send_provider_health_menu(token, chat_id, message.get("message_id"))
        return
    if data == "provider:refresh":
        _config, refreshed, failed = refresh_model_catalog(force=True)
        answer_callback(token, str(callback.get("id", "")), "Refreshed")
        send_text(token, chat_id, f"Model catalog refreshed: {refreshed} providers updated; {failed} failed.")
        send_model_menu(token, chat_id, session["model_id"] or DEFAULT_MODEL, message_id=message.get("message_id"))
        return
    if data == "provider:back":
        send_model_menu(token, chat_id, session["model_id"] or DEFAULT_MODEL, message_id=message.get("message_id"))
        return
    if data.startswith("provider:"):
        provider_id = resolve_dynamic_callback_token(data.split(":", 1)[1], "provider", chat_id) or ""
        current_model = session["model_id"] or DEFAULT_MODEL
        answer_callback(token, str(callback.get("id", "")), "Provider selected")
        send_model_menu(token, chat_id, current_model, provider_id, message_id=message.get("message_id"))
        return
    if data.startswith("unsupported:"):
        provider_id = resolve_dynamic_callback_token(data.split(":", 1)[1], "provider", chat_id) or ""
        answer_callback(token, str(callback.get("id", "")), "Catalog only: adapter not enabled")
        send_text(token, chat_id, f"Provider '{provider_id}' is visible in the bridge catalog, but its adapter is not enabled yet.")
        return
    if not data.startswith("model:"):
        return
    model = resolve_dynamic_callback_token(data.split(":", 1)[1], "model", chat_id) or ""
    if not model:
        answer_callback(token, str(callback.get("id", "")), "Model choice expired")
        return
    update_session(db, chat_id, session_id, operation_id=operation_id, operation_kind="model_select", model_id=model)
    answer_callback(token, str(callback.get("id", "")), f"Selected {model}")
    remove_inline_keyboard(token, callback)
    send_text(token, chat_id, f"Model changed to: {model}")
    logging.info("Model changed by Telegram user %s to %s", sender, model)



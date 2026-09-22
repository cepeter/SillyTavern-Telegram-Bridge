"""Handle System Prompt selection, disable, and pagination callbacks."""
from __future__ import annotations

def handle_system_prompt_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
    if data.startswith("systemprompt:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            page = int(value.split(":", 1)[1])
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_system_prompt_menu(token, chat_id, session.get("system_prompt") or "", message.get("message_id"), page)
            return True
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
        return True
    return False


def handle_note_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
    """Handle Author's Note cancel, disable, and input callbacks."""
    if data.startswith("note:"):
        action = data.split(":", 1)[1]
        if action == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            discard_panel_binding(db, chat_id, message.get("message_id"))
            close_panel_message(token, chat_id, callback)
            return True
        if action == "off":
            update_session(db, chat_id, session_id, operation_id=operation_id, operation_kind="author_note_off", author_note="")
            answer_callback(token, str(callback.get("id", "")), "Author's Note off")
            send_note_menu(token, chat_id, "", message.get("message_id"))
            return True
        if action == "input":
            pending_note = {"session_id": session_id, "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
            set_meta(db, f"note_input:{chat_id}", json.dumps(pending_note))
            answer_callback(token, str(callback.get("id", "")), "User input")
            discard_panel_binding(db, chat_id, message.get("message_id"))
            close_panel_message(token, chat_id, callback)
            pending_note["prompt_message_ids"] = send_text(token, chat_id, "Send Author's Note text (1–2,000 characters). Send /cancel to cancel.")
            set_meta(db, f"note_input:{chat_id}", json.dumps(pending_note))
            return True
        answer_callback(token, str(callback.get("id", "")), "Unknown note action")
        return True
    return False


def handle_language_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
    """Handle model response language selection and pagination callbacks."""
    if data.startswith("language:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            page = int(value.split(":", 1)[1])
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_language_menu(token, chat_id, session.get("response_language") or "auto", message.get("message_id"), page)
            return True
        if value == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            remove_inline_keyboard(token, callback)
            return True
        try:
            language = set_response_language(db, chat_id, session_id, value, operation_id=operation_id)
        except ValueError:
            answer_callback(token, str(callback.get("id", "")), "Language choice expired")
            return True
        answer_callback(token, str(callback.get("id", "")), "Language selected")
        remove_inline_keyboard(token, callback)
        send_text(token, chat_id, f"Model response language set to: {response_language_label(language)}.")
        return True
    return False


def handle_reset_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id, *, memory_service):
    """Handle reset confirmation and cancellation callbacks."""
    if data.startswith("reset:"):
        action = data.split(":", 1)[1]
        if action == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            remove_inline_keyboard(token, callback)
            return True
        if action != "confirm":
            answer_callback(token, str(callback.get("id", "")), "Unknown reset action")
            return True
        try:
            reset_session(db, token, chat_id, session, operation_id=operation_id, memory_service=memory_service)
        except Exception:
            logging.error("Reset failed for chat %s/session %s", chat_id, session_id, exc_info=True)
            answer_callback(token, str(callback.get("id", "")), "Reset failed; memory and session were preserved")
            send_text(token, chat_id, "Reset cancelled because Hindsight memory purge failed. No session data was deleted.")
            return True
        answer_callback(token, str(callback.get("id", "")), "Reset complete")
        remove_inline_keyboard(token, callback)
        send_text(token, chat_id, "Reset complete. The active session was cleared.")
        return True
    return False


def handle_swipe_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
    """Handle response variant browsing and keep/cancel callbacks."""
    if data.startswith("swipe:"):
        action = data.split(":", 1)[1]
        user_row, variants = last_user_variants(db, chat_id, session_id)
        if not variants:
            answer_callback(token, str(callback.get("id", "")), "No variants")
            remove_inline_keyboard(token, callback)
            return True
        current = int(get_meta(db, swipe_state_key(chat_id, session_id), str(variants[-1][0])))
        indexes = [int(row[0]) for row in variants]
        if action == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            remove_inline_keyboard(token, callback)
            return True
        if action in {"prev", "next"}:
            position = indexes.index(current) if current in indexes else 0
            position = (position - 1) % len(indexes) if action == "prev" else (position + 1) % len(indexes)
            answer_callback(token, str(callback.get("id", "")), f"Variant {indexes[position]}")
            edit_swipe_menu(token, db, callback, session_id, indexes[position], variants)
            return True
        if action == "keep":
            if user_row:
                delete_outgoing_messages(db, token, chat_id, session_id, int(user_row[0]))
            selected = keep_swipe_variant(db, chat_id, session_id, current)
            if selected is None:
                answer_callback(token, str(callback.get("id", "")), "Variant not found")
                return True
            answer_callback(token, str(callback.get("id", "")), "Kept")
            telegram_request(token, "editMessageText", {"chat_id": chat_id, "message_id": message.get("message_id"), "text": f"✅ Kept variant {current}\n\n{selected[:3900]}"})
            return True
        return True
    return False


def handle_expression_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
    """Handle manual, automatic, and disabled expression modes."""
    if not data.startswith("expression:"):
        return False
    value = data.split(":", 1)[1]
    if value.startswith("page:"):
        try:
            page = max(0, int(value.split(":", 1)[1]))
        except ValueError:
            page = 0
        answer_callback(token, str(callback.get("id", "")), "Page updated")
        send_expression_menu(token, chat_id, session, db, message.get("message_id"), page)
        return True
    if value == "cancel":
        answer_callback(token, str(callback.get("id", "")), "Cancelled")
        remove_inline_keyboard(token, callback)
        return True
    if value not in {"auto", "off"} and value not in discover_expression_assets(session["character_file"]):
        answer_callback(token, str(callback.get("id", "")), "Expression unavailable")
        return True
    set_meta(db, expression_mode_key(chat_id, session_id), value)
    set_meta(db, expression_last_key(chat_id, session_id), "")
    db.commit()
    answer_callback(token, str(callback.get("id", "")), "Expression updated")
    send_expression_menu(token, chat_id, session, db, message.get("message_id"))
    return True


def handle_sync_callback(
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
    sync_service: SyncService,
):
    """Handle Live API Sync callbacks."""
    if not data.startswith("sync:"):
        return False
    action = data.split(":", 1)[1]
    message_id = message.get("message_id")
    if action == "close":
        answer_callback(
            token,
            str(callback.get("id", "")),
            "Closed",
        )
        close_panel_message(token, chat_id, callback)
    elif action in {"menu", "status"}:
        answer_callback(
            token,
            str(callback.get("id", "")),
            "Sync status",
        )
        send_sync_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            sync_service=sync_service,
        )
    elif action == "realtime":
        result = sync_service.toggle_realtime(
            db,
            chat_id,
            session_id,
        )
        answer_callback(
            token,
            str(callback.get("id", "")),
            result[:200],
        )
        send_sync_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            sync_service=sync_service,
        )
    elif action == "now":
        result = sync_service.sync_now(
            db,
            chat_id,
            session_id,
        )
        answer_callback(
            token,
            str(callback.get("id", "")),
            result[:200],
        )
        send_sync_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            sync_service=sync_service,
        )
    else:
        answer_callback(
            token,
            str(callback.get("id", "")),
            "Unknown sync action",
        )
    return True


def handle_primary_panel_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id, *, memory_service, sync_service: SyncService):
    """Dispatch System Prompt, Note, language, reset, help, swipe, and expression callbacks."""
    if data.startswith("update:"):
        return handle_update_callback(token, callback, data, chat_id)
    if handle_expression_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
        return True
    if handle_system_prompt_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
        return True
    if handle_note_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
        return True
    if handle_language_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
        return True
    if handle_reset_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id, memory_service=memory_service):
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
        memory_service=memory_service,
    ):
        return True
    if handle_help_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
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
    ):
        return True
    return handle_swipe_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id)


def handle_character_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
    """Handle character selection, info, upload, and deletion callbacks."""
    message_id = message.get("message_id")
    if data == "character:protected":
        answer_callback(token, str(callback.get("id", "")), "Active/default character is protected")
        send_character_menu(token, chat_id, session["character_file"], message_id)
        return True
    if data == "character:menu":
        answer_callback(token, str(callback.get("id", "")), "Refreshed")
        send_character_menu(token, chat_id, session["character_file"], message.get("message_id"))
        return True
    if data == "character:info":
        answer_callback(token, str(callback.get("id", "")), "Info")
        send_character_info_menu(token, chat_id, message.get("message_id"))
        return True
    if data == "character:delete":
        answer_callback(token, str(callback.get("id", "")), "Delete")
        send_character_delete_menu(token, chat_id, session["character_file"], message.get("message_id"))
        return True
    if data == "character:upload":
        answer_callback(token, str(callback.get("id", "")), "Upload")
        telegram_request(token, "editMessageText", {"chat_id": chat_id, "message_id": message.get("message_id"), "text": "Send the character card as a Telegram Document (PNG with SillyTavern chara metadata). The upload will be validated and queued safely.", "reply_markup": {"inline_keyboard": [[{"text": "⬅️ Back", "callback_data": "character:menu"}, {"text": "❌ Close", "callback_data": "character:cancel"}]]}})
        return True
    if data.startswith("characterinfo:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            send_character_info_menu(token, chat_id, message.get("message_id"), int(value.split(":", 1)[1]))
            return True
        filename = resolve_dynamic_callback_token(value, "character", chat_id) or ""
        if not safe_character_path(filename):
            answer_callback(token, str(callback.get("id", "")), "Character choice expired")
            return True
        info = card_fields_from_file(filename)
        answer_callback(token, str(callback.get("id", "")), "Info")
        telegram_request(token, "editMessageText", {"chat_id": chat_id, "message_id": message.get("message_id"), "text": f"Character: {info['name']}\nFile: {filename}\nDescription: {len(info['description'])} chars\nPersonality: {len(info['personality'])} chars\nScenario: {len(info['scenario'])} chars\nFirst message: {len(info['first_mes'])} chars", "reply_markup": {"inline_keyboard": [[{"text": "⬅️ Back", "callback_data": "character:info"}, {"text": "❌ Close", "callback_data": "character:cancel"}]]}})
        return True
    if data.startswith("characterdelete:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            send_character_delete_menu(token, chat_id, session["character_file"], message.get("message_id"), int(value.split(":", 1)[1]))
            return True
        filename = resolve_dynamic_callback_token(value, "character", chat_id) or ""
        if not safe_character_path(filename) or filename == session["character_file"]:
            answer_callback(token, str(callback.get("id", "")), "Character choice invalid")
            return True
        answer_callback(token, str(callback.get("id", "")), "Confirm deletion")
        send_character_delete_confirm(token, chat_id, filename, message.get("message_id"))
        return True
    if data.startswith("characterdeleteconfirm:"):
        filename = resolve_dynamic_callback_token(data.split(":", 1)[1], "character", chat_id) or ""
        path = safe_character_path(filename)
        references = character_delete_references(db, filename) if path else []
        is_default = filename == DEFAULT_CHARACTER_FILE or (path and path.resolve() == CARD_FILE.resolve())
        if not path or filename == session["character_file"] or is_default or references:
            reason = "active/default/referenced character" if path else "character not found"
            answer_callback(token, str(callback.get("id", "")), f"Deletion refused: {reason}")
            send_character_menu(token, chat_id, session["character_file"], message_id)
            return True
        try:
            verify_character_card_backup(path, path.read_bytes())
        except OSError:
            answer_callback(token, str(callback.get("id", "")), "Deletion refused: backup verification failed")
            return True
        if operation_id is not None and not begin_operation(db, operation_id, "character_delete"):
            answer_callback(token, str(callback.get("id", "")), "Already processed")
            return True
        path.unlink(missing_ok=True)
        record_operation(db, operation_id, "character_delete")
        db.commit()
        answer_callback(token, str(callback.get("id", "")), "Deleted")
        send_character_menu(token, chat_id, session["character_file"], message.get("message_id"))
        return True
    if data.startswith("character:"):
        value = data.split(":", 1)[1]
        if not value.startswith("page:") and value != "cancel":
            value = resolve_dynamic_callback_token(value, "character", chat_id) or ""
        if value.startswith("page:"):
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_character_menu(token, chat_id, session["character_file"], message.get("message_id"), int(value.split(":", 1)[1]))
            return True
        if value == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            if group_setup_state(db, chat_id, session_id):
                set_meta(db, f"group_setup:{chat_id}", "")
            if get_meta(db, f"character_session_input:{chat_id}", ""):
                set_meta(db, f"character_session_input:{chat_id}", "")
            discard_panel_binding(db, chat_id, message.get("message_id"))
            close_panel_message(token, chat_id, callback)
        elif safe_character_path(value):
            character_name = card_fields_from_file(value)["name"]
            setup = group_setup_state(db, chat_id, session_id)
            if setup and setup.get("stage") == "character":
                return apply_group_setup_character(db, token, callback, answer_callback, chat_id, message, session_id, operation_id, value, character_name)
            set_meta(db, f"character_session_input:{chat_id}", json.dumps({"character_file": Path(value).name, "character_name": character_name, "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}))
            answer_callback(token, str(callback.get("id", "")), "Choose session")
            discard_panel_binding(db, chat_id, message.get("message_id"))
            close_panel_message(token, chat_id, callback)
            send_session_menu(token, chat_id, list_sessions(db, chat_id), session_id)
        else:
            answer_callback(token, str(callback.get("id", "")), "Character not found")
        return True
    return False


def handle_session_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id, *, memory_service):
    """Handle session selection, creation, and deletion callbacks."""
    if data == "session:protected":
        answer_callback(token, str(callback.get("id", "")), "Active session is protected")
        send_session_menu(token, chat_id, list_sessions(db, chat_id), session_id, message.get("message_id"))
        return True
    if data.startswith("sessiondeleteconfirm:"):
        target_session_id = resolve_dynamic_callback_token(data.split(":", 1)[1], "session", chat_id) or ""
        target = next((item for item in list_sessions(db, chat_id) if item["session_id"] == target_session_id), None)
        if target is None:
            answer_callback(token, str(callback.get("id", "")), "Session choice expired")
            return True
        deleted, reason = delete_session_data(db, chat_id, target_session_id, session_id, operation_id=operation_id, memory_service=memory_service)
        if not deleted:
            answer_callback(token, str(callback.get("id", "")), f"Deletion refused: {reason}")
            return True
        answer_callback(token, str(callback.get("id", "")), "Session deleted")
        remove_inline_keyboard(token, callback)
        send_session_menu(token, chat_id, list_sessions(db, chat_id), session_id, message.get("message_id"))
        return True
    if data.startswith("sessiondelete:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_session_delete_menu(token, chat_id, list_sessions(db, chat_id), session_id, message.get("message_id"), int(value.split(":", 1)[1]))
            return True
        target_session_id = resolve_dynamic_callback_token(value, "session", chat_id) or ""
        target = next((item for item in list_sessions(db, chat_id) if item["session_id"] == target_session_id), None)
        if target is None or target_session_id == session_id:
            answer_callback(token, str(callback.get("id", "")), "Only an inactive session can be deleted")
            return True
        answer_callback(token, str(callback.get("id", "")), "Confirm deletion")
        send_session_delete_confirm(token, chat_id, target_session_id, target["title"], message.get("message_id"))
        return True
    if data.startswith("session:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_session_menu(token, chat_id, list_sessions(db, chat_id), session_id, message.get("message_id"), int(value.split(":", 1)[1]))
            return True
        if value == "delete":
            answer_callback(token, str(callback.get("id", "")), "Delete session")
            send_session_delete_menu(token, chat_id, list_sessions(db, chat_id), session_id, message.get("message_id"))
            return True
        if value == "back":
            answer_callback(token, str(callback.get("id", "")), "Back")
            send_session_menu(token, chat_id, list_sessions(db, chat_id), session_id, message.get("message_id"))
            return True
        if value == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            set_meta(db, f"character_session_input:{chat_id}", "")
            remove_inline_keyboard(token, callback)
        elif value == "new":
            answer_callback(token, str(callback.get("id", "")), "Enter session name")
            start_session_name_input(db, token, chat_id, session, message=message)
        else:
            available = {item["session_id"] for item in list_sessions(db, chat_id)}
            if value in available:
                set_meta(db, f"active_session:{chat_id}", value)
                answer_callback(token, str(callback.get("id", "")), "Session selected")
                pending_character = pending_character_for_session(db, chat_id)
                if pending_character:
                    target_title = next((item["title"] for item in list_sessions(db, chat_id) if item["session_id"] == value), value)
                    update_session(db, chat_id, value, operation_id=operation_id, operation_kind="character_select", character_file=Path(pending_character["character_file"]).name)
                    set_meta(db, f"character_session_input:{chat_id}", "")
                remove_inline_keyboard(token, callback)
                if pending_character:
                    send_text(token, chat_id, f"Character selected for session '{target_title}': {pending_character.get('character_name') or Path(pending_character['character_file']).stem}")
                else:
                    send_text(token, chat_id, f"Session selected: {value}")
            else:
                answer_callback(token, str(callback.get("id", "")), "Session not found")
        return True
    return False


def handle_world_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
    """Handle World Info selection, upload, deletion, and pagination callbacks."""
    message_id = message.get("message_id")
    if data.startswith("worlddeleteconfirm:"):
        value = resolve_dynamic_callback_token(data.split(":", 1)[1], "world", chat_id) or ""
        try:
            delete_world_info_file(db, chat_id, value)
        except (ValueError, OSError) as exc:
            answer_callback(token, str(callback.get("id", "")), "Delete refused")
            send_text(token, chat_id, str(exc))
        else:
            answer_callback(token, str(callback.get("id", "")), "World Info deleted")
            send_world_menu(token, chat_id, session["world_file"], message_id, 0)
        return True
    if data.startswith("worlddelete:"):
        value = resolve_dynamic_callback_token(data.split(":", 1)[1], "world", chat_id) or ""
        if not safe_world_path(value):
            answer_callback(token, str(callback.get("id", "")), "World Info file not found")
            return True
        answer_callback(token, str(callback.get("id", "")), "Confirm deletion")
        send_panel_message(token, chat_id, f"Delete World Info '{Path(value).name}'? This cannot be undone.", {"inline_keyboard": [[{"text": "🗑️ Delete", "callback_data": "worlddeleteconfirm:" + dynamic_callback_token("world", value, chat_id)}, {"text": "Cancel", "callback_data": "world:cancel"}]]}, message_id)
        return True
    if data.startswith("world:"):
        setup = group_setup_state(db, chat_id, session_id)
        value = data.split(":", 1)[1]
        if not value.startswith("page:") and value not in {"cancel", "done", "off", "upload"}:
            value = resolve_dynamic_callback_token(value, "world", chat_id) or ""
        if value.startswith("page:"):
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_world_menu(token, chat_id, session["world_file"], message.get("message_id"), int(value.split(":", 1)[1]))
            return True
        if value == "upload":
            pending = {"session_id": session_id, "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
            set_meta(db, f"world_upload:{chat_id}", json.dumps(pending))
            answer_callback(token, str(callback.get("id", "")), "Send JSON document")
            discard_panel_binding(db, chat_id, message_id)
            send_text(token, chat_id, "Send the World Info JSON as a Telegram document. Use /cancel to abort.")
        elif value == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            if setup:
                set_meta(db, f"group_setup:{chat_id}", "")
            remove_inline_keyboard(token, callback)
        elif value == "done":
            answer_callback(token, str(callback.get("id", "")), "Saved")
            if setup:
                set_meta(db, f"group_setup:{chat_id}", "")
            remove_inline_keyboard(token, callback)
            if setup:
                send_group_menu(db, token, chat_id, session)
        elif value == "off":
            update_session(db, chat_id, session_id, operation_id=operation_id, operation_kind="world_clear", world_file="")
            session["world_file"] = ""
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
            session["world_file"] = encode_world_files(selected)
            answer_callback(token, str(callback.get("id", "")), status)
            send_world_menu(token, chat_id, encode_world_files(selected), message.get("message_id"), 0)
        else:
            answer_callback(token, str(callback.get("id", "")), "World Info file not found")
        return True
    return False


def handle_entity_panel_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id, *, memory_service, persona_service):
    """Dispatch character, session, persona, and World Info callbacks."""
    if handle_character_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
        return True
    if handle_session_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id, memory_service=memory_service):
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
    ):
        return True
    return handle_world_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id)


def handle_provider_model_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
    """Handle provider, model, and catalog-only callbacks."""
    message_id = message.get("message_id")
    if data.startswith("models:providers:"):
        page = int(data.rsplit(":", 1)[1])
        answer_callback(token, str(callback.get("id", "")), "Page")
        send_model_menu(token, chat_id, session["model_id"] or DEFAULT_MODEL, message_id=message_id, page=page)
        return True
    if data.startswith("models:model:"):
        parts = data.split(":")
        provider_id = resolve_dynamic_callback_token(parts[2], "provider", chat_id) or ""
        page = int(parts[3])
        answer_callback(token, str(callback.get("id", "")), "Page")
        send_model_menu(token, chat_id, session["model_id"] or DEFAULT_MODEL, provider_id, message_id, page)
        return True
    if data == "models:target":
        answer_callback(token, str(callback.get("id", "")), "Back to target")
        send_model_target_menu(token, chat_id, session["model_id"] or DEFAULT_MODEL, task_model_for_session(db, chat_id, session, "utility"), message_id)
        return True
    if data == "models:cancel":
        answer_callback(token, str(callback.get("id", "")), "Cancelled")
        remove_inline_keyboard(token, callback)
        return True
    if data == "models:back":
        answer_callback(token, str(callback.get("id", "")), "Back to providers")
        send_model_menu(token, chat_id, session["model_id"] or DEFAULT_MODEL, message_id=message_id)
        return True
    if data == "provider:health":
        answer_callback(token, str(callback.get("id", "")), "Health")
        send_provider_health_menu(token, chat_id, message_id)
        return True
    if data == "provider:refresh":
        _config, refreshed, failed = refresh_model_catalog(force=True)
        answer_callback(token, str(callback.get("id", "")), "Refreshed")
        send_text(token, chat_id, f"Model catalog refreshed: {refreshed} providers updated; {failed} failed.")
        send_model_menu(token, chat_id, session["model_id"] or DEFAULT_MODEL, message_id=message_id)
        return True
    if data == "provider:back":
        send_model_menu(token, chat_id, session["model_id"] or DEFAULT_MODEL, message_id=message_id)
        return True
    if data.startswith("provider:"):
        provider_id = resolve_dynamic_callback_token(data.split(":", 1)[1], "provider", chat_id) or ""
        answer_callback(token, str(callback.get("id", "")), "Provider selected")
        send_model_menu(token, chat_id, session["model_id"] or DEFAULT_MODEL, provider_id, message_id=message_id)
        return True
    if data.startswith("unsupported:"):
        provider_id = resolve_dynamic_callback_token(data.split(":", 1)[1], "provider", chat_id) or ""
        answer_callback(token, str(callback.get("id", "")), "Catalog only: adapter not enabled")
        send_text(token, chat_id, f"Provider '{provider_id}' is visible in the bridge catalog, but its adapter is not enabled yet.")
        return True
    if data.startswith("modeltarget:"):
        target = data.split(":", 1)[1]
        if target not in {"story", "utility"}:
            answer_callback(token, str(callback.get("id", "")), "Model target invalid")
            return True
        set_model_target_selection(db, chat_id, session_id, target)
        answer_callback(token, str(callback.get("id", "")), "Target selected")
        send_model_menu(token, chat_id, session["model_id"] or DEFAULT_MODEL, message_id=message_id)
        return True
    if not data.startswith("model:"):
        return False
    model = resolve_dynamic_callback_token(data.split(":", 1)[1], "model", chat_id) or ""
    if not model:
        answer_callback(token, str(callback.get("id", "")), "Model choice expired")
        return True
    target = get_model_target_selection(db, chat_id, session_id)
    if not target:
        answer_callback(token, str(callback.get("id", "")), "Choose Story or Utility first")
        send_model_target_menu(token, chat_id, session["model_id"] or DEFAULT_MODEL, task_model_for_session(db, chat_id, session, "utility"), message_id)
        return True
    if target == "story":
        update_session(db, chat_id, session_id, operation_id=operation_id, operation_kind="model_select", model_id=model)
        message = f"Story model updated: {model}"
    else:
        set_task_model(db, chat_id, session_id, model, "utility")
        message = f"Utility model updated: {model}"
    clear_model_target_selection(db, chat_id, session_id)
    answer_callback(token, str(callback.get("id", "")), "Model updated")
    send_text(token, chat_id, message)
    send_model_target_menu(token, chat_id, model if target == "story" else session["model_id"] or DEFAULT_MODEL, task_model_for_session(db, chat_id, session, "utility"), message_id)
    return True


# Explicit late imports replace transitional dependency injection.
import json
import logging
import time
from bridge.callback_tokens import (
    dynamic_callback_token,
    resolve_dynamic_callback_token,
)
from bridge.callbacks import (
    close_panel_message,
    discard_panel_binding,
)
from bridge.card_content import (
    active_world_files,
    card_fields_from_file,
    encode_world_files,
    get_system_prompt_choice,
    safe_character_path,
    safe_world_path,
)
from bridge.cards import (
    send_character_delete_confirm,
    send_character_delete_menu,
    send_character_info_menu,
    send_character_menu,
    send_panel_message,
    send_session_menu,
)
from bridge.catalog import (
    delete_world_info_file,
    refresh_model_catalog,
    send_model_menu,
    send_model_target_menu,
    send_provider_health_menu,
    send_world_menu,
)
from bridge.commands import send_note_menu
from bridge.config import (
    CARD_FILE,
    DEFAULT_CHARACTER_FILE,
    DEFAULT_MODEL,
    PENDING_SETTINGS_TTL_SECONDS,
)
from bridge.database import (
    begin_operation,
    clear_model_target_selection,
    get_meta,
    get_model_target_selection,
    record_operation,
    set_meta,
    set_model_target_selection,
    set_task_model,
    task_model_for_session,
)
from bridge.expressions import (
    discover_expression_assets,
    expression_last_key,
    expression_mode_key,
    send_expression_menu,
)
from bridge.generation import (
    edit_swipe_menu,
    keep_swipe_variant,
    last_user_variants,
    swipe_state_key,
)
from bridge.group_core import group_setup_state
from bridge.groups import (
    apply_group_setup_character,
    send_group_menu,
)
from bridge.help import send_system_prompt_menu
from bridge.help_details import handle_help_callback
from bridge.input_flows import (
    handle_persona_callback,
    pending_character_for_session,
)
from bridge.language import (
    response_language_label,
    send_language_menu,
    set_response_language,
)
from bridge.media import (
    delete_outgoing_messages,
    remove_inline_keyboard,
)
from bridge.message_commands import reset_session
from bridge.session_naming import start_session_name_input
from bridge.status_panels import (
    handle_prompt_and_feature_callback,
    send_sync_menu,
)
from bridge.sync_service import SyncService
from bridge.telegram import (
    character_delete_references,
    delete_session_data,
    list_sessions,
    send_session_delete_confirm,
    send_session_delete_menu,
    send_text,
    telegram_request,
    update_session,
    verify_character_card_backup,
)
from bridge.update import handle_update_callback
from pathlib import Path

"""Open one scoped free-form input action and close its originating panel."""
from __future__ import annotations

import threading

def _decode_pending_state(raw: str, meta_key: str) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        # Legacy settings_input state stored the raw setting key instead of JSON.
        return {"key": raw} if meta_key.startswith("settings_input:") else {}


def _pending_state(db, meta_key: str, session_id: str, token: str, chat_id: str) -> dict:
    raw = get_meta(db, meta_key, "")
    state = _decode_pending_state(raw, meta_key)
    if state and (state.get("session_id") not in {None, "", session_id} or float(state.get("expires_at", 0) or 0) < time.time()):
        delete_pending_input_prompts(token, chat_id, state)
        set_meta(db, meta_key, "")
        return {}
    return state


def _cancel_pending(db, token: str, chat_id: str, meta_key: str, state: dict) -> None:
    delete_pending_input_prompts(token, chat_id, state)
    set_meta(db, meta_key, "")


def start_text_action_input(db, token: str, chat_id: str, session_id: str, action: str, prompt: str, callback: dict | None = None) -> None:
    state = {"session_id": session_id, "action": action, "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
    meta_key = f"text_action_input:{chat_id}"
    if callback:
        message_id = (callback.get("message") or {}).get("message_id")
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(token, chat_id, callback)
    state["prompt_message_ids"] = send_text(token, chat_id, prompt + "\n\nSend /cancel to cancel.")
    set_meta(db, meta_key, json.dumps(state, ensure_ascii=False))


def handle_inline_text_action(db, token: str, api_key: str, chat_id: str, session: dict, fields: dict, action: str, value: str, operation_id: int | None = None, *, memory_service: MemoryService, persona_service=None) -> bool:
    """Run a bounded text action immediately when a command includes its value."""
    state = {"session_id": session["session_id"], "action": action, "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
    return _handle_text_action_input(db, token, api_key, chat_id, session, fields, value, state, operation_id, memory_service=memory_service, persona_service=persona_service)


def _handle_text_action_input(db, token: str, api_key: str, chat_id: str, session: dict, fields: dict, stripped: str, state: dict, operation_id: int | None, *, memory_service=None, persona_service=None) -> bool:
    meta_key = f"text_action_input:{chat_id}"
    if stripped.casefold() in {"/cancel", "cancel"}:
        _cancel_pending(db, token, chat_id, meta_key, state)
        send_text(token, chat_id, "Cancelled.")
        return True
    action = str(state.get("action") or "")
    value = stripped.strip()
    if not value:
        send_pending_input_message(db, token, chat_id, meta_key, state, "Input cannot be empty. Try again or send /cancel.")
        return True
    try:
        if action == "edit":
            edit_last_user(db, token, api_key, session, fields, chat_id, value[:12000], operation_id=operation_id, memory_service=memory_service, persona_service=persona_service)
        elif action == "remember":
            if len(value) > 4000 or not remember_fact(db, chat_id, session, fields, value):
                raise ValueError("Hindsight memory is unavailable or exceeds 4,000 characters")
            send_text(token, chat_id, "Memory queued for Hindsight.")
        elif action == "macro":
            handle_macro_command(db, token, chat_id, session, fields, "/macro " + value)
        elif action == "imagine":
            handle_imagine_prompt(token, chat_id, value)
        elif action == "memory_search":
            handle_memory_command(db, token, chat_id, session, fields, "/memory search " + value)
            send_memory_menu(token, chat_id, db)
        elif action == "databank_search":
            handle_data_bank_command(db, token, chat_id, "/databank search " + value)
            send_databank_menu(token, chat_id, db)
        elif action == "director_goal":
            if len(value) > 1200:
                raise ValueError("Director objective exceeds 1,200 characters")
            set_director_goal(db, chat_id, session["session_id"], value)
            send_director_goal_menu(token, chat_id, db, session)
        else:
            raise ValueError("Unknown text action")
    except ValueError as exc:
        send_pending_input_message(db, token, chat_id, meta_key, state, f"{exc}. Try again or send /cancel.")
        return True
    _cancel_pending(db, token, chat_id, meta_key, state)
    return True


def pending_character_for_session(db, chat_id: str) -> dict | None:
    """Load and validate a character waiting for session assignment."""
    meta_key = f"character_session_input:{chat_id}"
    state = _decode_pending_state(get_meta(db, meta_key, ""), meta_key)
    filename = str(state.get("character_file") or "")
    if not state or float(state.get("expires_at", 0) or 0) < time.time() or not safe_character_path(filename):
        if state:
            set_meta(db, meta_key, "")
        return None
    return state


def _handle_settings_input(db, token: str, chat_id: str, session_id: str, stripped: str, state: dict) -> bool:
    if stripped.casefold() in {"/cancel", "cancel"}:
        _cancel_pending(db, token, chat_id, f"settings_input:{chat_id}", state)
        send_settings_menu(token, chat_id, db, session_id)
        return True
    try:
        key, value = parse_generation_setting(str(state.get("key") or ""), stripped)
        update_generation_settings(db, chat_id, session_id, **{key: value})
    except ValueError as exc:
        send_pending_input_message(db, token, chat_id, f"settings_input:{chat_id}", state, f"Invalid {state.get('key')}: {exc} Try again or send /cancel.")
        return True
    _cancel_pending(db, token, chat_id, f"settings_input:{chat_id}", state)
    send_settings_menu(token, chat_id, db, session_id)
    return True


def _handle_preset_input(db, token: str, chat_id: str, session_id: str, stripped: str, state: dict) -> bool:
    meta_key = f"preset_save_input:{chat_id}"
    if stripped.casefold() in {"/cancel", "cancel"}:
        _cancel_pending(db, token, chat_id, meta_key, state)
        send_preset_menu(token, chat_id, db)
        return True
    preset_name = stripped.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", preset_name):
        send_pending_input_message(db, token, chat_id, meta_key, state, "Preset name must use 1–64 letters, numbers, hyphens, or underscores. Try again or send /cancel.")
        return True
    save_generation_preset(db, chat_id, preset_name, get_generation_settings(db, chat_id, session_id))
    _cancel_pending(db, token, chat_id, meta_key, state)
    send_text(token, chat_id, f"Preset saved: {preset_name}")
    send_preset_menu(token, chat_id, db)
    return True


def _handle_stt_input(db, token: str, chat_id: str, stripped: str, state: dict) -> bool:
    meta_key = f"stt_language_input:{chat_id}"
    if stripped.casefold() in {"/cancel", "cancel"}:
        _cancel_pending(db, token, chat_id, meta_key, state)
        send_stt_language_menu(token, chat_id, db)
        return True
    try:
        language = normalize_stt_language(stripped)
    except ValueError as exc:
        send_pending_input_message(db, token, chat_id, meta_key, state, f"Invalid STT language: {exc} Try again or send /cancel.")
        return True
    _cancel_pending(db, token, chat_id, meta_key, state)
    set_meta(db, f"stt_language:{chat_id}", language)
    send_text(token, chat_id, f"STT language set to {stt_language_label(language)}.")
    send_voice_input_menu(token, chat_id, db)
    return True


def _handle_note_input(db, token: str, chat_id: str, session: dict, stripped: str, state: dict, operation_id: int | None) -> bool:
    meta_key = f"note_input:{chat_id}"
    if stripped.casefold() in {"/cancel", "cancel"}:
        _cancel_pending(db, token, chat_id, meta_key, state)
        send_note_menu(token, chat_id, session.get("author_note") or "")
        return True
    note = stripped
    if not note or len(note) > 2000:
        send_pending_input_message(db, token, chat_id, meta_key, state, "Author's Note must contain 1–2,000 characters. Try again or send /cancel.")
        return True
    update_session(db, chat_id, session["session_id"], author_note=note, operation_id=operation_id, operation_kind="author_note_input")
    _cancel_pending(db, token, chat_id, meta_key, state)
    send_text(token, chat_id, "Author's Note updated for this session.")
    send_note_menu(token, chat_id, note)
    return True


PERSONA_EDIT_LOCK = threading.RLock()


def _persona_input_prompt(mode: str, current_name: str = "", persona_id: str = "", *, persona_service=None) -> str:
    """Return the user-facing prompt for creating or editing a persona."""
    persona_service = resolve_persona_service(persona_service)
    if mode == "create":
        return ("Create persona\n\nSend one line in this format:\n"
                "id | display name | persona description\n\n"
                "Use 1–64 letters, numbers, hyphens, or underscores for id. "
                "Description: 1–4,000 characters. Send /cancel to cancel.")
    persona = persona_service.get(persona_id) or {}
    description = str(persona.get("description") or "")
    if len(description) > 1600:
        description = description[:1600] + "…"
    if mode == "edit_name":
        return (f"Edit persona name\n\nCurrent name: {current_name}\n"
                f"Current description:\n{description}\n\n"
                "Send the new display name (1–120 characters). Send /cancel to cancel.")
    if mode == "edit_description":
        return (f"Edit persona description\n\nCurrent name: {current_name}\n"
                f"Current description:\n{description}\n\n"
                "Send the new description (1–4,000 characters). Send /cancel to cancel.")
    return (f"Edit persona\n\nCurrent name: {current_name}\n"
            f"Current description:\n{description}\n\n"
            "Send: display name | new description\nSend /cancel to cancel.")


def send_persona_edit_menu(token: str, chat_id: str, persona_id: str, message_id: int | None = None, *, persona_service=None) -> None:
    """Show current persona information before selecting an edit field."""
    persona_service = resolve_persona_service(persona_service)
    persona = persona_service.get(persona_id)
    if not persona:
        telegram_request(token, "editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": "Current persona is no longer available.", "reply_markup": {"inline_keyboard": [[{"text": "⬅️ Back", "callback_data": "persona:menu"}, {"text": "❌ Close", "callback_data": "persona:cancel"}]]}})
        return
    description = str(persona.get("description") or "")
    if len(description) > 1800:
        description = description[:1800] + "…"
    safe_id = html.escape(str(persona_id))
    safe_name = html.escape(str(persona.get("name") or persona_id))
    safe_description = html.escape(description)
    tags = ", ".join(str(tag) for tag in persona.get("tags", [])) or "none"
    text = (f"Persona information\n\nID: {safe_id}\nName: {safe_name}\n"
            f"Description (tap the copy icon):\n<pre>{safe_description}</pre>\n\nTags: {html.escape(tags)}\n\nChoose what to update:")
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "reply_markup": {"inline_keyboard": [
        [{"text": "✏️ Edit name", "callback_data": "persona:edit_name"}],
        [{"text": "📝 Edit description", "callback_data": "persona:edit_description"}],
        [{"text": "🔧 Edit name + description", "callback_data": "persona:edit_all"}],
        [{"text": "⬅️ Back", "callback_data": "persona:menu"}, {"text": "❌ Close", "callback_data": "persona:cancel"}],
    ]}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, "editMessageText" if message_id else "sendMessage", payload)


def start_persona_input(db, token: str, chat_id: str, session_id: str, mode: str, persona_id: str, callback: dict, *, persona_service=None) -> None:
    """Close the persona panel and start a scoped create/edit text input."""
    persona_service = resolve_persona_service(persona_service)
    state = {"session_id": session_id, "mode": mode, "persona_id": persona_id, "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
    meta_key = f"persona_input:{chat_id}"
    discard_panel_binding(db, chat_id, (callback.get("message") or {}).get("message_id"))
    close_panel_message(token, chat_id, callback)
    state["prompt_message_ids"] = send_text(
        token,
        chat_id,
        _persona_input_prompt(
            mode,
            persona_service.name(persona_id),
            persona_id,
            persona_service=persona_service,
        ),
    )
    set_meta(db, meta_key, json.dumps(state, ensure_ascii=False))




def _handle_persona_input(db, token: str, chat_id: str, session: dict, stripped: str, state: dict, operation_id: int | None, *, persona_service=None) -> bool:
    """Validate Persona input and delegate lifecycle changes to PersonaService."""
    persona_service = resolve_persona_service(persona_service)
    meta_key = f"persona_input:{chat_id}"
    if stripped.casefold() in {"/cancel", "cancel"}:
        _cancel_pending(db, token, chat_id, meta_key, state)
        send_persona_menu(
            token,
            chat_id,
            session.get("persona_id") or "",
            persona_service=persona_service,
        )
        return True
    mode = str(state.get("mode") or "")
    persona_id = str(state.get("persona_id") or "")
    current = persona_service.get(persona_id) if persona_id else None
    if mode == "create":
        parts = [part.strip() for part in stripped.split("|", 2)]
        if len(parts) != 3 or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", parts[0]):
            send_pending_input_message(db, token, chat_id, meta_key, state, "Use: id | display name | persona description. Try again or send /cancel.")
            return True
        requested_id, name, description = parts
        if not 1 <= len(name) <= 120 or not 1 <= len(description) <= 4000:
            send_pending_input_message(db, token, chat_id, meta_key, state, "Persona ID must be new; name 1–120 characters; description 1–4,000 characters. Try again or send /cancel.")
            return True
        target_id = requested_id
    elif mode in {"edit", "edit_all", "edit_name", "edit_description"} and current:
        current_name = str(current.get("name") or persona_id)
        current_description = str(current.get("description") or "")
        if mode == "edit_name":
            name, description = stripped, current_description
        elif mode == "edit_description":
            name, description = current_name, stripped
        else:
            parts = [part.strip() for part in stripped.split("|", 1)]
            name = current_name if len(parts) == 1 else parts[0]
            description = parts[0] if len(parts) == 1 else parts[1]
        if not 1 <= len(name) <= 120 or not 1 <= len(description) <= 4000:
            send_pending_input_message(db, token, chat_id, meta_key, state, "Display name must be 1–120 characters and description 1–4,000 characters. Try again or send /cancel.")
            return True
        target_id = persona_id
    else:
        _cancel_pending(db, token, chat_id, meta_key, state)
        send_persona_menu(
            token,
            chat_id,
            session.get("persona_id") or "",
            persona_service=persona_service,
        )
        return True
    try:
        if mode == "create":
            native_avatar = persona_service.create_and_select(
                db,
                chat_id,
                session["session_id"],
                target_id,
                name,
                description,
                operation_id=operation_id,
            )
        else:
            native_avatar = persona_service.update(
                target_id,
                name,
                description,
            )
    except ValueError as exc:
        if mode == "create" and (
            "already exists" in str(exc)
            or "Persona ID" in str(exc)
            or "Persona name" in str(exc)
            or "Persona description" in str(exc)
        ):
            send_pending_input_message(
                db,
                token,
                chat_id,
                meta_key,
                state,
                "Persona ID must be new; name 1–120 characters; description 1–4,000 characters. Try again or send /cancel.",
            )
            return True
        logging.warning("Native SillyTavern Persona save failed", exc_info=True)
        send_pending_input_message(db, token, chat_id, meta_key, state, f"Native Persona could not be saved: {exc}. Try again or send /cancel.")
        return True
    except Exception as exc:
        logging.warning("Native SillyTavern Persona save failed", exc_info=True)
        send_pending_input_message(db, token, chat_id, meta_key, state, f"Native Persona could not be saved: {exc}. Try again or send /cancel.")
        return True
    _cancel_pending(db, token, chat_id, meta_key, state)
    send_text(token, chat_id, f"Persona {'created and selected' if mode == 'create' else 'updated'}: {persona_service.name(native_avatar)}")
    send_persona_menu(
        token,
        chat_id,
        native_avatar if mode == "create" else session.get("persona_id") or "",
        persona_service=persona_service,
    )
    return True


def handle_pending_input(db: sqlite3.Connection, token: str, chat_id: str, session: dict, stripped: str, api_key: str = "", fields: dict | None = None, operation_id: int | None = None, *, memory_service=None, persona_service=None) -> bool:
    """Consume one scoped pending-input message, including cancel and validation."""
    session_id = session["session_id"]
    world_upload = _pending_state(db, f"world_upload:{chat_id}", session_id, token, chat_id)
    if world_upload:
        if stripped.casefold() in {"/cancel", "cancel"}:
            set_meta(db, f"world_upload:{chat_id}", "")
            send_text(token, chat_id, "World Info upload cancelled.")
        else:
            send_text(token, chat_id, "Please send the World Info JSON as a document, or use /cancel.")
        return True
    text_action = _pending_state(db, f"text_action_input:{chat_id}", session_id, token, chat_id)
    if text_action:
        action_fields = fields if fields is not None else card_fields_from_file(session["character_file"])
        return _handle_text_action_input(db, token, api_key, chat_id, session, action_fields, stripped, text_action, operation_id, memory_service=memory_service, persona_service=persona_service)
    session_name = _pending_state(db, f"session_name_input:{chat_id}", session_id, token, chat_id)
    if session_name:
        return handle_session_name_input(db, token, chat_id, session, stripped, session_name, operation_id)
    settings = _pending_state(db, f"settings_input:{chat_id}", session_id, token, chat_id)
    if settings.get("key"):
        return _handle_settings_input(db, token, chat_id, session_id, stripped, settings)
    preset = _pending_state(db, f"preset_save_input:{chat_id}", session_id, token, chat_id)
    if preset:
        return _handle_preset_input(db, token, chat_id, session_id, stripped, preset)
    stt = _pending_state(db, f"stt_language_input:{chat_id}", session_id, token, chat_id)
    if stt:
        return _handle_stt_input(db, token, chat_id, stripped, stt)
    persona = _pending_state(db, f"persona_input:{chat_id}", session_id, token, chat_id)
    if persona:
        return _handle_persona_input(
            db,
            token,
            chat_id,
            session,
            stripped,
            persona,
            operation_id,
            persona_service=persona_service,
        )
    note = _pending_state(db, f"note_input:{chat_id}", session_id, token, chat_id)
    if note:
        return _handle_note_input(db, token, chat_id, session, stripped, note, operation_id)
    return False


def send_persona_delete_confirm(token: str, chat_id: str, persona_id: str, message_id: int | None = None, *, persona_service=None) -> None:
    persona_service = resolve_persona_service(persona_service)
    token_value = dynamic_callback_token("persona", persona_id, chat_id)
    payload = {"chat_id": chat_id, "text": f"Delete Persona '{persona_service.name(persona_id)}'? Native Persona metadata will be removed; the avatar file will be preserved. This cannot be undone from the bridge.", "reply_markup": {"inline_keyboard": [[{"text": "✅ Confirm delete", "callback_data": "personadeleteconfirm:" + token_value}], [{"text": "❌ Cancel", "callback_data": "persona:menu"}]]}}
    send_panel_message(token, chat_id, payload["text"], payload["reply_markup"], message_id)


def handle_persona_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id, *, persona_service=None):
    """Handle persona selection, review, field editing, disable, and deletion callbacks."""
    persona_service = resolve_persona_service(persona_service)
    message_id = message.get("message_id")
    if data.startswith("persona:delete_page:"):
        page = max(0, int(data.rsplit(":", 1)[1]))
        send_persona_delete_menu(
            token,
            chat_id,
            session.get("persona_id") or "",
            message_id,
            page,
            persona_service=persona_service,
        )
        return True
    if data.startswith("persona:delete:"):
        target = resolve_dynamic_callback_token(data.split(":", 2)[2], "persona", chat_id) or ""
        if not target or target == session.get("persona_id"):
            answer_callback(token, str(callback.get("id", "")), "Cannot delete the current Persona")
            return True
        send_persona_delete_confirm(
            token,
            chat_id,
            target,
            message_id,
            persona_service=persona_service,
        )
        return True
    if data.startswith("personadeleteconfirm:"):
        persona_id = resolve_dynamic_callback_token(data.split(":", 1)[1], "persona", chat_id) or ""
        if not persona_id:
            answer_callback(token, str(callback.get("id", "")), "Persona not found")
            return True
        if persona_id == session.get("persona_id"):
            answer_callback(token, str(callback.get("id", "")), "Disable or switch the current Persona first")
            return True
        try:
            deleted = persona_service.delete_if_unused(db, persona_id)
        except ValueError as exc:
            if "used by another session" in str(exc):
                answer_callback(token, str(callback.get("id", "")), "Deletion refused: Persona is used by another session")
                return True
            logging.warning("Native Persona deletion failed", exc_info=True)
            answer_callback(token, str(callback.get("id", "")), "Persona deletion failed")
            return True
        except Exception:
            logging.warning("Native Persona deletion failed", exc_info=True)
            answer_callback(token, str(callback.get("id", "")), "Persona deletion failed")
            return True
        answer_callback(token, str(callback.get("id", "")), "Deleted" if deleted else "Persona not found")
        send_persona_menu(
            token,
            chat_id,
            session.get("persona_id") or "",
            message_id,
            persona_service=persona_service,
        )
        return True
    if not data.startswith("persona:"):
        return False
    value = data.split(":", 1)[1]
    message_id = message.get("message_id")
    field_actions = {"create", "edit", "edit_name", "edit_description", "edit_all", "delete"}
    if not value.startswith("page:") and value not in {"cancel", "off", "menu", *field_actions}:
        value = resolve_dynamic_callback_token(value, "persona", chat_id) or ""
    if value.startswith("page:"):
        answer_callback(token, str(callback.get("id", "")), "Page")
        send_persona_menu(
            token,
            chat_id,
            session["persona_id"],
            message_id,
            int(value.split(":", 1)[1]),
            persona_service=persona_service,
        )
        return True
    if value == "menu":
        answer_callback(token, str(callback.get("id", "")), "Personas")
        send_persona_menu(
            token,
            chat_id,
            session.get("persona_id") or "",
            message_id,
            persona_service=persona_service,
        )
        return True
    if value == "edit":
        persona_id = session.get("persona_id") or ""
        if not persona_service.get(persona_id):
            answer_callback(token, str(callback.get("id", "")), "Current persona not found")
            return True
        answer_callback(token, str(callback.get("id", "")), "Review persona")
        send_persona_edit_menu(
            token,
            chat_id,
            persona_id,
            message_id,
            persona_service=persona_service,
        )
        return True
    if value in {"create", "edit_name", "edit_description", "edit_all"}:
        persona_id = session.get("persona_id") or "" if value != "create" else ""
        if value != "create" and not persona_service.get(persona_id):
            answer_callback(token, str(callback.get("id", "")), "Current persona not found")
            return True
        answer_callback(token, str(callback.get("id", "")), "Enter persona text")
        start_persona_input(
            db,
            token,
            chat_id,
            session_id,
            value,
            persona_id,
            callback,
            persona_service=persona_service,
        )
        return True
    if value == "delete":
        answer_callback(token, str(callback.get("id", "")), "Choose an inactive Persona")
        send_persona_delete_menu(
            token,
            chat_id,
            session.get("persona_id") or "",
            message_id,
            persona_service=persona_service,
        )
        return True
    if value == "cancel":
        answer_callback(token, str(callback.get("id", "")), "Cancelled")
        remove_inline_keyboard(token, callback)
        return True
    if value == "off":
        persona_service.disable(
            db,
            chat_id,
            session_id,
            operation_id=operation_id,
        )
        answer_callback(token, str(callback.get("id", "")), "Persona off")
        remove_inline_keyboard(token, callback)
        send_text(token, chat_id, "Persona disabled for this session.")
        return True
    if persona_service.select(
        db,
        chat_id,
        session_id,
        value,
        operation_id=operation_id,
    ):
        answer_callback(token, str(callback.get("id", "")), "Persona selected")
        remove_inline_keyboard(token, callback)
        send_text(token, chat_id, f"Persona selected: {persona_service.name(value)}")
        return True
    answer_callback(token, str(callback.get("id", "")), "Persona not found")
    return True


# Explicit late imports replace transitional dependency injection.
import html
import json
import logging
import re
import sqlite3
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
    card_fields_from_file,
    safe_character_path,
)
from bridge.cards import (
    send_panel_message,
    send_persona_menu,
)
from bridge.commands import (
    edit_last_user,
    handle_macro_command,
    send_note_menu,
)
from bridge.common import delete_pending_input_prompts
from bridge.config import PENDING_SETTINGS_TTL_SECONDS
from bridge.database import (
    get_generation_settings,
    get_meta,
    parse_generation_setting,
    save_generation_preset,
    set_meta,
    update_generation_settings,
)
from bridge.director_goals import set_director_goal
from bridge.help import (
    send_databank_menu,
    send_memory_menu,
    send_preset_menu,
    send_settings_menu,
    send_stt_language_menu,
    send_voice_input_menu,
)
from bridge.image_generation import handle_imagine_prompt
from bridge.language import (
    normalize_stt_language,
    stt_language_label,
)
from bridge.media import remove_inline_keyboard
from bridge.memory import handle_memory_command
from bridge.memory_service import MemoryService
from bridge.memory_backend import remember_fact
from bridge.message_commands import send_pending_input_message
from bridge.persona_delete_panel import send_persona_delete_menu
from bridge.persona_sync import resolve_persona_service
from bridge.rag import handle_data_bank_command
from bridge.session_naming import handle_session_name_input
from bridge.status_panels import send_director_goal_menu
from bridge.telegram import (
    send_text,
    telegram_request,
    update_session,
)

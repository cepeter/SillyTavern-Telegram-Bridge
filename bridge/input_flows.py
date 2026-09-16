def _pending_state(db, meta_key: str, session_id: str, token: str, chat_id: str) -> dict:
    raw = get_meta(db, meta_key, "")
    try:
        state = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        state = {"key": raw} if meta_key.startswith("settings_input:") else {}
    if state and (state.get("session_id") not in {None, "", session_id} or float(state.get("expires_at", 0) or 0) < time.time()):
        delete_pending_input_prompts(token, chat_id, state)
        set_meta(db, meta_key, "")
        return {}
    return state


def _cancel_pending(db, token: str, chat_id: str, meta_key: str, state: dict) -> None:
    delete_pending_input_prompts(token, chat_id, state)
    set_meta(db, meta_key, "")


def pending_character_for_session(db, chat_id: str) -> dict | None:
    """Load and validate a character waiting for session assignment."""
    meta_key = f"character_session_input:{chat_id}"
    raw = get_meta(db, meta_key, "")
    try:
        state = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        state = {}
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


def handle_pending_input(db: sqlite3.Connection, token: str, chat_id: str, session: dict, stripped: str, operation_id: int | None = None) -> bool:
    """Consume one scoped pending-input message, including cancel and validation."""
    session_id = session["session_id"]
    settings = _pending_state(db, f"settings_input:{chat_id}", session_id, token, chat_id)
    if settings.get("key"):
        return _handle_settings_input(db, token, chat_id, session_id, stripped, settings)
    preset = _pending_state(db, f"preset_save_input:{chat_id}", session_id, token, chat_id)
    if preset:
        return _handle_preset_input(db, token, chat_id, session_id, stripped, preset)
    stt = _pending_state(db, f"stt_language_input:{chat_id}", session_id, token, chat_id)
    if stt:
        return _handle_stt_input(db, token, chat_id, stripped, stt)
    note = _pending_state(db, f"note_input:{chat_id}", session_id, token, chat_id)
    if note:
        return _handle_note_input(db, token, chat_id, session, stripped, note, operation_id)
    return False

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


PERSONA_EDIT_LOCK = threading.RLock()


def _persona_input_prompt(mode: str, current_name: str = "") -> str:
    """Return the user-facing prompt for creating or editing a persona."""
    if mode == "create":
        return ("Create persona\n\nSend one line in this format:\n"
                "id | display name | persona description\n\n"
                "Use 1–64 letters, numbers, hyphens, or underscores for id. "
                "Description: 1–4,000 characters. Send /cancel to cancel.")
    return (f"Edit persona: {current_name}\n\n"
            "Send the new description (1–4,000 characters). "
            "To change the display name too, send:\n"
            "display name | new description\n\n"
            "Send /cancel to cancel.")


def start_persona_input(db, token: str, chat_id: str, session_id: str, mode: str, persona_id: str, callback: dict) -> None:
    """Close the persona panel and start a scoped create/edit text input."""
    state = {"session_id": session_id, "mode": mode, "persona_id": persona_id, "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
    meta_key = f"persona_input:{chat_id}"
    discard_panel_binding(db, chat_id, (callback.get("message") or {}).get("message_id"))
    close_panel_message(token, chat_id, callback)
    state["prompt_message_ids"] = send_text(token, chat_id, _persona_input_prompt(mode, persona_name(persona_id)))
    set_meta(db, meta_key, json.dumps(state, ensure_ascii=False))


def _editable_personas() -> dict[str, dict[str, object]]:
    """Load and validate the persona catalog before allowing an edit."""
    if not PERSONA_FILE.exists():
        return {}
    raw = PERSONA_FILE.read_bytes()
    if len(raw) > 1_000_000:
        raise ValueError("persona catalog is too large")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("persona catalog must be a JSON object")
    result = {}
    for persona_id, persona in data.items():
        if not isinstance(persona_id, str) or not isinstance(persona, dict):
            raise ValueError("persona catalog has an invalid entry")
        if not isinstance(persona.get("name"), str) or not isinstance(persona.get("description"), str):
            raise ValueError("persona entries require name and description")
        tags = persona.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError("persona tags must be strings")
        result[persona_id] = dict(persona)
    return result


def _write_personas_atomically(personas: dict[str, dict[str, object]]) -> None:
    """Back up and atomically replace the private persona JSON catalog."""
    PERSONA_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = PERSONA_FILE.read_bytes() if PERSONA_FILE.exists() else None
    if existing is not None:
        backup = PERSONA_FILE.with_name(f"{PERSONA_FILE.name}.{time.time_ns()}.bak")
        backup.write_bytes(existing)
        os.chmod(backup, 0o600)
        if hashlib.sha256(backup.read_bytes()).digest() != hashlib.sha256(existing).digest():
            backup.unlink(missing_ok=True)
            raise OSError("persona backup checksum verification failed")
    payload = json.dumps(personas, ensure_ascii=False, indent=2) + "\n"
    temporary = PERSONA_FILE.with_name(f".{PERSONA_FILE.name}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(PERSONA_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def _handle_persona_input(db, token: str, chat_id: str, session: dict, stripped: str, state: dict, operation_id: int | None) -> bool:
    """Validate and persist one persona create/edit input."""
    meta_key = f"persona_input:{chat_id}"
    if stripped.casefold() in {"/cancel", "cancel"}:
        _cancel_pending(db, token, chat_id, meta_key, state)
        send_persona_menu(token, chat_id, session.get("persona_id") or "")
        return True
    mode = str(state.get("mode") or "")
    persona_id = str(state.get("persona_id") or "")
    with PERSONA_EDIT_LOCK:
        try:
            personas = _editable_personas()
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            send_pending_input_message(db, token, chat_id, meta_key, state, f"Persona catalog cannot be edited: {exc}. Try /cancel.")
            return True
        if mode == "create":
            parts = [part.strip() for part in stripped.split("|", 2)]
            if len(parts) != 3 or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", parts[0]):
                send_pending_input_message(db, token, chat_id, meta_key, state, "Use: id | display name | persona description. Try again or send /cancel.")
                return True
            persona_id, name, description = parts
            if persona_id in personas:
                send_pending_input_message(db, token, chat_id, meta_key, state, "That persona id already exists. Choose another id or send /cancel.")
                return True
            if not 1 <= len(name) <= 120 or not 1 <= len(description) <= 4000:
                send_pending_input_message(db, token, chat_id, meta_key, state, "Display name must be 1–120 characters and description 1–4,000 characters. Try again or send /cancel.")
                return True
            personas[persona_id] = {"name": name, "description": description, "tags": []}
        elif mode == "edit" and persona_id in personas:
            current = personas[persona_id]
            parts = [part.strip() for part in stripped.split("|", 1)]
            name = str(current.get("name") or persona_id) if len(parts) == 1 else parts[0]
            description = parts[0] if len(parts) == 1 else parts[1]
            if not 1 <= len(name) <= 120 or not 1 <= len(description) <= 4000:
                send_pending_input_message(db, token, chat_id, meta_key, state, "Display name must be 1–120 characters and description 1–4,000 characters. Try again or send /cancel.")
                return True
            current["name"], current["description"] = name, description
        else:
            _cancel_pending(db, token, chat_id, meta_key, state)
            send_persona_menu(token, chat_id, session.get("persona_id") or "")
            return True
        try:
            _write_personas_atomically(personas)
        except (OSError, TypeError, ValueError) as exc:
            send_pending_input_message(db, token, chat_id, meta_key, state, f"Persona could not be saved: {exc}. Try again or send /cancel.")
            return True
    if mode == "create":
        update_session(db, chat_id, session["session_id"], persona_id=persona_id, operation_id=operation_id, operation_kind="persona_create")
    _cancel_pending(db, token, chat_id, meta_key, state)
    send_text(token, chat_id, f"Persona {'created and selected' if mode == 'create' else 'updated'}: {persona_name(persona_id)}")
    send_persona_menu(token, chat_id, persona_id if mode == "create" else session.get("persona_id") or "")
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
    persona = _pending_state(db, f"persona_input:{chat_id}", session_id, token, chat_id)
    if persona:
        return _handle_persona_input(db, token, chat_id, session, stripped, persona, operation_id)
    note = _pending_state(db, f"note_input:{chat_id}", session_id, token, chat_id)
    if note:
        return _handle_note_input(db, token, chat_id, session, stripped, note, operation_id)
    return False

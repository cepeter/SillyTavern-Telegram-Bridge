def load_session(db: sqlite3.Connection, chat_id: str, session_id: str, default_model: str) -> dict[str, str]:
    row = db.execute("SELECT chat_id,session_id,title,character_file,model_id,persona_id,world_file,author_note,system_prompt FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, session_id)).fetchone()
    if row is None:
        raise ValueError(f"queued session no longer exists: {session_id}")
    get_generation_settings(db, chat_id, session_id)
    keys = ("chat_id", "session_id", "title", "character_file", "model_id", "persona_id", "world_file", "author_note", "system_prompt")
    return dict(zip(keys, row))


def ensure_session(db: sqlite3.Connection, chat_id: str, default_model: str) -> dict[str, str]:
    active_id = get_meta(db, f"active_session:{chat_id}", "default")
    row = db.execute("SELECT chat_id,session_id,title,character_file,model_id,persona_id,world_file,author_note,system_prompt FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, active_id)).fetchone()
    if row is None:
        now = time.time()
        row = (chat_id, active_id, "Default session", DEFAULT_CHARACTER_FILE,
               get_meta(db, "model", default_model), get_meta(db, "persona_id", "punto"),
               get_meta(db, "world_file", ""), "", "")
        db.execute("INSERT OR REPLACE INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,author_note,system_prompt,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (*row, now, now))
        db.commit()
    get_generation_settings(db, chat_id, active_id)
    keys = ("chat_id", "session_id", "title", "character_file", "model_id", "persona_id", "world_file", "author_note", "system_prompt")
    return dict(zip(keys, row))


def update_session(db: sqlite3.Connection, chat_id: str, session_id: str, operation_id: int | str | None = None, operation_kind: str = "session_update", **values) -> None:
    allowed = {"title", "character_file", "model_id", "persona_id", "world_file", "author_note", "system_prompt"}
    values = {k: v for k, v in values.items() if k in allowed}
    if not values:
        return
    if not begin_operation(db, operation_id, operation_kind):
        return
    assignments = ", ".join(f"{key}=?" for key in values)
    params = list(values.values()) + [time.time(), chat_id, session_id]
    db.execute(f"UPDATE sessions SET {assignments}, updated_at=? WHERE chat_id=? AND session_id=?", params)
    record_operation(db, operation_id, operation_kind)
    db.commit()


def create_session(db: sqlite3.Connection, chat_id: str, default_model: str, session_id: str | None = None) -> dict[str, str]:
    session_id = session_id or ("s" + str(int(time.time() * 1000)))
    now = time.time()
    row = (chat_id, session_id, "New session", DEFAULT_CHARACTER_FILE,
           get_meta(db, "model", default_model), get_meta(db, "persona_id", "punto"),
           get_meta(db, "world_file", ""), "", "")
    db.execute("INSERT OR IGNORE INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,author_note,system_prompt,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (*row, now, now))
    set_meta(db, f"active_session:{chat_id}", session_id)
    db.commit()
    stored = db.execute("SELECT chat_id,session_id,title,character_file,model_id,persona_id,world_file,author_note,system_prompt FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, session_id)).fetchone()
    return dict(zip(("chat_id", "session_id", "title", "character_file", "model_id", "persona_id", "world_file", "author_note", "system_prompt"), stored or row))


def list_sessions(db: sqlite3.Connection, chat_id: str) -> list[dict[str, str]]:
    rows = db.execute("SELECT chat_id,session_id,title,character_file,model_id,persona_id,world_file,author_note,system_prompt FROM sessions WHERE chat_id=? ORDER BY updated_at DESC", (chat_id,)).fetchall()
    keys = ("chat_id", "session_id", "title", "character_file", "model_id", "persona_id", "world_file", "author_note", "system_prompt")
    return [dict(zip(keys, row)) for row in rows]


def allowed_users() -> set[str]:
    value = os.environ.get("SILLYTAVERN_TELEGRAM_ALLOWED_USERS", DEFAULT_ALLOWED_USER)
    return {x.strip() for x in value.split(",") if x.strip()}


def telegram_request(token: str, method: str, payload: dict | None = None) -> dict:
    request_payload = dict(payload or {})
    scoped_chat_id = str(request_payload.get("chat_id", "")) if request_payload.get("chat_id") is not None else ""
    if request_payload.get("chat_id") is not None:
        chat_id, thread_id = parse_topic_scope(str(request_payload["chat_id"]))
        request_payload["chat_id"] = chat_id
        if thread_id is not None and method in {"sendMessage", "sendPhoto", "sendVoice", "sendDocument", "sendChatAction"}:
            request_payload["message_thread_id"] = thread_id
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(request_payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=65) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} failed")
    if request_payload.get("reply_markup") and method in {"sendMessage", "editMessageText"}:
        bound_session = panel_session_context()
        api_result = result.get("result") or {}
        bound_message_id = api_result.get("message_id") if isinstance(api_result, dict) else None
        bound_message_id = bound_message_id or request_payload.get("message_id")
        if bound_session and bound_message_id:
            panel_db = db_connect()
            try:
                bind_panel_session(panel_db, scoped_chat_id, bound_message_id, bound_session)
            finally:
                panel_db.close()
    return result["result"]


def download_telegram_file(token: str, file_id: str, max_bytes: int = IMPORT_MAX_BYTES) -> bytes:
    file_info = telegram_request(token, "getFile", {"file_id": file_id})
    file_path = file_info.get("file_path")
    if not file_path:
        raise ValueError("Telegram did not return a file path")
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    with urllib.request.urlopen(urllib.request.Request(url), timeout=120) as response:
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"Telegram file exceeds {max_bytes // (1024 * 1024)} MB")
    return raw


def parse_sillytavern_jsonl(raw: bytes) -> tuple[dict, list[tuple[str, str]]]:
    records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if not records:
        raise ValueError("empty JSONL file")
    metadata = records[0].get("chat_metadata", {}) if isinstance(records[0], dict) else {}
    messages = []
    total_chars = 0
    for record in records[1:] if metadata else records:
        if not isinstance(record, dict) or record.get("is_system") or "mes" not in record:
            continue
        role = "user" if record.get("is_user") else "assistant"
        content = str(record.get("mes") or "").strip()
        if len(content) > IMPORT_MAX_MESSAGE_CHARS:
            raise ValueError(f"JSONL message exceeds {IMPORT_MAX_MESSAGE_CHARS} characters")
        total_chars += len(content)
        if total_chars > IMPORT_MAX_TOTAL_CHARS:
            raise ValueError(f"JSONL transcript exceeds {IMPORT_MAX_TOTAL_CHARS} characters")
        if content:
            messages.append((role, content))
    if not messages:
        raise ValueError("JSONL contains no user/assistant messages")
    return metadata if isinstance(metadata, dict) else {}, messages


def import_chat_session(db: sqlite3.Connection, chat_id: str, raw: bytes, default_model: str, operation_id: int | str | None = None) -> dict[str, str]:
    metadata, imported_messages = parse_sillytavern_jsonl(raw)
    deterministic_session_id = f"import-job-{operation_id}" if operation_id is not None else None
    phase_key = f"import_phase:{operation_id}" if operation_id is not None else ""
    phase = get_meta(db, phase_key, "created") if phase_key else "created"
    session = create_session(db, chat_id, default_model, session_id=deterministic_session_id)
    if phase == "complete":
        return session
    if phase == "created":
        values = {"title": str(metadata.get("name") or "Imported chat")[:120]}
        character_file = str(metadata.get("character_file") or "")
        if safe_character_path(character_file):
            values["character_file"] = Path(character_file).name
        if metadata.get("model"):
            values["model_id"] = str(metadata["model"])[:200]
        persona_id = str(metadata.get("persona") or "")
        if persona_id and get_persona(persona_id):
            values["persona_id"] = persona_id
        raw_worlds = metadata.get("world_info")
        world_candidates = raw_worlds if isinstance(raw_worlds, list) else ([raw_worlds] if raw_worlds else [])
        valid_worlds = [Path(str(name)).name for name in world_candidates if safe_world_path(str(name))]
        if valid_worlds:
            values["world_file"] = encode_world_files(valid_worlds)
        values["author_note"] = str(metadata.get("author_note") or "")[:2000]
        values["system_prompt"] = str(metadata.get("system_prompt") or "")[:8000]
        update_session(db, chat_id, session["session_id"], **values)
        imported_settings = metadata.get("generation_settings")
        if isinstance(imported_settings, dict):
            normalized = {}
            for key, raw_value in imported_settings.items():
                if key not in GENERATION_DEFAULTS:
                    continue
                try:
                    normalized[key] = parse_generation_setting(key, str(raw_value))[1]
                except ValueError:
                    continue
            if normalized:
                update_generation_settings(db, chat_id, session["session_id"], **normalized)
        for role, content in imported_messages:
            db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)", (chat_id, session["session_id"], role, content, time.time()))
            time.sleep(0.001)
        db.commit()
        if phase_key:
            set_meta(db, phase_key, "messages_loaded")
        phase = "messages_loaded"
    if phase == "messages_loaded":
        imported_summary = str(metadata.get("session_summary") or "")[:SUMMARY_MAX_CHARS]
        if imported_summary:
            last_rowid = int(db.execute("SELECT COALESCE(MAX(rowid), 0) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"])).fetchone()[0])
            db.execute("INSERT OR REPLACE INTO session_summaries(chat_id,session_id,summary,covered_until_rowid,updated_at) VALUES(?,?,?,?,?)", (chat_id, session["session_id"], imported_summary, last_rowid, time.time()))
            db.commit()
        if phase_key:
            set_meta(db, phase_key, "summary_loaded")
        phase = "summary_loaded"
    if phase == "summary_loaded":
        last_user = None
        for role, content in imported_messages:
            if role == "user":
                last_user = content
            elif role == "assistant" and last_user is not None:
                save_response_variant(db, chat_id, session["session_id"], last_user, content)
        if phase_key:
            set_meta(db, phase_key, "complete")
    return ensure_session(db, chat_id, default_model)


def process_telegram_image(db: sqlite3.Connection, token: str, chat_id: str, file_id: str, caption: str, default_model: str, file_size: int = 0, telegram_message_id: int | None = None, queued_session_id: str | None = None) -> None:
    if file_size > IMAGE_MAX_BYTES:
        send_text(token, chat_id, "Image terlalu besar. Batasnya 8 MB.")
        return
    image_bytes = download_telegram_file(token, file_id, IMAGE_MAX_BYTES)
    session = load_session(db, chat_id, queued_session_id, default_model) if queued_session_id else ensure_session(db, chat_id, default_model)
    fields = card_fields_from_file(session["character_file"])
    process_image_message(db, token, os.environ.get("LLM_API_KEY", ""), session, fields, chat_id, caption, image_bytes, telegram_message_id=telegram_message_id)


def verify_character_card_backup(target: Path, raw: bytes) -> Path:
    CHARACTER_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = CHARACTER_BACKUP_DIR / target.name
    versioned = CHARACTER_BACKUP_DIR / f"{target.stem}.{time.time_ns()}{target.suffix}"
    for destination in (versioned, backup):
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(raw)
        if hashlib.sha256(temporary.read_bytes()).digest() != hashlib.sha256(raw).digest():
            temporary.unlink(missing_ok=True)
            raise OSError("character-card backup checksum verification failed")
        temporary.replace(destination)
    return backup


def character_backup_versions(name: str) -> list[Path]:
    target = Path(name)
    return sorted(CHARACTER_BACKUP_DIR.glob(f"{target.stem}.*{target.suffix}"), reverse=True)


def character_delete_references(db: sqlite3.Connection, filename: str) -> list[str]:
    references = []
    for chat_id, session_id in db.execute("SELECT chat_id,session_id FROM sessions WHERE character_file=?", (filename,)).fetchall():
        references.append(f"session:{chat_id}/{session_id}")
    for chat_id, session_id, members_json in db.execute("SELECT chat_id,session_id,members_json FROM group_sessions").fetchall():
        try:
            members = json.loads(members_json or "[]")
        except json.JSONDecodeError:
            members = []
        if filename in members:
            references.append(f"group:{chat_id}/{session_id}")
    return references


def prune_character_backups(name: str, keep: int = 10) -> None:
    versions = character_backup_versions(name)
    for stale in versions[keep:]:
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            logging.warning("Could not prune character backup %s", stale, exc_info=True)


def import_character_card(db: sqlite3.Connection, token: str, chat_id: str, filename: str, raw: bytes) -> None:
    if len(raw) > RAG_MAX_FILE_BYTES:
        send_text(token, chat_id, "Character card terlalu besar. Batasnya 10 MB.")
        return
    try:
        fields = card_fields(parse_png_chara_bytes(raw))
    except Exception:
        send_text(token, chat_id, "PNG ini bukan character card SillyTavern yang valid; metadata chara tidak ditemukan.")
        return
    stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in fields["name"]).strip("_") or Path(filename).stem or "character"
    if len(stem.encode("utf-8")) > 80:
        stem = stem.encode("utf-8")[:64].decode("utf-8", "ignore").rstrip("_-") + "-" + hashlib.sha256(raw).hexdigest()[:8]
    target = CHARACTER_DIR / f"{stem}.png"
    if target.exists() and target.read_bytes() != raw:
        target = CHARACTER_DIR / f"{stem}-{hashlib.sha256(raw).hexdigest()[:8]}.png"
    CHARACTER_DIR.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() == raw:
            try:
                backup = verify_character_card_backup(target, raw)
                prune_character_backups(target.name)
            except OSError:
                send_text(token, chat_id, "Character card exists, but backup verification failed; no changes made.")
                return
            send_text(token, chat_id, f"Character card already installed: {fields['name']}. Backup verified: {backup.name}.")
            return
    try:
        backup = verify_character_card_backup(target, raw)
        target.write_bytes(raw)
        prune_character_backups(target.name)
    except OSError:
        send_text(token, chat_id, "Character card backup verification failed; card was not installed.")
        return
    send_text(token, chat_id, f"Character card imported: {fields['name']} ({target.name}). Backup verified: {backup.name}.")


def import_telegram_document(db: sqlite3.Connection, token: str, chat_id: str, document: dict, default_model: str, telegram_message_id: int | None = None, operation_id: int | str | None = None) -> None:
    filename = str(document.get("file_name") or "document")
    suffix = Path(filename).suffix.casefold()
    file_size = int(document.get("file_size") or 0)
    if suffix == ".png":
        raw = download_telegram_file(token, str(document.get("file_id") or ""), RAG_MAX_FILE_BYTES)
        try:
            parse_png_chara_bytes(raw)
        except Exception:
            session = ensure_session(db, chat_id, default_model)
            fields = card_fields_from_file(session["character_file"])
            process_image_message(db, token, os.environ.get("LLM_API_KEY", ""), session, fields, chat_id, str(document.get("caption") or ""), raw, mime_type="image/png", telegram_message_id=telegram_message_id)
        else:
            import_character_card(db, token, chat_id, filename, raw)
        return
    if suffix == ".jsonl":
        if file_size > IMPORT_MAX_BYTES:
            send_text(token, chat_id, "File import terlalu besar. Batasnya 10 MB.")
            return
        raw = download_telegram_file(token, str(document.get("file_id")))
        session = import_chat_session(db, chat_id, raw, default_model, operation_id=operation_id)
        send_text(token, chat_id, f"Chat imported into session {session['session_id']} ({session['title']}).")
        return
    if suffix not in RAG_SUPPORTED_SUFFIXES:
        send_text(token, chat_id, "Format Data Bank tidak didukung. Gunakan PDF, TXT, MD, JSON, YAML, CSV, HTML, XML, atau DOCX.")
        return
    if file_size > RAG_MAX_FILE_BYTES:
        send_text(token, chat_id, "Data Bank file terlalu besar. Batasnya 10 MB.")
        return
    raw = download_telegram_file(token, str(document.get("file_id")), RAG_MAX_FILE_BYTES)
    status, chunks = add_data_bank_document(db, chat_id, filename, raw)
    if status == "duplicate":
        send_text(token, chat_id, f"Data Bank already contains {filename} ({chunks} chunks).")
    else:
        send_text(token, chat_id, f"Added {filename} to Data Bank ({chunks} chunks). RAG is {rag_mode(db, chat_id)}.")


def split_telegram_text(text: str, limit: int = MAX_TELEGRAM_LENGTH) -> list[str]:
    text = str(text)
    chunks = []
    start = 0
    units = 0
    for index, character in enumerate(text):
        width = len(character.encode("utf-16-le")) // 2
        if units and units + width > limit:
            chunks.append(text[start:index])
            start = index
            units = 0
        units += width
    if start < len(text) or not chunks:
        chunks.append(text[start:])
    return chunks


def send_text(token: str, chat_id: str, text: str) -> list[int]:
    message_ids = []
    for chunk in split_telegram_text(text):
        result = telegram_request(token, "sendMessage", {
            "chat_id": chat_id,
            "text": chunk,
        })
        if result.get("message_id") is not None:
            message_ids.append(int(result["message_id"]))
    return message_ids



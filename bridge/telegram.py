from __future__ import annotations

from bridge.callback_tokens import (
    dynamic_callback_token,
)

from bridge.card_content import (
    active_world_files,
    card_fields,
    card_fields_from_file,
    encode_world_files,
    parse_png_chara_bytes,
    safe_world_path,
)

from bridge.common import (
    Path,
    hashlib,
    json,
    logging,
    os,
    re,
    sqlite3,
    time,
    urllib,
)

from bridge.config import (
    CATALOG_MAX_ITEMS,
    CHARACTER_DIR,
    DEFAULT_CHARACTER_FILE,
    RAG_MAX_FILE_BYTES,
    RAG_SUPPORTED_SUFFIXES,
)

from bridge.database import (
    begin_operation,
    bind_panel_session,
    db_connect,
    get_generation_settings,
    get_meta,
    optimize_database,
    record_operation,
    run_write_txn,
    set_meta,
)

from bridge.memory_backend import (
    hindsight_session_lock,
)

from bridge.panel_utils import (
    panel_label,
    panel_navigation,
    panel_page,
)

from bridge.rag_core import (
    add_data_bank_document,
    data_bank_document_versions,
    rag_mode,
)

from bridge.composition import RequestContext

from bridge.config import CHARACTER_BACKUP_DIR, SYNC_MAX_BYTES
from bridge.common import MAX_TELEGRAM_LENGTH

_SESSION_COLUMNS = ("chat_id", "session_id", "title", "character_file", "model_id", "persona_id", "world_file", "author_note", "system_prompt", "response_language")
_SESSION_COLUMN_SQL = ", ".join(_SESSION_COLUMNS)


def _session_row_dict(row) -> dict[str, str]:
    return dict(zip(_SESSION_COLUMNS, row))


def _default_session_persona(db: sqlite3.Connection) -> str:
    return default_persona_id()


def _native_default_world() -> str:
    try:
        settings = json.loads(NATIVE_PERSONA_SETTINGS_FILE.read_text(encoding="utf-8"))
        selected = (((settings.get("world_info_settings") or {}).get("world_info") or {}).get("globalSelect") or [])
        if isinstance(selected, list):
            return encode_world_files([str(name) for name in selected])
    except (OSError, ValueError, TypeError, AttributeError):
        logging.warning("Could not read native SillyTavern World Info defaults", exc_info=True)
    return ""


def _default_session_world(db: sqlite3.Connection) -> str:
    return _native_default_world()


def _normalize_session_defaults(db: sqlite3.Connection, session: dict[str, str]) -> dict[str, str]:
    persona_id = session.get("persona_id") or ""
    world_file = session.get("world_file") or ""
    replacement_persona = persona_id if not persona_id or get_persona(persona_id) else _default_session_persona(db)
    replacement_world = world_file if not world_file or any(safe_world_path(name) for name in active_world_files(world_file)) else _default_session_world(db)
    changes = {}
    if replacement_persona != persona_id:
        changes["persona_id"] = replacement_persona
    if replacement_world != world_file:
        changes["world_file"] = replacement_world
    if changes:
        assignments = ", ".join(f"{key}=?" for key in changes)
        db.execute(f"UPDATE sessions SET {assignments}, updated_at=? WHERE chat_id=? AND session_id=?", (*changes.values(), time.time(), session["chat_id"], session["session_id"]))  # nosec B608 - assignments are allowlisted session columns
        db.commit()
        session.update(changes)
    return session


def load_session(db: sqlite3.Connection, chat_id: str, session_id: str, default_model: str) -> dict[str, str]:
    row = db.execute(f"SELECT {_SESSION_COLUMN_SQL} FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, session_id)).fetchone()  # nosec B608 - column list is a fixed module constant
    if row is None:
        raise ValueError(f"queued session no longer exists: {session_id}")
    get_generation_settings(db, chat_id, session_id)
    return _normalize_session_defaults(db, _session_row_dict(row))


def ensure_session(db: sqlite3.Connection, chat_id: str, default_model: str) -> dict[str, str]:
    active_id = get_meta(db, f"active_session:{chat_id}", "default")
    row = db.execute(f"SELECT {_SESSION_COLUMN_SQL} FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, active_id)).fetchone()  # nosec B608 - column list is a fixed module constant
    if row is None:
        now = time.time()
        row = (chat_id, active_id, "Default session", DEFAULT_CHARACTER_FILE,
               get_meta(db, "model", default_model), _default_session_persona(db),
               _default_session_world(db), "", "", "auto")
        db.execute(f"INSERT OR REPLACE INTO sessions({_SESSION_COLUMN_SQL},created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (*row, now, now))
        db.commit()
    get_generation_settings(db, chat_id, active_id)
    return _normalize_session_defaults(db, _session_row_dict(row))


def update_session(db: sqlite3.Connection, chat_id: str, session_id: str, operation_id: int | str | None = None, operation_kind: str = "session_update", **values) -> None:
    allowed = {"title", "character_file", "model_id", "persona_id", "world_file", "author_note", "system_prompt", "response_language"}
    values = {k: v for k, v in values.items() if k in allowed}
    if not values:
        return
    if not begin_operation(db, operation_id, operation_kind):
        return
    assignments = ", ".join(f"{key}=?" for key in values)
    params = list(values.values()) + [time.time(), chat_id, session_id]
    def write():
        db.execute(f"UPDATE sessions SET {assignments}, updated_at=? WHERE chat_id=? AND session_id=?", params)  # nosec B608 - assignments are allowlisted session columns
        record_operation(db, operation_id, operation_kind)
        db.commit()
    run_write_txn(db, write)


def create_session(db: sqlite3.Connection, chat_id: str, default_model: str, session_id: str | None = None, title: str = "New session") -> dict[str, str]:
    session_id = session_id or ("s" + str(int(time.time() * 1000)))
    title = normalize_session_title(title)
    now = time.time()
    row = (chat_id, session_id, title, DEFAULT_CHARACTER_FILE,
           get_meta(db, "model", default_model), _default_session_persona(db),
           _default_session_world(db), "", "", "auto")
    def write():
        db.execute(f"INSERT OR IGNORE INTO sessions({_SESSION_COLUMN_SQL},created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (*row, now, now))
        set_meta(db, f"active_session:{chat_id}", session_id)
        db.commit()
    run_write_txn(db, write)
    stored = db.execute(f"SELECT {_SESSION_COLUMN_SQL} FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, session_id)).fetchone()  # nosec B608 - column list is a fixed module constant
    return _session_row_dict(stored or row)


def list_sessions(db: sqlite3.Connection, chat_id: str) -> list[dict[str, str]]:
    rows = db.execute(f"SELECT {_SESSION_COLUMN_SQL} FROM sessions WHERE chat_id=? ORDER BY updated_at DESC", (chat_id,)).fetchall()  # nosec B608 - column list is a fixed module constant
    return [_session_row_dict(row) for row in rows]


def send_session_delete_menu(token: str, chat_id: str, sessions: list[dict[str, str]], active_id: str, message_id: int | None = None, page: int = 0, *, request_context) -> None:
    options = [(item["session_id"], item["title"] or item["session_id"]) for item in sessions if item["session_id"] != active_id]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [[{"text": panel_label(label), "callback_data": "sessiondelete:" + dynamic_callback_token("session", session_id, chat_id, db=request_context.db)}] for session_id, label in page_options]
    navigation = panel_navigation("sessiondelete", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "⬅️ Back", "callback_data": "session:back"}, {"text": "❌ Close", "callback_data": "session:cancel"}])
    text = f"Choose an inactive session to delete (page {current_page + 1}/{total_pages}). Session-scoped Hindsight documents are deleted; memories from other sessions remain."
    send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id, request_context=request_context)


def send_session_delete_confirm(token: str, chat_id: str, session_id: str, title: str, message_id: int | None = None, *, request_context) -> None:
    token_value = dynamic_callback_token("session", session_id, chat_id, db=request_context.db)
    payload = {"chat_id": chat_id, "text": f"Delete session '{panel_label(title)}'?\n\nThis removes its SQLite transcript, variants, summary, generation settings, group state, failed turns, session record, and session-scoped Hindsight documents. Memories from other sessions remain. The active session cannot be deleted. Cleanup fails closed if Hindsight is unavailable. This cannot be undone.", "reply_markup": {"inline_keyboard": [[{"text": "✅ Confirm delete", "callback_data": "sessiondeleteconfirm:" + token_value}, {"text": "❌ Cancel", "callback_data": "session:back"}]]}}
    send_panel_message(token, chat_id, payload["text"], payload["reply_markup"], message_id, request_context=request_context)


def delete_session_data(db: sqlite3.Connection, chat_id: str, target_session_id: str, active_session_id: str, operation_id: int | str | None = None, *, memory_service: MemoryService) -> tuple[bool, str]:
    if target_session_id == active_session_id:
        return False, "active session"
    exists = db.execute("SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, target_session_id)).fetchone()
    if not exists:
        return False, "session not found"
    busy = db.execute("SELECT 1 FROM jobs WHERE chat_id=? AND session_id=? AND state IN ('queued','scheduled','running') LIMIT 1", (chat_id, target_session_id)).fetchone()
    if busy:
        return False, "session has active jobs"
    with hindsight_session_lock(chat_id, target_session_id):
        if not db.execute("SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, target_session_id)).fetchone():
            return False, "session not found"
        busy = db.execute("SELECT 1 FROM jobs WHERE chat_id=? AND session_id=? AND state IN ('queued','scheduled','running') LIMIT 1", (chat_id, target_session_id)).fetchone()
        if busy:
            return False, "session has active jobs"
        try:
            memory_service.purge_session(db, chat_id, target_session_id)
        except RuntimeError:
            return False, "Hindsight cleanup failed; session was preserved"
        if operation_id is not None and not begin_operation(db, operation_id, "session_delete"):
            return False, "already processed"
        db.execute("DELETE FROM messages WHERE chat_id=? AND session_id=?", (chat_id, target_session_id))
        db.execute("DELETE FROM response_variants WHERE chat_id=? AND session_id=?", (chat_id, target_session_id))
        db.execute("DELETE FROM session_summaries WHERE chat_id=? AND session_id=?", (chat_id, target_session_id))
        db.execute("DELETE FROM generation_settings WHERE chat_id=? AND session_id=?", (chat_id, target_session_id))
        db.execute("DELETE FROM group_sessions WHERE chat_id=? AND session_id=?", (chat_id, target_session_id))
        db.execute("DELETE FROM failed_turns WHERE chat_id=? AND session_id=?", (chat_id, target_session_id))
        db.execute("DELETE FROM panel_sessions WHERE chat_id=? AND session_id=?", (chat_id, target_session_id))
        db.execute("DELETE FROM jobs WHERE chat_id=? AND session_id=?", (chat_id, target_session_id))
        db.execute("DELETE FROM hindsight_documents WHERE chat_id=? AND session_id=?", (chat_id, target_session_id))
        db.execute("DELETE FROM meta WHERE key IN (?, ?, ?, ?)", (swipe_state_key(chat_id, target_session_id), f"swipe_message:{chat_id}:{target_session_id}", expression_mode_key(chat_id, target_session_id), expression_last_key(chat_id, target_session_id)))
        db.execute("DELETE FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, target_session_id))
        if operation_id is not None:
            record_operation(db, operation_id, "session_delete")
        db.commit()
        optimize_database(db)
    return True, "deleted"


def allowed_users() -> set[str]:
    value = os.environ.get("SILLYTAVERN_TELEGRAM_ALLOWED_USERS", DEFAULT_ALLOWED_USER)
    return {x.strip() for x in value.split(",") if x.strip()}


def telegram_request(token: str, method: str, payload: dict | None = None) -> dict:
    request_payload = dict(payload or {})
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
    for attempt in range(3 if method == "sendMessage" else 1):
        try:
            with urllib.request.urlopen(req, timeout=65) as response:  # nosec B310 - fixed HTTPS Telegram endpoint
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("description") or exc.reason
            except (OSError, ValueError, json.JSONDecodeError):
                detail = exc.reason
            if method == "sendMessage" and exc.code == 404 and attempt < 2:
                logging.warning("Telegram sendMessage returned 404; retrying (attempt %s/3)", attempt + 2)
                time.sleep(0.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Telegram {method} failed: {detail}") from exc
        if not result.get("ok"):
            detail = result.get("description") or "unknown Telegram error"
            if method == "sendMessage" and detail == "Not Found" and attempt < 2:
                logging.warning("Telegram sendMessage returned Not Found; retrying (attempt %s/3)", attempt + 2)
                time.sleep(0.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Telegram {method} failed: {detail}")
        break
    return result["result"]


def delete_pending_input_prompts(token: str, chat_id: str, state: dict) -> None:
    """Remove prompt messages created for a pending text-input transition."""
    message_ids = state.get("prompt_message_ids") or []
    if isinstance(message_ids, (int, str)):
        message_ids = [message_ids]
    for message_id in message_ids:
        try:
            telegram_request(
                token,
                "deleteMessage",
                {"chat_id": chat_id, "message_id": int(message_id)},
            )
        except Exception:
            logging.info(
                "Pending input prompt already unavailable",
                exc_info=True,
            )


def send_panel_request(token: str, method: str, payload: dict, *, request_context: RequestContext) -> dict:
    scoped_chat_id = str(payload.get("chat_id", "")) if payload.get("chat_id") is not None else ""
    result = telegram_request(token, method, payload)
    if payload.get("reply_markup") and method in {"sendMessage", "editMessageText"}:
        bound_message_id = result.get("message_id") if isinstance(result, dict) else None
        bound_message_id = bound_message_id or payload.get("message_id")
        if request_context.session_id and bound_message_id:
            bind_panel_session(
                request_context.db,
                scoped_chat_id,
                bound_message_id,
                request_context.session_id,
                request_context.actor_id,
            )
    return result


def download_telegram_file(token: str, file_id: str, max_bytes: int = SYNC_MAX_BYTES) -> bytes:
    file_info = telegram_request(token, "getFile", {"file_id": file_id})
    file_path = file_info.get("file_path")
    if not file_path:
        raise ValueError("Telegram did not return a file path")
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    with urllib.request.urlopen(urllib.request.Request(url), timeout=120) as response:  # nosec B310 - Telegram file URL is built from the fixed HTTPS API host
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"Telegram file exceeds {max_bytes // (1024 * 1024)} MB")
    return raw


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
        send_text(token, chat_id, "Character card is too large. The limit is 10 MB.")
        return
    try:
        fields = card_fields(parse_png_chara_bytes(raw))
    except Exception:
        send_text(token, chat_id, "This PNG is not a valid SillyTavern character card; chara metadata was not found.")
        return
    stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in fields["name"]).strip("_") or Path(filename).stem or "character"
    if len(stem.encode("utf-8")) > 80:
        stem = stem.encode("utf-8")[:64].decode("utf-8", "ignore").rstrip("_-") + "-" + hashlib.sha256(raw).hexdigest()[:8]
    target = CHARACTER_DIR / f"{stem}.png"
    previous_target = target
    is_new_version = target.exists() and target.read_bytes() != raw
    if is_new_version:
        target = CHARACTER_DIR / f"{stem}-{hashlib.sha256(raw).hexdigest()[:8]}.png"
    installed_count = sum(1 for path in CHARACTER_DIR.glob("*.png") if path.is_file()) if CHARACTER_DIR.exists() else 0
    if not target.exists() and installed_count >= CATALOG_MAX_ITEMS:
        send_text(token, chat_id, f"Character catalog is full ({CATALOG_MAX_ITEMS} maximum). Delete one before uploading another.")
        return
    CHARACTER_DIR.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() == raw:
            try:
                backup = verify_character_card_backup(target, raw)
                prune_character_backups(target.name)
            except OSError:
                send_text(token, chat_id, "Character card exists, but backup verification failed; no changes made.")
                return
            send_text(token, chat_id, f"Duplicate character card: {fields['name']} is already installed as {target.name}. Backup verified: {backup.name}.")
            return
    try:
        backup = verify_character_card_backup(target, raw)
        target.write_bytes(raw)
        prune_character_backups(target.name)
    except OSError:
        send_text(token, chat_id, "Character card backup verification failed; card was not installed.")
        return
    if is_new_version:
        send_text(token, chat_id, f"New character-card version installed: {fields['name']} ({target.name}). Previous version retained as {previous_target.name}. Backup verified: {backup.name}.")
    else:
        send_text(token, chat_id, f"Character card imported: {fields['name']} ({target.name}). Backup verified: {backup.name}.")


def _consume_world_upload(db: sqlite3.Connection, chat_id: str) -> bool:
    raw_state = get_meta(db, f"world_upload:{chat_id}")
    if not raw_state:
        return False
    set_meta(db, f"world_upload:{chat_id}", "")
    try:
        state = json.loads(raw_state)
    except json.JSONDecodeError:
        return False
    if float(state.get("expires_at", 0)) < time.time():
        return False
    return True


def import_world_info_document(db: sqlite3.Connection, token: str, chat_id: str, filename: str, raw: bytes) -> None:
    try:
        target = install_world_info_document(filename, raw)
    except FileExistsError:
        send_text(token, chat_id, f"World Info already exists: {Path(filename).name}. Delete it first, then upload again.")
    except (OSError, ValueError) as exc:
        send_text(token, chat_id, f"World Info upload refused: {exc}")
    else:
        send_text(token, chat_id, f"World Info imported: {target.name}. Open /world to enable it.")


def import_telegram_document(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    document: dict,
    default_model: str,
    telegram_message_id: int | None = None,
    *,
    api_key: str,
    process_image,
    memory_service: MemoryService,
    persona_service: PersonaService,
    group_director_service: GroupDirectorService,
) -> None:
    filename = str(document.get("file_name") or "document")
    suffix = Path(filename).suffix.casefold()
    file_size = int(document.get("file_size") or 0)
    if _consume_world_upload(db, chat_id):
        if suffix != ".json":
            send_text(token, chat_id, "World Info upload expects a .json document. Use /world and try again.")
            return
        if file_size > RAG_MAX_FILE_BYTES:
            send_text(token, chat_id, "World Info file is too large. The limit is 10 MB.")
            return
        raw = download_telegram_file(token, str(document.get("file_id") or ""), RAG_MAX_FILE_BYTES)
        import_world_info_document(db, token, chat_id, filename, raw)
        return
    if suffix == ".png":
        raw = download_telegram_file(token, str(document.get("file_id") or ""), RAG_MAX_FILE_BYTES)
        try:
            parse_png_chara_bytes(raw)
        except Exception:
            session = ensure_session(db, chat_id, default_model)
            fields = card_fields_from_file(session["character_file"])
            process_image(
                db,
                token,
                api_key,
                session,
                fields,
                chat_id,
                str(document.get("caption") or ""),
                raw,
                mime_type="image/png",
                telegram_message_id=telegram_message_id,
                memory_service=memory_service,
                persona_service=persona_service,
                group_director_service=group_director_service,
            )
        else:
            import_character_card(db, token, chat_id, filename, raw)
        return
    if suffix not in RAG_SUPPORTED_SUFFIXES:
        send_text(token, chat_id, "Unsupported Data Bank format. Use PDF, TXT, MD, JSON, YAML, CSV, HTML, XML, or DOCX.")
        return
    if file_size > RAG_MAX_FILE_BYTES:
        send_text(token, chat_id, "Data Bank file is too large. The limit is 10 MB.")
        return
    raw = download_telegram_file(token, str(document.get("file_id")), RAG_MAX_FILE_BYTES)
    status, chunks = add_data_bank_document(db, chat_id, filename, raw)
    if status == "duplicate":
        send_text(token, chat_id, f"Data Bank already contains {filename} ({chunks} chunks).")
    elif status == "versioned":
        versions = data_bank_document_versions(db, chat_id, filename)
        active_version = next((int(row[1]) for row in versions if row[2]), len(versions))
        send_text(token, chat_id, f"Added {filename} v{active_version} ({chunks} chunks). Previous versions are retained but excluded from RAG.")
    else:
        send_text(token, chat_id, f"Added {filename} to Data Bank ({chunks} chunks). RAG is {rag_mode(db, chat_id)}.")


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _semantic_boundary(text: str, start: int, end: int) -> int:
    """Choose the latest safe paragraph, sentence, or whitespace boundary."""
    segment = text[start:end]
    patterns = (r"\n\s*\n", r"\n", r"(?<=[.!?。！？])\s+", r"\s+")
    for pattern in patterns:
        candidates = []
        for match in re.finditer(pattern, segment):
            position = start + match.end()
            if position <= end and segment[:match.end()].count("```") % 2 == 0:
                candidates.append(position)
        if candidates:
            return max(candidates)
    return end


def split_telegram_text(text: str, limit: int = MAX_TELEGRAM_LENGTH) -> list[str]:
    """Split text semantically while respecting Telegram's UTF-16 limit."""
    text = str(text)
    if limit <= 0:
        raise ValueError("Telegram message limit must be positive")
    if _utf16_length(text) <= limit:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        units = 0
        hard_end = start
        for index in range(start, len(text)):
            width = _utf16_length(text[index])
            if units and units + width > limit:
                break
            units += width
            hard_end = index + 1
        if hard_end == start:
            hard_end = start + 1
        boundary = len(text) if hard_end == len(text) else _semantic_boundary(text, start, hard_end)
        if boundary <= start:
            boundary = hard_end
        chunks.append(text[start:boundary])
        start = boundary
    return chunks


def send_text(token: str, chat_id: str, text: str) -> list[int]:
    message_ids = []
    for chunk in split_telegram_text(text):
        result = telegram_request(token, "sendMessage", {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        })
        if result.get("message_id") is not None:
            message_ids.append(int(result["message_id"]))
    return message_ids


# Explicit late imports replace transitional dependency injection.
from bridge.cards import (
    default_persona_id,
    get_persona,
    send_panel_message,
)
from bridge.catalog import install_world_info_document
from bridge.common import (
    DEFAULT_ALLOWED_USER,
    IMAGE_MAX_BYTES,
    parse_topic_scope,
)
from bridge.expressions import (
    expression_last_key,
    expression_mode_key,
)
from bridge.generation import swipe_state_key
from bridge.group_director_service import GroupDirectorService
from bridge.memory_service import MemoryService
from bridge.persona_service import PersonaService
from bridge.persona_sync import NATIVE_PERSONA_SETTINGS_FILE
from bridge.session_titles import normalize_session_title

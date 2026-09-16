"""Phase 2 file-based synchronization with SillyTavern chat files."""

PHASE2_SYNC_INTERVAL_SECONDS = max(10, int(os.environ.get("SILLYTAVERN_SYNC_INTERVAL_SECONDS", "30")))
PHASE2_SYNC_MAX_FILE_BYTES = min(max(1024, int(os.environ.get("SILLYTAVERN_SYNC_MAX_FILE_BYTES", str(IMPORT_MAX_BYTES)))), IMPORT_MAX_BYTES)
PHASE2_SYNC_CHAT_DIR = Path(os.environ.get("SILLYTAVERN_SYNC_CHAT_DIR", str(SILLYTAVERN_DIR / "data/default-user/chats")))
PHASE2_SYNC_GROUP_DIR = Path(os.environ.get("SILLYTAVERN_SYNC_GROUP_DIR", str(SILLYTAVERN_DIR / "data/default-user/groups")))
PHASE2_SYNC_BACKUP_DIR = BRIDGE_HOME / "backups" / "sillytavern" / "sync"


def _phase2_binding(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, object]:
    """Return the complete Phase 2 binding row for one bridge session."""
    ensure_sync_binding(db, chat_id, session_id)
    row = db.execute(
        "SELECT sync_id,last_hash,last_direction,last_synced_at,external_path,external_hash,external_mtime_ns,auto_enabled,conflict,last_error,last_checked_at,realtime_enabled,realtime_failures,realtime_next_retry_at FROM sync_bindings WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ).fetchone()
    if row is None:
        raise ValueError("sync binding could not be created")
    keys = ("sync_id", "last_hash", "last_direction", "last_synced_at", "external_path", "external_hash", "external_mtime_ns", "auto_enabled", "conflict", "last_error", "last_checked_at", "realtime_enabled", "realtime_failures", "realtime_next_retry_at")
    return dict(zip(keys, row))


def _component(value: str, fallback: str) -> str:
    """Make a safe, bounded filename component."""
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value)).strip("_-")
    return (cleaned or fallback)[:80]


def _sync_roots() -> tuple[Path, Path]:
    """Return resolved SillyTavern chat roots used by Phase 2."""
    return PHASE2_SYNC_CHAT_DIR.resolve(), PHASE2_SYNC_GROUP_DIR.resolve()


def _valid_external_path(path: Path) -> bool:
    """Allow only paths below the configured SillyTavern chat roots."""
    resolved = path.resolve()
    return any(resolved.is_relative_to(root) for root in _sync_roots())


def _phase2_path(db: sqlite3.Connection, chat_id: str, session: dict[str, str], binding: dict[str, object]) -> Path:
    """Resolve or create the stable SillyTavern JSONL path for a binding."""
    stored = Path(str(binding.get("external_path") or "")) if binding.get("external_path") else None
    if stored and _valid_external_path(stored):
        return stored.resolve()
    fields = {}
    try:
        fields = card_fields_from_file(session["character_file"])
    except Exception:
        fields = {"name": Path(session["character_file"]).stem}
    group = group_state(db, chat_id, session["session_id"])
    root = PHASE2_SYNC_GROUP_DIR if group.get("enabled") else PHASE2_SYNC_CHAT_DIR / _component(fields.get("name", "character"), "character")
    path = (root / f"bridge-{binding['sync_id']}.jsonl").resolve()
    if not _valid_external_path(path):
        raise ValueError("configured SillyTavern sync path is outside the allowed chat directory")
    db.execute("UPDATE sync_bindings SET external_path=? WHERE chat_id=? AND session_id=?", (str(path), chat_id, session["session_id"]))
    db.commit()
    return path


def _local_rows(db: sqlite3.Connection, chat_id: str, session_id: str) -> list[tuple[int, str, str, float]]:
    """Load the complete ordered bridge transcript."""
    return db.execute(
        "SELECT rowid,role,content,created_at FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid",
        (chat_id, session_id),
    ).fetchall()


def _swipe_records(db: sqlite3.Connection, chat_id: str, session_id: str, user_rowid: int) -> list[str]:
    """Return stored response variants in SillyTavern swipe order."""
    rows = db.execute(
        "SELECT response FROM response_variants WHERE chat_id=? AND session_id=? AND user_rowid=? ORDER BY variant_index",
        (chat_id, session_id, user_rowid),
    ).fetchall()
    return [str(row[0]) for row in rows if str(row[0] or "")]


def _build_phase2_jsonl(db: sqlite3.Connection, chat_id: str, session: dict[str, str], fields: dict[str, str], sync_id: str, rows: list[tuple[int, str, str, float]]) -> bytes:
    """Build a SillyTavern JSONL document, including compatible swipes."""
    user_name = persona_name(session["persona_id"]) if session["persona_id"] else "Punto"
    transcript_hash = sync_transcript_hash([(role, content) for _rowid, role, content, _created_at in rows])
    header = {"chat_metadata": {
        "name": session["title"], "session_id": session["session_id"], "character": fields["name"],
        "character_file": session["character_file"], "model": session["model_id"],
        "response_language": session.get("response_language") or "auto", "persona": session["persona_id"],
        "world_info": active_world_files(session["world_file"]), "author_note": session["author_note"],
        "generation_settings": get_generation_settings(db, chat_id, session["session_id"]),
        "session_summary": get_session_summary(db, chat_id, session["session_id"])[0],
        "bridge_sync": {"version": 2, "sync_id": sync_id, "transcript_hash": transcript_hash},
    }, "user_name": user_name, "character_name": fields["name"]}
    lines = [json.dumps(header, ensure_ascii=False)]
    previous_user_rowid = None
    for rowid, role, content, created_at in rows:
        record = {"name": user_name if role == "user" else fields["name"], "is_user": role == "user", "is_system": False, "send_date": time.strftime("%Y-%m-%d @ %H:%M:%S", time.localtime(created_at)), "mes": content, "extra": {}}
        if role == "user":
            previous_user_rowid = rowid
        elif previous_user_rowid is not None:
            variants = _swipe_records(db, chat_id, session["session_id"], previous_user_rowid)
            if len(variants) > 1:
                if content in variants:
                    selected = variants.index(content)
                else:
                    variants.append(content)
                    selected = len(variants) - 1
                record["swipes"], record["swipe_id"] = variants, selected
        lines.append(json.dumps(record, ensure_ascii=False))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_phase2_file(path: Path, payload: bytes, sync_id: str) -> None:
    """Back up an external chat and replace it atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    PHASE2_SYNC_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_bytes()
        backup = PHASE2_SYNC_BACKUP_DIR / f"{sync_id}.{time.time_ns()}.jsonl"
        backup.write_bytes(existing)
        os.chmod(backup, 0o600)
        if hashlib.sha256(backup.read_bytes()).digest() != hashlib.sha256(existing).digest():
            backup.unlink(missing_ok=True)
            raise OSError("SillyTavern chat backup checksum verification failed")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def _read_phase2_file(path: Path) -> tuple[dict, list[tuple[str, str]], dict[int, tuple[list[str], int]], int]:
    """Read a bounded JSONL file and collect assistant swipe records."""
    raw = path.read_bytes()
    if len(raw) > PHASE2_SYNC_MAX_FILE_BYTES:
        raise ValueError("SillyTavern chat file exceeds the configured sync limit")
    metadata, messages = parse_sillytavern_jsonl(raw)
    records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    variants = {}
    message_index = 0
    for record in records[1:] if metadata else records:
        if not isinstance(record, dict) or record.get("is_system") or "mes" not in record:
            continue
        content = str(record.get("mes") or "").strip()
        if not content:
            continue
        if not record.get("is_user") and isinstance(record.get("swipes"), list):
            swipes = [str(item).strip() for item in record["swipes"] if str(item or "").strip()]
            if len(swipes) > 1:
                try:
                    selected = int(record.get("swipe_id", 0))
                except (TypeError, ValueError):
                    selected = 0
                variants[message_index] = (swipes[:8], max(0, min(selected, len(swipes) - 1)))
        message_index += 1
    return metadata, messages, variants, int(path.stat().st_mtime_ns)


def _apply_external_session(db: sqlite3.Connection, chat_id: str, session: dict[str, str], metadata: dict, messages: list[tuple[str, str]], variants: dict[int, tuple[list[str], int]]) -> str:
    """Replace one bound bridge transcript from an external SillyTavern file."""
    values = {"title": str(metadata.get("name") or session["title"])[:120]}
    character_file = str(metadata.get("character_file") or "")
    if character_file and safe_character_path(character_file):
        values["character_file"] = Path(character_file).name
    if metadata.get("model"):
        values["model_id"] = str(metadata["model"])[:200]
    persona_id = str(metadata.get("persona") or "")
    if persona_id and get_persona(persona_id):
        values["persona_id"] = persona_id
    raw_worlds = metadata.get("world_info")
    candidates = raw_worlds if isinstance(raw_worlds, list) else ([raw_worlds] if raw_worlds else [])
    valid_worlds = [Path(str(name)).name for name in candidates if safe_world_path(str(name))]
    if valid_worlds:
        values["world_file"] = encode_world_files(valid_worlds)
    values["author_note"] = str(metadata.get("author_note") or "")[:2000]
    values["system_prompt"] = str(metadata.get("system_prompt") or "")[:8000]
    try:
        values["response_language"] = normalize_response_language(str(metadata.get("response_language") or "auto"))
    except ValueError:
        pass
    update_session(db, chat_id, session["session_id"], **values)
    imported_settings = metadata.get("generation_settings")
    if isinstance(imported_settings, dict):
        normalized = {}
        for key, raw_value in imported_settings.items():
            if key in GENERATION_DEFAULTS:
                try:
                    normalized[key] = parse_generation_setting(key, str(raw_value))[1]
                except ValueError:
                    pass
        if normalized:
            update_generation_settings(db, chat_id, session["session_id"], **normalized)
    db.execute("DELETE FROM response_variants WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"]))
    db.execute("DELETE FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"]))
    user_rowids = {}
    for index, (role, content) in enumerate(messages):
        cursor = db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)", (chat_id, session["session_id"], role, content, time.time() + index * 0.001))
        if role == "user":
            user_rowids[index] = (int(cursor.lastrowid), content)
    for index, (swipes, selected) in variants.items():
        user_index = next((item for item in range(index - 1, -1, -1) if item in user_rowids), None)
        if user_index is None:
            continue
        user_rowid, user_content = user_rowids[user_index]
        ordered = [value for pos, value in enumerate(swipes) if pos != selected] + [swipes[selected]]
        for response in ordered:
            save_response_variant(db, chat_id, session["session_id"], user_content, response, user_rowid=user_rowid, commit=False)
    db.commit()
    return sync_transcript_hash(messages)


def _set_phase2_state(db: sqlite3.Connection, chat_id: str, session_id: str, path: Path, local_hash: str, external_hash: str, mtime_ns: int, direction: str, conflict: str = "", error: str = "") -> None:
    """Persist a Phase 2 checkpoint or conflict state."""
    db.execute("UPDATE sync_bindings SET external_path=?,last_hash=?,external_hash=?,external_mtime_ns=?,last_direction=?,last_synced_at=?,auto_enabled=CASE WHEN ? IN ('conflict','sync_id_mismatch','initial_divergence','external_file_error') THEN 0 ELSE auto_enabled END,conflict=?,last_error=?,last_checked_at=? WHERE chat_id=? AND session_id=?", (str(path), local_hash, external_hash, mtime_ns, direction, time.time(), conflict, conflict, error[:1000], time.time(), chat_id, session_id))
    db.commit()


def phase2_sync_now(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    """Synchronize one binding, stopping instead of overwriting divergent changes."""
    session = load_session(db, chat_id, session_id, DEFAULT_MODEL)
    binding = _phase2_binding(db, chat_id, session_id)
    path = _phase2_path(db, chat_id, session, binding)
    rows = _local_rows(db, chat_id, session_id)
    local_hash = sync_transcript_hash([(role, content) for _rowid, role, content, _created_at in rows])
    if not path.exists():
        fields = card_fields_from_file(session["character_file"])
        _write_phase2_file(path, _build_phase2_jsonl(db, chat_id, session, fields, str(binding["sync_id"]), rows), str(binding["sync_id"]))
        _set_phase2_state(db, chat_id, session_id, path, local_hash, local_hash, path.stat().st_mtime_ns, "bridge_to_sillytavern")
        return "created external chat"
    try:
        metadata, external_messages, external_variants, mtime_ns = _read_phase2_file(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        _set_phase2_state(db, chat_id, session_id, path, local_hash, str(binding.get("external_hash") or ""), int(binding.get("external_mtime_ns") or 0), "", "external_file_error", str(exc))
        return "external chat unreadable; sync stopped"
    remote_sync = metadata.get("bridge_sync") if isinstance(metadata.get("bridge_sync"), dict) else {}
    if remote_sync.get("sync_id") and remote_sync.get("sync_id") != binding["sync_id"]:
        _set_phase2_state(db, chat_id, session_id, path, local_hash, "", mtime_ns, "", "sync_id_mismatch", "external sync ID does not match this session")
        return "sync ID mismatch; sync stopped"
    external_hash = sync_transcript_hash(external_messages)
    baseline = str(binding.get("last_hash") or "")
    if external_hash == local_hash:
        _set_phase2_state(db, chat_id, session_id, path, local_hash, external_hash, mtime_ns, str(binding.get("last_direction") or ""))
        return "unchanged"
    if not baseline:
        _set_phase2_state(db, chat_id, session_id, path, local_hash, external_hash, mtime_ns, "", "initial_divergence", "link has no common checkpoint")
        return "initial divergence; sync stopped"
    if local_hash == baseline and external_hash != baseline:
        imported_hash = _apply_external_session(db, chat_id, session, metadata, external_messages, external_variants)
        _set_phase2_state(db, chat_id, session_id, path, imported_hash, external_hash, mtime_ns, "sillytavern_to_bridge")
        return "imported SillyTavern changes"
    if external_hash == baseline and local_hash != baseline:
        fields = card_fields_from_file(session["character_file"])
        _write_phase2_file(path, _build_phase2_jsonl(db, chat_id, session, fields, str(binding["sync_id"]), rows), str(binding["sync_id"]))
        _set_phase2_state(db, chat_id, session_id, path, local_hash, local_hash, path.stat().st_mtime_ns, "bridge_to_sillytavern")
        return "exported bridge changes"
    _set_phase2_state(db, chat_id, session_id, path, local_hash, external_hash, mtime_ns, "", "conflict", "both sides changed since the last checkpoint")
    return "conflict detected; sync stopped"


def phase2_toggle_auto(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    """Toggle automatic polling for one session and initialize its file safely."""
    session = load_session(db, chat_id, session_id, DEFAULT_MODEL)
    binding = _phase2_binding(db, chat_id, session_id)
    enabled = not bool(binding.get("auto_enabled"))
    db.execute("UPDATE sync_bindings SET auto_enabled=?,conflict='',last_error='' WHERE chat_id=? AND session_id=?", (1 if enabled else 0, chat_id, session_id))
    db.commit()
    if not enabled:
        return "auto sync disabled"
    return "auto sync enabled; " + phase2_sync_now(db, chat_id, session_id)


def phase2_sync_status_line(db: sqlite3.Connection, chat_id: str, session: dict[str, str]) -> str:
    """Render compact Phase 2 status without exposing full private paths."""
    binding = _phase2_binding(db, chat_id, session["session_id"])
    auto = "on" if binding.get("auto_enabled") else "off"
    path = Path(str(binding.get("external_path") or ""))
    target = path.name if path.name else "not linked"
    problem = str(binding.get("conflict") or binding.get("last_error") or "clear")
    return f"File auto sync: {auto}\nFile: {target}\nState: {problem}"


def phase2_sync_poll(db: sqlite3.Connection) -> None:
    """Poll due automatic bindings and leave conflicts stopped for review."""
    now = time.time()
    rows = db.execute("SELECT chat_id,session_id,last_checked_at FROM sync_bindings WHERE auto_enabled=1 AND (last_checked_at=0 OR last_checked_at<?) LIMIT 32", (now - PHASE2_SYNC_INTERVAL_SECONDS,)).fetchall()
    for chat_id, session_id, _last_checked in rows:
        try:
            phase2_sync_now(db, str(chat_id), str(session_id))
        except Exception as exc:
            logging.warning("Phase 2 sync failed for session %s: %s", session_id, exc, exc_info=True)
            db.execute("UPDATE sync_bindings SET last_checked_at=?,last_error=? WHERE chat_id=? AND session_id=?", (time.time(), str(exc)[:1000], chat_id, session_id))
            db.commit()

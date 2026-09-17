"""Late-loaded state-integrity hardening for native storage and Live Sync.

Keep whole-document native Persona writes serialized, preserve native avatar media
extensions, propagate explicit Live Sync metadata clears, and prevent queued
Hindsight retention from restoring a stale session snapshot after newer local
state or a session-memory purge.
"""

_ORIGINAL_UPSERT_NATIVE_PERSONA = upsert_native_persona
_ORIGINAL_APPLY_SYNC_SNAPSHOT = apply_sync_snapshot
_ORIGINAL_RETAIN_SESSION_MEMORY_WORKER = _retain_session_memory
_ORIGINAL_PURGE_HINDSIGHT_SESSION = purge_hindsight_session


def _native_persona_id_is_taken(identifier: str) -> bool:
    """Treat bridge-created native avatar stems as stable logical Persona IDs."""
    value = str(identifier or "")
    if _valid_native_avatar(value):
        return False
    expected_stem = f"bridge-{value}"
    return any(Path(avatar).stem == expected_stem for avatar in load_native_personas(force=True))


def _choose_native_avatar(persona_id: str, persona: dict, settings: dict, native_names: dict) -> str:
    """Allocate a native avatar without lying about the cloned image format."""
    mapped = _valid_native_avatar(persona.get("sillytavern_avatar"))
    if mapped:
        return mapped
    matches = [
        avatar for avatar, name in native_names.items()
        if str(name).casefold() == str(persona["name"]).casefold() and _valid_native_avatar(avatar)
    ]
    if len(matches) == 1:
        return matches[0]
    source_name = _valid_native_avatar(settings.get("user_avatar"))
    suffix = Path(source_name).suffix.casefold() if source_name else ".png"
    if suffix not in _NATIVE_AVATAR_SUFFIXES:
        suffix = ".png"
    base = f"bridge-{persona_id}"
    candidate = f"{base}{suffix}"
    for attempt in range(257):
        if candidate not in native_names and not (NATIVE_PERSONA_AVATAR_DIR / candidate).exists():
            return candidate
        token = hashlib.sha256(f"{persona_id}:{attempt}".encode("utf-8")).hexdigest()[:8]
        candidate = f"{base[:54]}-{token}{suffix}"
    raise ValueError("Could not allocate a unique native persona avatar")


def upsert_native_persona(identifier: str, name: str, description: str, client=None) -> str:
    """Serialize native settings read-modify-write and enforce logical ID uniqueness."""
    with PERSONA_EDIT_LOCK:
        if _native_persona_id_is_taken(identifier):
            raise ValueError("Persona ID already exists")
        return _ORIGINAL_UPSERT_NATIVE_PERSONA(identifier, name, description, client=client)


def _hindsight_epoch_key(chat_id: str, session_id: str) -> str:
    return f"hindsight_epoch:{chat_id}:{session_id}"


def _hindsight_memory_epoch(db: sqlite3.Connection, chat_id: str, session_id: str) -> int:
    try:
        return max(0, int(get_meta(db, _hindsight_epoch_key(chat_id, session_id), "0") or 0))
    except (TypeError, ValueError):
        return 0


def _hindsight_conversation_snapshot(db: sqlite3.Connection, chat_id: str, session_id: str):
    rows = db.execute(
        "SELECT role,content,created_at FROM messages WHERE chat_id=? AND session_id=? "
        "ORDER BY created_at DESC,rowid DESC LIMIT ?",
        (chat_id, session_id, HINDSIGHT_RETAIN_MAX_MESSAGES),
    ).fetchall()
    rows = list(reversed(rows))
    if not rows:
        return "", ""
    conversation = json.dumps([
        {
            "role": role,
            "content": content,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created_at)),
        }
        for role, content, created_at in rows
    ], ensure_ascii=False)
    fingerprint = hashlib.sha256(json.dumps(
        [[str(role), str(content)] for role, content, _created_at in rows],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return conversation, fingerprint


def _retain_session_memory(
    chat_id: str,
    session: dict[str, str],
    character_name: str,
    conversation: str,
    snapshot_hash: str = "",
    snapshot_epoch: int | None = None,
) -> None:
    """Reject a queued retain if its session epoch or transcript is already stale."""
    session_id = str(session["session_id"])
    with hindsight_session_lock(chat_id, session_id):
        session_db = db_connect()
        try:
            exists = session_db.execute(
                "SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?",
                (str(chat_id), session_id),
            ).fetchone()
            if not exists:
                return
            current_epoch = _hindsight_memory_epoch(session_db, chat_id, session_id)
            current_conversation, current_hash = _hindsight_conversation_snapshot(
                session_db, str(chat_id), session_id
            )
        finally:
            session_db.close()
        if snapshot_epoch is not None and current_epoch != int(snapshot_epoch):
            logging.info("Skipping stale Hindsight retain after session-memory purge for %s/%s", chat_id, session_id)
            return
        if snapshot_hash and current_hash != snapshot_hash:
            logging.info("Skipping stale Hindsight retain after transcript change for %s/%s", chat_id, session_id)
            return
        payload = current_conversation if snapshot_hash else conversation
        if not payload:
            return
        _ORIGINAL_RETAIN_SESSION_MEMORY_WORKER(
            chat_id, session, character_name, payload
        )


def retain_session_memory(db: sqlite3.Connection, chat_id: str, session: dict[str, str], fields: dict[str, str]) -> None:
    """Queue retention with a transcript fingerprint and purge epoch."""
    if memory_mode(db, chat_id) != "on":
        return
    conversation, snapshot_hash = _hindsight_conversation_snapshot(
        db, chat_id, session["session_id"]
    )
    if not conversation:
        return
    snapshot_epoch = _hindsight_memory_epoch(db, chat_id, session["session_id"])
    submit_background(
        "hindsight_retain",
        _retain_session_memory,
        chat_id,
        dict(session),
        fields["name"],
        conversation,
        snapshot_hash,
        snapshot_epoch,
    )


def purge_hindsight_session(db: sqlite3.Connection, chat_id: str, session_id: str) -> int:
    """Invalidate queued retains only after a successful remote session purge."""
    with hindsight_session_lock(chat_id, session_id):
        deleted = _ORIGINAL_PURGE_HINDSIGHT_SESSION(db, chat_id, session_id)
        db.execute(
            "DELETE FROM hindsight_documents WHERE chat_id=? AND session_id=?",
            (str(chat_id), str(session_id)),
        )
        next_epoch = _hindsight_memory_epoch(db, chat_id, session_id) + 1
        db.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            (_hindsight_epoch_key(chat_id, session_id), str(next_epoch)),
        )
        db.commit()
        return deleted


def apply_sync_snapshot(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    metadata: dict,
    messages: list[tuple[str, str]],
    variants: dict[int, tuple[list[str], int]],
) -> str:
    """Apply explicit metadata clears and refresh memory after a remote import."""
    imported_hash = _ORIGINAL_APPLY_SYNC_SNAPSHOT(
        db, chat_id, session, metadata, messages, variants
    )
    updates = {}
    if "persona" in metadata and not str(metadata.get("persona") or "").strip():
        updates["persona_id"] = ""
    if "world_info" in metadata:
        raw_worlds = metadata.get("world_info")
        candidates = raw_worlds if isinstance(raw_worlds, list) else ([raw_worlds] if raw_worlds else [])
        if not candidates:
            updates["world_file"] = ""
    if updates:
        update_session(db, chat_id, session["session_id"], **updates)
    try:
        refreshed = load_session(db, chat_id, session["session_id"], DEFAULT_MODEL)
        retain_session_memory(
            db,
            chat_id,
            refreshed,
            card_fields_from_file(refreshed["character_file"]),
        )
    except Exception:
        logging.warning("Could not refresh Hindsight after Live Sync import", exc_info=True)
    return imported_hash

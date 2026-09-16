"""Late-loaded idempotent JSONL import implementation."""


def _set_import_phase_uncommitted(db, phase_key: str, phase: str) -> None:
    if phase_key:
        db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (phase_key, phase))


def import_chat_session(db, chat_id, raw, default_model, operation_id=None):
    metadata, imported_messages = parse_sillytavern_jsonl(raw)
    sync_metadata = metadata.get("bridge_sync") if isinstance(metadata.get("bridge_sync"), dict) else {}
    requested_sync_id = str(sync_metadata.get("sync_id") or "")
    deterministic_session_id = f"import-job-{operation_id}" if operation_id is not None else None
    phase_key = f"import_phase:{operation_id}" if operation_id is not None else ""
    phase = get_meta(db, phase_key, "created") if phase_key else "created"
    session = create_session(db, chat_id, default_model, session_id=deterministic_session_id)
    session_id = session["session_id"]
    if phase == "complete":
        return session

    if phase == "created":
        # Metadata/settings helpers are idempotent.  The transcript itself is
        # rebuilt from scratch and its phase marker is committed in the same
        # transaction, so a crash can never leave a committed partial/duplicate
        # transcript with phase='created'.
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
        update_session(db, chat_id, session_id, **values)

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
                update_generation_settings(db, chat_id, session_id, **normalized)

        db.execute("DELETE FROM response_variants WHERE chat_id=? AND session_id=?", (chat_id, session_id))
        db.execute("DELETE FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session_id))
        base_time = time.time()
        for index, (role, content) in enumerate(imported_messages):
            db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (chat_id, session_id, role, content, base_time + index * 0.001),
            )
        _set_import_phase_uncommitted(db, phase_key, "messages_loaded")
        db.commit()
        phase = "messages_loaded"

    if phase == "messages_loaded":
        imported_summary = str(metadata.get("session_summary") or "")[:SUMMARY_MAX_CHARS]
        if imported_summary:
            last_rowid = int(
                db.execute(
                    "SELECT COALESCE(MAX(rowid), 0) FROM messages WHERE chat_id=? AND session_id=?",
                    (chat_id, session_id),
                ).fetchone()[0]
            )
            db.execute(
                "INSERT OR REPLACE INTO session_summaries(chat_id,session_id,summary,covered_until_rowid,updated_at) VALUES(?,?,?,?,?)",
                (chat_id, session_id, imported_summary, last_rowid, time.time()),
            )
        else:
            db.execute("DELETE FROM session_summaries WHERE chat_id=? AND session_id=?", (chat_id, session_id))
        _set_import_phase_uncommitted(db, phase_key, "summary_loaded")
        db.commit()
        phase = "summary_loaded"

    if phase == "summary_loaded":
        # Rebuild variants deterministically from stored rowids.  Clearing first
        # makes a replay of this whole phase idempotent; commit=False keeps all
        # variants and the final phase marker in one transaction.
        db.execute("DELETE FROM response_variants WHERE chat_id=? AND session_id=?", (chat_id, session_id))
        rows = db.execute(
            "SELECT rowid,role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid",
            (chat_id, session_id),
        ).fetchall()
        last_user_rowid = None
        last_user_content = None
        for rowid, role, content in rows:
            if role == "user":
                last_user_rowid = int(rowid)
                last_user_content = str(content)
            elif role == "assistant" and last_user_rowid is not None:
                save_response_variant(
                    db,
                    chat_id,
                    session_id,
                    last_user_content,
                    str(content),
                    user_rowid=last_user_rowid,
                    commit=False,
                )
        _set_import_phase_uncommitted(db, phase_key, "complete")
        db.commit()

    ensure_sync_binding(db, chat_id, session_id, requested_sync_id)
    record_sync_binding(db, chat_id, session_id, sync_transcript_hash(imported_messages), "sillytavern_to_bridge")
    return ensure_session(db, chat_id, default_model)

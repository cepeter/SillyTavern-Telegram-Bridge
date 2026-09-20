"""Late-loaded Live Sync state-integrity hardening.

Propagate explicit Live Sync metadata clears and refresh memory after remote imports.
"""


_ORIGINAL_APPLY_SYNC_SNAPSHOT = apply_sync_snapshot


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

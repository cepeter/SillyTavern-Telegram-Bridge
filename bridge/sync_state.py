"""Stable transcript hashes and synchronization identity use cases."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from bridge.sqlite_store import write_transaction
from bridge.sync_repository import insert_sync_binding, load_sync_binding_row


def sync_transcript_hash(rows: list[tuple[str, str]]) -> str:
    """Create a stable hash for an ordered user/assistant transcript."""
    payload = json.dumps(
        [[str(role), str(content)] for role, content in rows], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ensure_sync_binding(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, object]:
    """Return/create stable external identity without committing a caller transaction."""
    row = load_sync_binding_row(db, chat_id, session_id)
    if row is None:
        with write_transaction(db):
            row = load_sync_binding_row(db, chat_id, session_id)
            if row is None:
                sync_id = "stb-" + hashlib.sha256(f"{chat_id}:{session_id}:{time.time_ns()}".encode()).hexdigest()[:32]
                try:
                    insert_sync_binding(db, chat_id, session_id, sync_id)
                except sqlite3.IntegrityError:
                    sync_id = (
                        "stb-" + hashlib.sha256(f"{chat_id}:{session_id}:{time.time_ns()}".encode()).hexdigest()[:32]
                    )
                    insert_sync_binding(db, chat_id, session_id, sync_id)
                row = load_sync_binding_row(db, chat_id, session_id)
    if row is None:
        raise RuntimeError("sync binding creation failed")
    return {
        "sync_id": str(row[0]),
        "last_hash": str(row[1] or ""),
        "last_direction": str(row[2] or ""),
        "last_synced_at": float(row[3] or 0),
    }

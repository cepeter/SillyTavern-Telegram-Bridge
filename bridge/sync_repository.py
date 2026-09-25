"""SQL-only stable synchronization identity storage."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def load_sync_binding_row(
    db: sqlite3.Connection, chat_id: str, session_id: str
) -> tuple[str, str | None, str | None, float | None] | None:
    return db.execute(
        "SELECT sync_id,last_hash,last_direction,last_synced_at FROM sync_bindings WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ).fetchone()


def insert_sync_binding(db: sqlite3.Connection, chat_id: str, session_id: str, sync_id: str) -> None:
    require_active_transaction(db)
    db.execute("INSERT INTO sync_bindings(chat_id,session_id,sync_id) VALUES(?,?,?)", (chat_id, session_id, sync_id))

"""SQL-only panel binding storage; time and transaction ownership belong to callers."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def store_panel_binding(
    db: sqlite3.Connection,
    chat_id: str,
    message_id: str,
    session_id: str,
    owner_user_id: str,
    expires_at: float,
) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT OR REPLACE INTO panel_sessions(chat_id,message_id,session_id,owner_user_id,expires_"
        "at) VALUES(?,?,?,?,?)",
        (chat_id, message_id, session_id, owner_user_id, expires_at),
    )


def load_panel_binding(db: sqlite3.Connection, chat_id: str, message_id: str, now: float) -> tuple[str, str] | None:
    row = db.execute(
        "SELECT session_id,owner_user_id FROM panel_sessions WHERE chat_id=? AND message_id=? AND expires_at>=?",
        (chat_id, message_id, now),
    ).fetchone()
    return (str(row[0]), str(row[1] or "")) if row else None

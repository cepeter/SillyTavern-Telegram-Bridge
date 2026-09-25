"""Canonical reference repository owner."""

from __future__ import annotations

import sqlite3


def count_persona_references(
    db: sqlite3.Connection,
    persona_id: str,
) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM sessions WHERE persona_id=?",
        (str(persona_id),),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def count_session_messages(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    ).fetchone()
    return int(row[0] or 0) if row else 0

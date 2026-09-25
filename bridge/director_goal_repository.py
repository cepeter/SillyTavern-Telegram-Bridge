"""Canonical director goal repository owner."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def load_director_goal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> str:
    row = db.execute(
        "SELECT goal FROM director_goals WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    ).fetchone()
    return str(row[0]) if row else ""


def store_director_goal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    goal: str,
    updated_at: float,
) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT OR REPLACE INTO director_goals(chat_id,session_id,goal,updated_at) VALUES(?,?,?,?)",
        (str(chat_id), str(session_id), str(goal), float(updated_at)),
    )


def delete_director_goal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> None:
    require_active_transaction(db)
    db.execute(
        "DELETE FROM director_goals WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    )

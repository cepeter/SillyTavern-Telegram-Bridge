"""Canonical scene repository owner."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def load_scene_state_row(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> tuple[str, int] | None:
    row = db.execute(
        "SELECT state_json,updated_through_rowid FROM scene_states WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    ).fetchone()
    return (str(row[0] or "{}"), int(row[1] or 0)) if row else None


def delete_scene_state(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> None:
    require_active_transaction(db)
    db.execute(
        "DELETE FROM scene_states WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    )


def upsert_scene_state_if_fresh(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    state_json: str,
    through_rowid: int,
    updated_at: float,
) -> bool:
    require_active_transaction(db)
    cursor = db.execute(
        """
        INSERT INTO scene_states(
            chat_id,session_id,state_json,updated_through_rowid,updated_at
        ) VALUES(?,?,?,?,?)
        ON CONFLICT(chat_id,session_id) DO UPDATE SET
            state_json=excluded.state_json,
            updated_through_rowid=excluded.updated_through_rowid,
            updated_at=excluded.updated_at
        WHERE excluded.updated_through_rowid >= scene_states.updated_through_rowid
        """,
        (
            str(chat_id),
            str(session_id),
            str(state_json),
            int(through_rowid),
            float(updated_at),
        ),
    )
    return cursor.rowcount > 0

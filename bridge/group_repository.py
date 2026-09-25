"""Canonical group repository owner."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def load_group_state_row(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> tuple[object, ...] | None:
    return db.execute(
        "SELECT title,enabled,turn_index,mode,forced_speaker,"
        "members_json,turn_user_id,turn_users_json "
        "FROM group_sessions WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    ).fetchone()


def store_group_state_row(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    title: str,
    enabled: bool,
    turn_index: int,
    mode: str,
    forced_speaker: str,
    members_json: str,
    turn_user_id: str,
    turn_users_json: str,
    updated_at: float,
) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT OR REPLACE INTO group_sessions("
        "chat_id,session_id,title,enabled,turn_index,mode,forced_speaker,"
        "members_json,turn_user_id,turn_users_json,updated_at"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            str(chat_id),
            str(session_id),
            str(title),
            int(bool(enabled)),
            int(turn_index),
            str(mode),
            str(forced_speaker),
            str(members_json),
            str(turn_user_id),
            str(turn_users_json),
            float(updated_at),
        ),
    )

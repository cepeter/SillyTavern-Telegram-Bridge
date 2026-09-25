"""SQL-only durable failed-turn persistence; callers own transactions and filtering."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def load_session_model(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    row = db.execute("SELECT model_id FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, session_id)).fetchone()
    return str(row[0] or "") if row else ""


def upsert_failed_turn(
    db: sqlite3.Connection,
    chat_id: str,
    message_id: str,
    text: str,
    model: str,
    session_id: str,
    error: str,
    now: float,
) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT INTO failed_turns(chat_id,telegram_message_id,text,model,session_id,attempts,last_e"
        "rror,created_at,updated_at) "
        "VALUES(?,?,?,?,?,1,?,?,?) ON CONFLICT(chat_id,telegram_message_id) DO UPDATE SET "
        "model=excluded.model,session_id=excluded.session_id,attempts=attempts+1,last_error=exclude"
        "d.last_error,updated_at=excluded.updated_at",
        (chat_id, message_id, text, model, session_id, error, now, now),
    )


def load_failed_turns(db: sqlite3.Connection, chat_id: str) -> list[tuple]:
    return db.execute(
        "SELECT telegram_message_id,text,model,attempts,last_error,session_id "
        "FROM failed_turns WHERE chat_id=? ORDER BY updated_at DESC",
        (chat_id,),
    ).fetchall()


def delete_failed_turn(db: sqlite3.Connection, chat_id: str, message_id: str) -> None:
    require_active_transaction(db)
    db.execute("DELETE FROM failed_turns WHERE chat_id=? AND telegram_message_id=?", (chat_id, message_id))

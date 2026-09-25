"""SQL-only session storage. Callers own validation, transactions and external cleanup."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence

from bridge.repository_contracts import require_active_transaction

SESSION_COLUMNS = (
    "chat_id",
    "session_id",
    "title",
    "character_file",
    "model_id",
    "persona_id",
    "world_file",
    "author_note",
    "system_prompt",
    "response_language",
)
_SESSION_COLUMN_SQL = ", ".join(SESSION_COLUMNS)
_MUTABLE_COLUMNS = frozenset(SESSION_COLUMNS) - {"chat_id", "session_id"}
_SESSION_OWNED_TABLES = (
    "messages",
    "response_variants",
    "session_summaries",
    "generation_settings",
    "group_sessions",
    "failed_turns",
    "panel_sessions",
    "jobs",
    "hindsight_documents",
    "sessions",
)


def load_session_row(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, str] | None:
    row = db.execute(
        f"SELECT {_SESSION_COLUMN_SQL} FROM sessions WHERE chat_id=? AND session_id=?",  # noqa: S608 -- fixed columns, bound values
        (chat_id, session_id),
    ).fetchone()
    return dict(zip(SESSION_COLUMNS, row, strict=False)) if row is not None else None


def list_session_rows(db: sqlite3.Connection, chat_id: str) -> list[dict[str, str]]:
    rows = db.execute(
        f"SELECT {_SESSION_COLUMN_SQL} FROM sessions WHERE chat_id=? ORDER BY updated_at DESC",  # noqa: S608 -- fixed columns, bound values
        (chat_id,),
    ).fetchall()
    return [dict(zip(SESSION_COLUMNS, row, strict=False)) for row in rows]


def insert_session_row(db: sqlite3.Connection, session: Mapping[str, str], now: float) -> None:
    require_active_transaction(db)
    db.execute(
        f"INSERT OR IGNORE INTO sessions({_SESSION_COLUMN_SQL},created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (*(session[column] for column in SESSION_COLUMNS), now, now),
    )


def update_session_row(
    db: sqlite3.Connection, chat_id: str, session_id: str, values: Mapping[str, object], now: float
) -> None:
    require_active_transaction(db)
    if not values or not set(values) <= _MUTABLE_COLUMNS:
        raise ValueError("session update contains unsupported fields")
    assignments = ", ".join(f"{key}=?" for key in values)
    db.execute(
        f"UPDATE sessions SET {assignments}, updated_at=? WHERE chat_id=? AND session_id=?",  # noqa: S608 -- whitelisted columns, bound values
        (*values.values(), now, chat_id, session_id),
    )


def session_has_active_jobs(db: sqlite3.Connection, chat_id: str, session_id: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM jobs WHERE chat_id=? AND session_id=? AND state IN ('queued','scheduled','running') LIMIT 1",
            (chat_id, session_id),
        ).fetchone()
        is not None
    )


def delete_session_rows(db: sqlite3.Connection, chat_id: str, session_id: str, meta_keys: Sequence[str]) -> None:
    require_active_transaction(db)
    for table in _SESSION_OWNED_TABLES:
        db.execute(f"DELETE FROM {table} WHERE chat_id=? AND session_id=?", (chat_id, session_id))  # noqa: S608 -- fixed internal table names, bound values
    db.executemany("DELETE FROM meta WHERE key=?", [(key,) for key in meta_keys])

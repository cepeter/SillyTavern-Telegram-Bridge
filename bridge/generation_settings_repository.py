"""SQL-only settings and presets; caller validates values and owns transactions."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from bridge.repository_contracts import require_active_transaction

SETTINGS_COLUMNS = (
    "temperature",
    "max_tokens",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "reasoning_budget",
    "stop_sequences",
)


def load_settings_row(db: sqlite3.Connection, chat_id: str, session_id: str) -> tuple[object, ...] | None:
    return db.execute(
        "SELECT temperature,max_tokens,top_p,frequency_penalty,presence_penalty,reasoning_budget,stop_sequences "
        "FROM generation_settings WHERE chat_id=? AND session_id=?",
        (chat_id, session_id),
    ).fetchone()


def ensure_settings_row(db: sqlite3.Connection, chat_id: str, session_id: str, defaults: Mapping[str, object]) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT OR IGNORE INTO generation_settings(chat_id,session_id,temperature,max_tokens,top_p,"
        "frequency_penalty,presence_penalty,reasoning_budget,stop_sequences) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (chat_id, session_id, *(defaults[key] for key in SETTINGS_COLUMNS)),
    )


def update_settings_row(db: sqlite3.Connection, chat_id: str, session_id: str, values: Mapping[str, object]) -> None:
    require_active_transaction(db)
    if not set(values) <= set(SETTINGS_COLUMNS):
        raise ValueError("unsupported generation settings column")
    if values:
        assignments = ", ".join(f"{key}=?" for key in values)
        db.execute(
            f"UPDATE generation_settings SET {assignments} WHERE chat_id=? AND session_id=?",  # noqa: S608 -- columns checked against fixed allowlist; bound values
            (*values.values(), chat_id, session_id),
        )


def list_preset_names(db: sqlite3.Connection, chat_id: str) -> list[str]:
    return [
        str(row[0])
        for row in db.execute(
            "SELECT preset_name FROM generation_presets WHERE chat_id=? ORDER BY preset_name",
            (chat_id,),
        ).fetchall()
    ]


def store_preset_json(db: sqlite3.Connection, chat_id: str, name: str, payload: str, now: float) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT OR REPLACE INTO generation_presets(chat_id,preset_name,settings_json,created_at) VALUES(?,?,?,?)",
        (chat_id, name, payload, now),
    )


def load_preset_json(db: sqlite3.Connection, chat_id: str, name: str) -> str | None:
    row = db.execute(
        "SELECT settings_json FROM generation_presets WHERE chat_id=? AND preset_name=?", (chat_id, name)
    ).fetchone()
    return str(row[0]) if row else None


def delete_preset_row(db: sqlite3.Connection, chat_id: str, name: str) -> bool:
    require_active_transaction(db)
    return db.execute("DELETE FROM generation_presets WHERE chat_id=? AND preset_name=?", (chat_id, name)).rowcount > 0

"""Retryable model-turn diagnostics and current session-model resolution."""

from __future__ import annotations

import sqlite3
import time

from bridge.failure_repository import delete_failed_turn, load_failed_turns, load_session_model, upsert_failed_turn
from bridge.sqlite_store import write_transaction


def _retryable_model_turn_text(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    parts = stripped.split(None, 1)
    first = parts[0].casefold()
    if stripped.casefold() == "start" or first.startswith("/"):
        return False
    if first.startswith("@") and len(parts) > 1:
        return not parts[1].lstrip().startswith("/")
    return True


def record_failed_turn(
    db: sqlite3.Connection,
    chat_id: str,
    telegram_message_id: int,
    text: str,
    model: str,
    error: str,
    session_id: str = "",
) -> None:
    if not _retryable_model_turn_text(text):
        return
    with write_transaction(db):
        resolved = str(model or "")
        if session_id:
            selected = load_session_model(db, chat_id, session_id)
            if selected.strip():
                resolved = selected
        upsert_failed_turn(
            db,
            chat_id,
            str(telegram_message_id),
            text[:12000],
            resolved[:200],
            session_id[:200],
            error[:1000],
            time.time(),
        )


def latest_failed_turn(db: sqlite3.Connection, chat_id: str) -> tuple | None:
    return next((row for row in load_failed_turns(db, chat_id) if _retryable_model_turn_text(row[1])), None)


def clear_failed_turn(db: sqlite3.Connection, chat_id: str, telegram_message_id: int | str) -> None:
    with write_transaction(db):
        delete_failed_turn(db, chat_id, str(telegram_message_id))

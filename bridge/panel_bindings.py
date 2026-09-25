"""Session and actor panel ownership with explicit binding lifetimes."""

from __future__ import annotations

import sqlite3
import time

from bridge.panel_repository import load_panel_binding, store_panel_binding
from bridge.sqlite_store import write_transaction


def bind_panel_session(
    db: sqlite3.Connection,
    chat_id: str,
    message_id: int | str,
    session_id: str,
    owner_user_id: str = "",
) -> None:
    with write_transaction(db):
        store_panel_binding(
            db, str(chat_id), str(message_id), str(session_id), str(owner_user_id or ""), time.time() + 900
        )


def panel_session_for_message(db: sqlite3.Connection, chat_id: str, message_id: int | str) -> str | None:
    row = load_panel_binding(db, str(chat_id), str(message_id), time.time())
    return row[0] if row else None


def panel_owner_for_message(db: sqlite3.Connection, chat_id: str, message_id: int | str) -> str:
    row = load_panel_binding(db, str(chat_id), str(message_id), time.time())
    return row[1] if row else ""

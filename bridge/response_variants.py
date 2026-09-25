"""Response variant lifecycle with caller-owned atomic save and selection."""

from __future__ import annotations

import sqlite3
import time

from bridge.sqlite_store import write_transaction
from bridge.variant_repository import find_variant_user, last_user_variant_rows, select_variant, store_variant


def save_response_variant(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    user_content: str,
    response: str,
    user_rowid: int | None = None,
) -> int:
    with write_transaction(db):
        if user_rowid is None:
            user_rowid = find_variant_user(db, chat_id, session_id, user_content)
        return store_variant(db, chat_id, session_id, user_rowid, user_content, response, time.time())


def swipe_state_key(chat_id: str, session_id: str) -> str:
    return f"swipe_index:{chat_id}:{session_id}"


def last_user_variants(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> tuple[tuple[int, str] | None, list[tuple[int, str, int]]]:
    return last_user_variant_rows(db, chat_id, session_id)


def keep_swipe_variant(db: sqlite3.Connection, chat_id: str, session_id: str, index: int) -> str | None:
    with write_transaction(db):
        user_row, variants = last_user_variants(db, chat_id, session_id)
        selected = next((row[1] for row in variants if int(row[0]) == index), None)
        if not user_row or selected is None:
            return None
        select_variant(db, chat_id, session_id, int(user_row[0]), index, selected, time.time())
    return selected

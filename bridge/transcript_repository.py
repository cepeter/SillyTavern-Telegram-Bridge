"""Canonical transcript repository owner."""

from __future__ import annotations

import sqlite3


def committed_assistant_for_message(
    db: sqlite3.Connection, chat_id: str, telegram_message_id: int | str
) -> tuple[int, str, str | None] | None:
    return db.execute(
        """SELECT assistant.rowid, assistant.content, assistant.telegram_message_ids
        FROM messages AS user_message
        JOIN messages AS assistant
          ON assistant.chat_id=user_message.chat_id
         AND assistant.session_id=user_message.session_id
         AND assistant.role='assistant'
         AND assistant.rowid > user_message.rowid
        WHERE user_message.chat_id=? AND user_message.role='user' AND user_message.telegram_message_id=?
        ORDER BY assistant.rowid LIMIT 1""",
        (chat_id, str(telegram_message_id)),
    ).fetchone()


def native_edit_target(db: sqlite3.Connection, chat_id: str, message_id: int) -> tuple[int, str, str] | None:
    """Read the original message's session without changing the active session."""
    return db.execute(
        "SELECT rowid,session_id,role FROM messages WHERE chat_id=? AND "
        "telegram_message_id=? ORDER BY rowid DESC LIMIT 1",
        (chat_id, str(message_id)),
    ).fetchone()

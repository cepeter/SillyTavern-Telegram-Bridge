"""Opaque chat-scoped callback handles stored only in the caller's SQLite DB.

Tokens are not authorization by themselves: panel ownership and session checks
remain at ingress. No process cache can resurrect a rolled-back or deleted row.
"""

from __future__ import annotations

import logging
import math
import secrets
import sqlite3
import time

_CALLBACK_TOKEN_TTL_SECONDS = 900
_TOKEN_INSERT_ATTEMPTS = 8


class CallbackTokenError(RuntimeError):
    """A durable callback handle could not be created."""


def _required_scope(kind: str, chat_id: str) -> None:
    if not isinstance(chat_id, str) or not chat_id.strip():
        raise ValueError("callback chat_id is required")
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("callback kind is required")


def dynamic_callback_token(kind: str, value: str, chat_id: str, *, db: sqlite3.Connection) -> str:
    """Issue an unpredictable handle; never commit an enclosing transaction.

    Without an existing transaction this owns and commits one short write. If a
    caller already owns a transaction, the token becomes durable only with that
    transaction's commit; a rollback invalidates it without any cache cleanup.
    """
    _required_scope(kind, chat_id)
    owns_transaction = not db.in_transaction
    now = time.time()
    try:
        db.execute("DELETE FROM callback_tokens WHERE expires_at <= ?", (now,))
        for _attempt in range(_TOKEN_INSERT_ATTEMPTS):
            token = "t" + secrets.token_urlsafe(16)
            cursor = db.execute(
                "INSERT INTO callback_tokens(token,kind,value,chat_id,expires_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(token) DO NOTHING",
                (token, kind, str(value), chat_id, now + _CALLBACK_TOKEN_TTL_SECONDS),
            )
            if cursor.rowcount == 1:
                if owns_transaction:
                    db.commit()
                return token
        raise CallbackTokenError("Could not allocate a unique callback token")
    except (sqlite3.Error, OSError, CallbackTokenError) as exc:
        if owns_transaction:
            try:
                db.rollback()
            except sqlite3.Error:
                pass
        logging.warning("Could not persist callback token; reopen the panel")
        raise CallbackTokenError("Could not persist callback token") from exc


def resolve_dynamic_callback_token(token: str, kind: str, chat_id: str, *, db: sqlite3.Connection) -> str | None:
    """Read a valid handle without owning, committing, or pruning a transaction."""
    _required_scope(kind, chat_id)
    if not isinstance(token, str) or not token or len(token) > 64:
        return None
    try:
        row = db.execute(
            "SELECT kind,value,chat_id,expires_at FROM callback_tokens WHERE token=?",
            (token,),
        ).fetchone()
    except sqlite3.Error:
        logging.warning("Could not read callback token; reopen the panel")
        return None
    if row is None or row[0] != kind or not row[2] or row[2] != chat_id:
        return None
    try:
        expires_at = float(row[3])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(expires_at) or expires_at <= time.time():
        return None
    return str(row[1])

"""Canonical ambient runtime context for bridge request handling."""

import sqlite3
import threading


_PANEL_SESSION_CONTEXT = threading.local()
_DB_CONNECTION_CONTEXT = threading.local()


def set_panel_session_context(session_id: str | None) -> None:
    _PANEL_SESSION_CONTEXT.session_id = (
        str(session_id) if session_id else ""
    )


def panel_session_context() -> str:
    return str(
        getattr(_PANEL_SESSION_CONTEXT, "session_id", "") or ""
    )


def set_panel_actor_context(user_id: str | None) -> None:
    _PANEL_SESSION_CONTEXT.user_id = (
        str(user_id) if user_id else ""
    )


def panel_actor_context() -> str:
    return str(
        getattr(_PANEL_SESSION_CONTEXT, "user_id", "") or ""
    )


def set_db_connection_context(
    db: sqlite3.Connection | None,
) -> None:
    _DB_CONNECTION_CONTEXT.connection = db


def db_connection_context() -> sqlite3.Connection | None:
    return getattr(_DB_CONNECTION_CONTEXT, "connection", None)

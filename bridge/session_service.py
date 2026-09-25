"""Explicit application port for session lifecycle; no transport or global settings."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from bridge.port_contracts import CreateSession, DeleteSession, EnsureSession, ListSessions, LoadSession, UpdateSession


@dataclass(frozen=True)
class SessionService:
    load_backend: LoadSession
    ensure_backend: EnsureSession
    create_backend: CreateSession
    update_backend: UpdateSession
    list_backend: ListSessions
    delete_backend: DeleteSession

    def load(self, db: sqlite3.Connection, chat_id: str, session_id: str, default_model: str) -> dict[str, str]:
        return self.load_backend(db, chat_id, session_id, default_model)

    def ensure(self, db: sqlite3.Connection, chat_id: str, default_model: str) -> dict[str, str]:
        return self.ensure_backend(db, chat_id, default_model)

    def create(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        default_model: str,
        session_id: str | None = None,
        title: str = "New session",
    ) -> dict[str, str]:
        return self.create_backend(db, chat_id, default_model, session_id=session_id, title=title)

    def update(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        operation_id: int | str | None = None,
        operation_kind: str = "session_update",
        **values: object,
    ) -> None:
        self.update_backend(db, chat_id, session_id, operation_id=operation_id, operation_kind=operation_kind, **values)

    def list(self, db: sqlite3.Connection, chat_id: str) -> list[dict[str, str]]:
        return self.list_backend(db, chat_id)

    def delete(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        target_session_id: str,
        active_session_id: str,
        operation_id: int | str | None = None,
    ) -> tuple[bool, str]:
        return self.delete_backend(db, chat_id, target_session_id, active_session_id, operation_id=operation_id)

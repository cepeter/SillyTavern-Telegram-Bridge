"""Live Sync application service."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import sqlite3
from typing import cast


@dataclass(frozen=True)
class SyncStatus:
    session_id: str
    message_count: int
    sync_id: str
    last_synced_at: float
    last_direction: str
    realtime_enabled: bool
    api_configured: bool


@dataclass(frozen=True)
class SyncService:
    load_binding: Callable[[sqlite3.Connection, str, str], dict[str, object]]
    count_messages: Callable[[sqlite3.Connection, str, str], int]
    sync_now_backend: Callable[[sqlite3.Connection, str, str], str]
    toggle_realtime_backend: Callable[[sqlite3.Connection, str, str], str]
    poll_backend: Callable[[sqlite3.Connection], None]
    disable_realtime: Callable[[sqlite3.Connection, str, str, str], None]
    api_configured: Callable[[], bool]
    expected_errors: tuple[type[Exception], ...]

    def status(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
    ) -> SyncStatus:
        chat_id = str(chat_id)
        session_id = str(session_id)
        binding = self.load_binding(db, chat_id, session_id)
        return SyncStatus(
            session_id=session_id,
            message_count=self.count_messages(db, chat_id, session_id),
            sync_id=str(binding.get("sync_id") or ""),
            last_synced_at=float(
                cast(
                    int | float | str,
                    binding.get("last_synced_at") or 0.0,
                )
            ),
            last_direction=str(binding.get("last_direction") or ""),
            realtime_enabled=bool(binding.get("realtime_enabled")),
            api_configured=bool(self.api_configured()),
        )

    def sync_now(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
    ) -> str:
        chat_id = str(chat_id)
        session_id = str(session_id)
        try:
            return str(self.sync_now_backend(db, chat_id, session_id))
        except self.expected_errors as exc:
            self.disable_realtime(db, chat_id, session_id, str(exc))
            return f"Live API unavailable: {exc}"

    def toggle_realtime(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
    ) -> str:
        return str(
            self.toggle_realtime_backend(
                db,
                str(chat_id),
                str(session_id),
            )
        )

    def poll(self, db: sqlite3.Connection) -> None:
        self.poll_backend(db)

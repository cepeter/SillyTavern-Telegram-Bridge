"""Application service for conversation-memory orchestration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True)
class MemoryPromptContext:
    """Memory material supplied to prompt assembly."""

    recall: str
    summary: str


@dataclass(frozen=True)
class MemoryService:
    """Coordinate prompt memory, retention, and session-memory purge."""

    recall_context: Callable[..., str]
    summary_for_prompt: Callable[..., str]
    summary_state: Callable[..., tuple[str, int]]
    retain_session: Callable[..., None]
    purge_session_memory: Callable[..., int]

    def prompt_context(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session: dict[str, str],
        fields: dict[str, str],
        query: str,
        *,
        edited_user_rowid: int | None = None,
    ) -> MemoryPromptContext:
        recall = self.recall_context(
            db,
            chat_id,
            session,
            fields,
            query,
        )
        if edited_user_rowid is None:
            summary = self.summary_for_prompt(db, chat_id, session)
        else:
            _stored_summary, covered_until = self.summary_state(
                db,
                chat_id,
                session["session_id"],
            )
            if int(covered_until) >= int(edited_user_rowid):
                summary = ""
            else:
                summary = self.summary_for_prompt(db, chat_id, session)
        return MemoryPromptContext(
            recall=str(recall or ""),
            summary=str(summary or ""),
        )

    def summary_status(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
    ) -> tuple[str, int]:
        summary, covered_until = self.summary_state(
            db,
            chat_id,
            session_id,
        )
        return str(summary or ""), int(covered_until)

    def retain(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session: dict[str, str],
        fields: dict[str, str],
    ) -> None:
        self.retain_session(db, chat_id, session, fields)

    def purge_session(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
    ) -> int:
        return int(self.purge_session_memory(db, chat_id, session_id))

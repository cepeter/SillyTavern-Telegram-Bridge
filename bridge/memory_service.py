"""Application service for conversation-memory orchestration."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from bridge.port_contracts import PurgeSessionMemory, ReadSummary, ReadSummaryState, RecallMemory, RetainSessionMemory


@dataclass(frozen=True)
class MemoryPromptContext:
    """Memory material supplied to prompt assembly."""

    recall: str
    summary: str


@dataclass(frozen=True)
class MemoryService:
    """Coordinate prompt memory, retention, and session-memory purge."""

    recall_context: RecallMemory
    summary_for_prompt: ReadSummary
    summary_state: ReadSummaryState
    retain_session: RetainSessionMemory
    purge_session_memory: PurgeSessionMemory

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
        result = self.purge_session_memory(db, chat_id, session_id)
        return 0 if result is None else int(result)

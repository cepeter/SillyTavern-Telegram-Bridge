"""Application service for canonical group state and turn operations."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bridge.port_contracts import (
    GroupAdvance,
    GroupSetupRead,
    GroupSpeakerRead,
    GroupStateRead,
    GroupStateWrite,
    GroupUserTurn,
)


@dataclass(frozen=True)
class GroupService:
    load_state: GroupStateRead
    save_state: GroupStateWrite
    user_turn_allowed_backend: GroupUserTurn
    claim_user_turn_backend: GroupUserTurn
    pass_user_turn_backend: GroupUserTurn
    setup_state_backend: GroupSetupRead
    character_option_label_backend: Callable[[Path], str]
    resolve_character_backend: Callable[[str], str | None]
    member_labels_backend: Callable[[list[str]], list[str]]
    current_speaker_backend: GroupSpeakerRead
    advance_turn_backend: GroupAdvance

    def state(self, db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, object]:
        return self.load_state(db, chat_id, session_id)

    def save(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        state: dict[str, object],
        operation_id: int | str | None = None,
    ) -> bool:
        return bool(self.save_state(db, chat_id, session_id, state, operation_id))

    def user_turn_allowed(self, db: sqlite3.Connection, chat_id: str, session_id: str, sender_id: str) -> bool:
        return bool(self.user_turn_allowed_backend(db, chat_id, session_id, sender_id))

    def claim_user_turn(self, db: sqlite3.Connection, chat_id: str, session_id: str, sender_id: str) -> bool:
        return bool(self.claim_user_turn_backend(db, chat_id, session_id, sender_id))

    def pass_user_turn(self, db: sqlite3.Connection, chat_id: str, session_id: str, sender_id: str) -> bool:
        return bool(self.pass_user_turn_backend(db, chat_id, session_id, sender_id))

    def setup_state(self, db: sqlite3.Connection, chat_id: str, session_id: str) -> dict | None:
        return self.setup_state_backend(db, chat_id, session_id)

    def character_option_label(self, path: Path) -> str:
        return str(self.character_option_label_backend(path))

    def resolve_character(self, requested: str) -> str | None:
        return self.resolve_character_backend(requested)

    def member_labels(self, member_files: list[str]) -> list[str]:
        return list(self.member_labels_backend(member_files))

    def current_speaker(
        self, db: sqlite3.Connection, chat_id: str, session: dict[str, str], user_text: str = ""
    ) -> tuple[str, dict[str, object]] | None:
        return self.current_speaker_backend(db, chat_id, session, user_text)

    def advance_turn(
        self, db: sqlite3.Connection, chat_id: str, session_id: str, operation_id: int | str | None = None
    ) -> None:
        self.advance_turn_backend(db, chat_id, session_id, operation_id)

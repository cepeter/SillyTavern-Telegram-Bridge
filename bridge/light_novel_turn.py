"""Per-generation Light Novel envelope state shared by narrative generation paths."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from bridge.job_store import job_actor_id
from bridge.light_novel_format import add_inline_contract, parse_story_response
from bridge.light_novel_repository import ChoiceSet, fail_choice_generation
from bridge.light_novel_service import attach_turn, prepare_turn
from bridge.sqlite_store import write_transaction


@dataclass
class NovelTurn:
    record: ChoiceSet
    choices: list[str] | None = None
    choices_failed: bool = False

    def messages(self, messages: list[dict], language: str) -> list[dict]:
        if self.record.strategy != "a":
            return messages
        return add_inline_contract(messages, self.record.requested_count, language)

    def extract(self, raw: str) -> str:
        if self.record.strategy != "a":
            return raw
        story, self.choices = parse_story_response(raw, self.record.requested_count)
        self.choices_failed = self.choices is None
        return story

    def commit(self, db: sqlite3.Connection, assistant_rowid: int, story: str) -> None:
        with write_transaction(db):
            attach_turn(db, self.record, assistant_rowid, story, self.choices)
            if self.choices_failed:
                fail_choice_generation(db, self.record.nonce)


def begin_novel_turn(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict,
    kind: str,
    operation_id: int | str | None,
) -> NovelTurn | None:
    identity = str(operation_id) if operation_id is not None else f"direct-{time.time_ns()}"
    actor_id = str(session.get("_actor_id") or "")
    if not actor_id and isinstance(operation_id, int):
        actor_id = job_actor_id(db, operation_id)
    record = prepare_turn(db, chat_id, session, f"{kind}:{identity}", actor_id)
    return NovelTurn(record) if record is not None else None

"""SQL-only choice sets. Callers own transactions, authorization and network work."""

from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass

from bridge.repository_contracts import require_active_transaction


@dataclass(frozen=True)
class ChoiceSet:
    id: int
    chat_id: str
    session_id: str
    epoch: int
    turn_key: str
    assistant_rowid: int | None
    story_hash: str
    nonce: str
    strategy: str
    requested_count: int
    choices: tuple[str, ...]
    generation_status: str
    state: str
    actor_id: str
    model_id: str
    selected_index: int | None
    panel_message_id: int | None
    job_id: int | None
    lease_token: str
    lease_until: float


_COLUMNS = (
    "id,chat_id,session_id,epoch,turn_key,assistant_rowid,story_hash,nonce,strategy,"
    "requested_count,choices_json,generation_status,state,actor_id,model_id,selected_index,"
    "panel_message_id,job_id,lease_token,lease_until"
)


def _record(row: tuple | None) -> ChoiceSet | None:
    if row is None:
        return None
    values = list(row)
    decoded = json.loads(values[10])
    if not isinstance(decoded, list) or any(not isinstance(text, str) for text in decoded):
        raise ValueError("Invalid stored choices")
    values[10] = tuple(decoded)
    return ChoiceSet(*values)


def load_choice_set(db: sqlite3.Connection, nonce: str) -> ChoiceSet | None:
    return _record(db.execute(f"SELECT {_COLUMNS} FROM light_novel_choice_sets WHERE nonce=?", (nonce,)).fetchone())  # noqa: S608 -- fixed columns


def latest_choice_set(db: sqlite3.Connection, chat_id: str, session_id: str) -> ChoiceSet | None:
    return _record(
        db.execute(
            f"SELECT {_COLUMNS} FROM light_novel_choice_sets WHERE chat_id=? AND session_id=? ORDER BY id DESC LIMIT 1",  # noqa: S608 -- fixed columns
            (chat_id, session_id),
        ).fetchone()
    )


def choice_set_for_assistant(db: sqlite3.Connection, chat_id: str, session_id: str, rowid: int) -> ChoiceSet | None:
    return _record(
        db.execute(
            f"SELECT {_COLUMNS} FROM light_novel_choice_sets "  # noqa: S608 -- fixed columns
            "WHERE chat_id=? AND session_id=? AND assistant_rowid=? ORDER BY id DESC LIMIT 1",
            (chat_id, session_id, rowid),
        ).fetchone()
    )


def reserve_choice_set(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    epoch: int,
    turn_key: str,
    strategy: str,
    count: int,
    actor_id: str,
    model_id: str,
    now: float,
) -> ChoiceSet:
    require_active_transaction(db)
    if strategy not in {"a", "b", "c"} or count not in {2, 3, 4}:
        raise ValueError("Invalid choice strategy or count")
    db.execute(
        "INSERT OR IGNORE INTO light_novel_choice_sets(chat_id,session_id,epoch,turn_key,nonce,strategy,"
        "requested_count,actor_id,model_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            chat_id,
            session_id,
            epoch,
            turn_key,
            secrets.token_urlsafe(18),
            strategy,
            count,
            actor_id,
            model_id,
            now,
            now,
        ),
    )
    result = _record(
        db.execute(
            f"SELECT {_COLUMNS} FROM light_novel_choice_sets "  # noqa: S608 -- fixed columns
            "WHERE chat_id=? AND session_id=? AND epoch=? AND turn_key=?",
            (chat_id, session_id, epoch, turn_key),
        ).fetchone()
    )
    if result is None:
        raise RuntimeError("Could not reserve choices")
    return result


def attach_choice_set(
    db: sqlite3.Connection, nonce: str, rowid: int, story_hash: str, choices: list[str] | None = None
) -> None:
    require_active_transaction(db)
    db.execute(
        "UPDATE light_novel_choice_sets SET assistant_rowid=?,story_hash=? WHERE nonce=? AND state='open'",
        (rowid, story_hash, nonce),
    )
    if choices is not None:
        db.execute(
            "UPDATE light_novel_choice_sets SET choices_json=?,generation_status='ready' WH"
            "ERE nonce=? AND state='open' AND requested_count=?",
            (json.dumps(choices, ensure_ascii=False), nonce, len(choices)),
        )


def invalidate_choice_sets(db: sqlite3.Connection, chat_id: str, session_id: str, except_nonce: str = "") -> list[int]:
    require_active_transaction(db)
    panels = [
        int(row[0])
        for row in db.execute(
            "SELECT panel_message_id FROM light_novel_choice_sets WHERE chat_id=? AND sessi"
            "on_id=? AND state='open' AND nonce<>? AND panel_message_id IS NOT NULL",
            (chat_id, session_id, except_nonce),
        )
    ]
    db.execute(
        "UPDATE light_novel_choice_sets SET state='invalidated',lease_token='',lease_un"
        "til=0 WHERE chat_id=? AND session_id=? AND state='open' AND nonce<>?",
        (chat_id, session_id, except_nonce),
    )
    return panels


def bind_choice_panel(db: sqlite3.Connection, nonce: str, message_id: int) -> bool:
    require_active_transaction(db)
    return (
        db.execute(
            "UPDATE light_novel_choice_sets SET panel_message_id=? WHERE nonce=? AND state='open'", (message_id, nonce)
        ).rowcount
        == 1
    )


def claim_choice_generation(db: sqlite3.Connection, nonce: str, token: str, now: float) -> bool:
    require_active_transaction(db)
    return (
        db.execute(
            "UPDATE light_novel_choice_sets SET generation_status='pending',lease_token=?,lease_until=?,updated_at=? "
            "WHERE nonce=? AND state='open' AND generation_status<>'ready' AND lease_until<=?",
            (token, now + 90, now, nonce, now),
        ).rowcount
        == 1
    )


def complete_choice_generation(
    db: sqlite3.Connection, nonce: str, token: str, choices: list[str] | None, now: float
) -> bool:
    require_active_transaction(db)
    return (
        db.execute(
            "UPDATE light_novel_choice_sets SET choices_json=?,generation_status=?,lease_to"
            "ken='',lease_until=0,updated_at=? "
            "WHERE nonce=? AND state='open' AND lease_token=?",
            (json.dumps(choices or [], ensure_ascii=False), "ready" if choices else "failed", now, nonce, token),
        ).rowcount
        == 1
    )


def fail_choice_generation(db: sqlite3.Connection, nonce: str) -> None:
    require_active_transaction(db)
    db.execute(
        "UPDATE light_novel_choice_sets SET generation_status='failed',lease_token='',l"
        "ease_until=0 WHERE nonce=? AND state='open' AND generation_status<>'ready'",
        (nonce,),
    )


def consume_choice_set(
    db: sqlite3.Connection,
    nonce: str,
    index: int,
    chat_id: str,
    session_id: str,
    actor_id: str,
    epoch: int,
    panel_message_id: int,
) -> ChoiceSet:
    require_active_transaction(db)
    record = load_choice_set(db, nonce)
    if record is None or (
        record.chat_id,
        record.session_id,
        record.actor_id,
        record.epoch,
        record.panel_message_id,
    ) != (chat_id, session_id, actor_id, epoch, panel_message_id):
        raise ValueError("Choice expired")
    if record.state == "consumed":
        raise ValueError("Choice already used")
    if record.state != "open" or record.generation_status != "ready" or not 0 <= index < len(record.choices):
        raise ValueError("Choice expired")
    if (
        db.execute(
            "UPDATE light_novel_choice_sets SET state='consumed',selected_index=? WHERE nonce=? AND state='open'",
            (index, nonce),
        ).rowcount
        != 1
    ):
        raise ValueError("Choice already used")
    result = load_choice_set(db, nonce)
    assert result is not None  # noqa: S101 -- guarded by update in the same transaction
    return result


def set_choice_job(db: sqlite3.Connection, nonce: str, job_id: int) -> None:
    require_active_transaction(db)
    db.execute("UPDATE light_novel_choice_sets SET job_id=? WHERE nonce=?", (job_id, nonce))

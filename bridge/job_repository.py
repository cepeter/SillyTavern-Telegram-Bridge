"""SQL-only durable job state transitions and update deduplication."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def insert_job(
    db: sqlite3.Connection,
    update_id: int,
    chat_id: str,
    session_id: str,
    message_id: str,
    kind: str,
    payload_json: str,
    now: float,
) -> int:
    require_active_transaction(db)
    db.execute(
        "INSERT OR IGNORE INTO jobs(update_id,chat_id,session_id,telegram_message_id,kind,payload_j"
        "son,state,attempts,last_error,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,'queued',0,'',?,?)",
        (update_id, chat_id, session_id, message_id, kind, payload_json, now, now),
    )
    row = db.execute("SELECT job_id FROM jobs WHERE update_id=?", (update_id,)).fetchone()
    if row is None:
        raise RuntimeError("job handoff failed")
    store_processed_update(db, update_id, now)
    return int(row[0])


def store_processed_update(db: sqlite3.Connection, update_id: int, now: float) -> None:
    require_active_transaction(db)
    db.execute("INSERT OR IGNORE INTO processed_updates(update_id,processed_at) VALUES(?,?)", (update_id, now))


def load_job_payload(db: sqlite3.Connection, job_id: int) -> str:
    row = db.execute("SELECT payload_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    return str(row[0] or "{}") if row else "{}"


def schedule_job(db: sqlite3.Connection, job_id: int, now: float) -> bool:
    require_active_transaction(db)
    return (
        db.execute(
            "UPDATE jobs SET state='scheduled', updated_at=? WHERE job_id=? AND state='queued'",
            (now, job_id),
        ).rowcount
        == 1
    )


def start_job(db: sqlite3.Connection, job_id: int, now: float) -> bool:
    require_active_transaction(db)
    return (
        db.execute(
            "UPDATE jobs SET state='running', attempts=attempts+1, updated_at=? WHERE job_id=? AND stat"
            "e IN ('queued','scheduled')",
            (now, job_id),
        ).rowcount
        == 1
    )


def finish_job_row(db: sqlite3.Connection, job_id: int, state: str, error: str, now: float) -> None:
    require_active_transaction(db)
    db.execute("UPDATE jobs SET state=?, last_error=?, updated_at=? WHERE job_id=?", (state, error, now, job_id))


def reset_running_jobs(db: sqlite3.Connection, now: float) -> None:
    require_active_transaction(db)
    db.execute("UPDATE jobs SET state='queued', updated_at=? WHERE state IN ('running','scheduled')", (now,))


def queued_job_rows(db: sqlite3.Connection) -> list[tuple]:
    return db.execute(
        "SELECT job_id,chat_id,session_id,telegram_message_id,kind,payload_json "
        "FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 128"
    ).fetchall()

"""Durable job persistence use cases; each transition joins or owns one short transaction."""

from __future__ import annotations

import json
import logging
import sqlite3
import time

from bridge.job_repository import (
    finish_job_row,
    insert_job,
    load_job_payload,
    queued_job_rows,
    replace_job_payload,
    reset_running_jobs,
    schedule_job,
    start_job,
)
from bridge.sqlite_store import write_transaction


def enqueue_job(
    db: sqlite3.Connection,
    update_id: int,
    chat_id: str,
    session_id: str,
    telegram_message_id: int,
    kind: str,
    payload: dict,
) -> int:
    encoded = json.dumps(payload, ensure_ascii=False)
    with write_transaction(db):
        return insert_job(db, update_id, chat_id, session_id, str(telegram_message_id), kind, encoded, time.time())


def job_actor_id(db: sqlite3.Connection, job_id: int | None) -> str:
    if job_id is None:
        return ""
    try:
        payload = json.loads(load_job_payload(db, int(job_id)))
    except (TypeError, json.JSONDecodeError):
        return ""
    return str(payload.get("actor_id") or "") if isinstance(payload, dict) else ""


def store_job_payload(db: sqlite3.Connection, job_id: int, payload: dict) -> bool:
    encoded = json.dumps(payload, ensure_ascii=False)
    with write_transaction(db):
        return replace_job_payload(db, job_id, encoded, time.time())


def mark_job_scheduled(db: sqlite3.Connection, job_id: int) -> bool:
    with write_transaction(db):
        return schedule_job(db, job_id, time.time())


def mark_job_running(db: sqlite3.Connection, job_id: int) -> bool:
    with write_transaction(db):
        return start_job(db, job_id, time.time())


def finish_job(db: sqlite3.Connection, job_id: int, state: str, error: str = "") -> bool:
    nested = db.in_transaction
    try:
        with write_transaction(db):
            finish_job_row(db, job_id, state, error[:1000], time.time())
        return True
    except sqlite3.OperationalError as exc:
        if nested or ("locked" not in str(exc).casefold() and "busy" not in str(exc).casefold()):
            raise
        # write_transaction already rolled back this owned transition. Never
        # roll back an enclosing caller's transaction or hide its failure.
        logging.warning("Could not persist job %s transition to %s: %s", job_id, state, exc)
        return False


def recover_jobs(db: sqlite3.Connection, recover_running: bool = True) -> list[tuple]:
    if not recover_running:
        return queued_job_rows(db)
    with write_transaction(db):
        reset_running_jobs(db, time.time())
        return queued_job_rows(db)

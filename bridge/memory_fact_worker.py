"""Bounded native fact dispatch preserves durable attempt and generation fences."""

import time

from bridge import memory_backend
from bridge.hindsight_diagnostics import hindsight_fact_attempt
from bridge.memory_fact_store import index_fact_is_current
from bridge.memory_retirement_store import reopen_retirement
from bridge.memory_store import (
    begin_external_memory_attempt,
    claim_is_current,
    finish_archival_attempt,
    resolve_archival_attempt,
)
from bridge.sqlite_store import write_transaction

MAX_FACTS_PER_RUN = 8


def retire_fact_document(db, document_id):
    db.execute("UPDATE memory_fact_index SET state='retired' WHERE document_id=?", (document_id,))
    row = db.execute("SELECT chat_id,session_id FROM memory_fact_index WHERE document_id=?", (document_id,)).fetchone()
    if row:
        reopen_retirement(db, *row, document_id)


def run_fact_index(db, claim, fields, *, app_settings):
    """Commit intent before I/O; publish only a still-current fact and owned claim."""
    rows = db.execute(
        "SELECT document_id FROM memory_fact_index WHERE chat_id=? AND session_id=? AND session_created_at=? "
        "AND state='pending' ORDER BY created_at,document_id LIMIT ?",
        (claim.chat_id, claim.session_id, claim.session_created_at, MAX_FACTS_PER_RUN),
    ).fetchall()
    calls = 0
    for (document_id,) in rows:
        with memory_backend.hindsight_session_lock(claim.chat_id, claim.session_id), write_transaction(db):
            if not claim_is_current(db, claim):
                return "stale_source", calls
            if memory_backend.memory_mode(db, claim.chat_id) != "on":
                return "disabled", calls
            fact = index_fact_is_current(db, document_id)
            if fact is None:
                retire_fact_document(db, document_id)
                continue
            db.execute("UPDATE memory_jobs SET lease_deadline=? WHERE lease_token=?", (time.time() + 900, claim.token))
            token = begin_external_memory_attempt(
                db,
                document_id=document_id,
                chat_id=claim.chat_id,
                session_id=claim.session_id,
                session_created_at=claim.session_created_at,
                kind="native_fact",
            )
        calls += 1
        with hindsight_fact_attempt(token, document_id):
            retained = memory_backend._retain_with_client(
                claim.chat_id,
                claim.session_id,
                document_id,
                fields.get("name", "Story"),
                fact.fact.summary,
                "Locally accepted native story fact; audience is enforced by SQLite",
                "native_fact",
                app_settings=app_settings,
                generation_tags=tuple(
                    memory_backend.hindsight_generation_tags(
                        claim.chat_id, claim.session_id, claim.session_created_at, claim.purge_epoch
                    )
                ),
            )
        if retained:
            finish_archival_attempt(db, token)
        with memory_backend.hindsight_session_lock(claim.chat_id, claim.session_id), write_transaction(db):
            if index_fact_is_current(db, document_id) is None:
                retire_fact_document(db, document_id)
                resolve_archival_attempt(db, token)
                return "stale_source", calls
            if not claim_is_current(db, claim):
                resolve_archival_attempt(db, token)
                return "stale_source", calls
            if memory_backend.memory_mode(db, claim.chat_id) != "on":
                resolve_archival_attempt(db, token)
                return "disabled", calls
            if not retained:
                db.execute(
                    "UPDATE memory_fact_index SET last_error='retain_failed' WHERE document_id=?", (document_id,)
                )
                return "retain_failed", calls
            db.execute(
                "UPDATE memory_fact_index SET state='retained',last_error='' WHERE document_id=?", (document_id,)
            )
            db.execute(
                "INSERT OR REPLACE INTO hindsight_documents(chat_id,session_id,document_id,kind,created_at) "
                "VALUES(?,?,?,'native_fact',?)",
                (claim.chat_id, claim.session_id, document_id, time.time()),
            )
            resolve_archival_attempt(db, token)
    return "complete", calls

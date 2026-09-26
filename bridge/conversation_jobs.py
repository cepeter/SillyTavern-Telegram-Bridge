"""Reject queued narrative work from an earlier reset/configuration epoch."""

from __future__ import annotations

import json
import sqlite3

from bridge.conversation_lifecycle import conversation_state


def narrative_job_is_current(db: sqlite3.Connection, job_id: int | None) -> bool:
    if job_id is None:
        return True
    row = db.execute("SELECT chat_id,session_id,payload_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        return True
    try:
        payload = json.loads(row[2])
        if "epoch" not in payload:
            return True
        return int(payload["epoch"]) == conversation_state(db, str(row[0]), str(row[1])).epoch
    except (TypeError, ValueError):
        return False

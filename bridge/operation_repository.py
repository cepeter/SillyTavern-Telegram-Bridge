"""Canonical operation repository owner."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def claim_operation(
    db: sqlite3.Connection,
    operation_id: int | str | None,
    kind: str,
    now: float,
) -> bool:
    if operation_id is None:
        return True
    require_active_transaction(db)
    operation_id = str(operation_id)
    cursor = db.execute(
        "INSERT OR IGNORE INTO operations(operation_id,kind,state,created_at,updated_at) VALUES(?,?,'in_progress',?,?)",
        (operation_id, str(kind), float(now), float(now)),
    )
    if cursor.rowcount == 1:
        return True
    row = db.execute(
        "SELECT state FROM operations WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    return not row or str(row[0]) != "applied"


def mark_operation_applied(
    db: sqlite3.Connection,
    operation_id: int | str | None,
    kind: str,
    now: float,
) -> None:
    if operation_id is None:
        return
    require_active_transaction(db)
    db.execute(
        "UPDATE operations SET state='applied',kind=?,updated_at=? WHERE operation_id=?",
        (str(kind), float(now), str(operation_id)),
    )


def load_operation_phase(db: sqlite3.Connection, operation_id: str) -> str:
    row = db.execute("SELECT state FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
    return str(row[0]) if row else ""


def write_operation_phase(db: sqlite3.Connection, operation_id: str, kind: str, phase: str, now: float) -> None:
    require_active_transaction(db)
    db.execute(
        "UPDATE operations SET state=?, kind=?, updated_at=? WHERE operation_id=?", (phase, kind, now, operation_id)
    )

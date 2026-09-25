"""Durable operation boundaries. External I/O must occur between committed phases."""

from __future__ import annotations

import sqlite3
import time

from bridge.operation_repository import (
    claim_operation,
    load_operation_phase,
    mark_operation_applied,
    write_operation_phase,
)
from bridge.sqlite_store import write_transaction


def operation_phase(db: sqlite3.Connection, operation_id: int | str | None) -> str:
    return "" if operation_id is None else load_operation_phase(db, str(operation_id))


def set_operation_phase(db: sqlite3.Connection, operation_id: int | str | None, kind: str, phase: str) -> None:
    if operation_id is not None:
        with write_transaction(db):
            write_operation_phase(db, str(operation_id), kind, phase, time.time())


def begin_operation(db: sqlite3.Connection, operation_id: int | str | None, kind: str) -> bool:
    """Commit preparation when standalone; join an explicitly enclosing unit of work."""
    if operation_id is None:
        return True
    with write_transaction(db):
        return claim_operation(db, operation_id, kind, time.time())


def operation_was_applied(db: sqlite3.Connection, operation_id: int | str | None) -> bool:
    return operation_phase(db, operation_id) == "applied"


def record_operation(db: sqlite3.Connection, operation_id: int | str | None, kind: str) -> None:
    if operation_id is not None:
        with write_transaction(db):
            mark_operation_applied(db, operation_id, kind, time.time())

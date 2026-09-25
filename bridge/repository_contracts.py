"""Transaction precondition shared by SQL-only repository writers."""

from __future__ import annotations

import sqlite3


def require_active_transaction(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise RuntimeError("repository write requires an active caller-owned transaction")

"""Canonical meta repository owner."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def load_meta_value(
    db: sqlite3.Connection,
    key: str,
    default: str = "",
) -> str:
    row = db.execute(
        "SELECT value FROM meta WHERE key=?",
        (str(key),),
    ).fetchone()
    return str(row[0]) if row else str(default)


def store_meta_value(
    db: sqlite3.Connection,
    key: str,
    value: str,
) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
        (str(key), str(value)),
    )


def delete_meta_value(
    db: sqlite3.Connection,
    key: str,
) -> None:
    require_active_transaction(db)
    db.execute(
        "DELETE FROM meta WHERE key=?",
        (str(key),),
    )

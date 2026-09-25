"""Metadata use cases; reads are pure and writes join the caller's transaction."""

from __future__ import annotations

import sqlite3

from bridge.meta_repository import load_meta_value, store_meta_value
from bridge.sqlite_store import write_transaction


def get_meta(db: sqlite3.Connection, key: str, default: str = "") -> str:
    return load_meta_value(db, key, default)


def set_meta(db: sqlite3.Connection, key: str, value: str) -> None:
    with write_transaction(db):
        store_meta_value(db, key, value)

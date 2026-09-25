"""Generation settings and preset use cases over transaction-pure SQL storage."""

from __future__ import annotations

import json
import sqlite3
import time

from bridge.config import GENERATION_DEFAULTS
from bridge.generation_settings_repository import (
    SETTINGS_COLUMNS,
    delete_preset_row,
    ensure_settings_row,
    list_preset_names,
    load_preset_json,
    load_settings_row,
    store_preset_json,
    update_settings_row,
)
from bridge.sqlite_store import write_transaction


def get_generation_settings(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, object]:
    row = load_settings_row(db, chat_id, session_id)
    return dict(GENERATION_DEFAULTS) if row is None else dict(zip(SETTINGS_COLUMNS, row, strict=False))


def update_generation_settings(
    db: sqlite3.Connection, chat_id: str, session_id: str, **values: object
) -> dict[str, object]:
    values = {key: value for key, value in values.items() if key in GENERATION_DEFAULTS}
    with write_transaction(db):
        ensure_settings_row(db, chat_id, session_id, GENERATION_DEFAULTS)
        update_settings_row(db, chat_id, session_id, values)
    return get_generation_settings(db, chat_id, session_id)


def preset_names(db: sqlite3.Connection, chat_id: str) -> list[str]:
    return list_preset_names(db, chat_id)


def save_generation_preset(db: sqlite3.Connection, chat_id: str, name: str, settings: dict[str, object]) -> None:
    encoded = json.dumps(settings, ensure_ascii=False)
    with write_transaction(db):
        store_preset_json(db, chat_id, name, encoded, time.time())


def load_generation_preset(db: sqlite3.Connection, chat_id: str, name: str) -> dict[str, object] | None:
    raw = load_preset_json(db, chat_id, name)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def delete_generation_preset(db: sqlite3.Connection, chat_id: str, name: str) -> bool:
    with write_transaction(db):
        return delete_preset_row(db, chat_id, name)

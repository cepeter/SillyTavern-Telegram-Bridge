"""Telegram update coordinator and durable completion boundary."""
from __future__ import annotations

import sqlite3
import time

from bridge.composition import BridgeServices
from bridge.database import run_write_txn, set_meta
from bridge.update_callback_routing import route_callback_update
from bridge.update_message_routing import (
    route_edited_message_update,
    route_message_update,
)


def complete_update(
    db: sqlite3.Connection,
    update_id: int,
    offset: int,
) -> None:
    def write():
        db.execute(
            "INSERT OR IGNORE INTO processed_updates("
            "update_id,processed_at"
            ") VALUES(?,?)",
            (update_id, time.time()),
        )
        set_meta(db, "telegram_offset", str(offset))

    run_write_txn(db, write)


def route_update(
    services: BridgeServices,
    db: sqlite3.Connection,
    fields: dict,
    update: dict,
    offset: int,
    permitted: frozenset[str],
) -> int:
    update_id = int(update["update_id"])
    next_offset = max(offset, update_id + 1)

    if db.execute(
        "SELECT 1 FROM processed_updates WHERE update_id=?",
        (update_id,),
    ).fetchone():
        complete_update(db, update_id, next_offset)
        return next_offset

    callback = update.get("callback_query")
    if callback:
        route_callback_update(
            services,
            db,
            callback,
            update_id,
            permitted,
        )
        complete_update(db, update_id, next_offset)
        return next_offset

    edited_message = update.get("edited_message")
    if edited_message:
        route_edited_message_update(
            services,
            db,
            edited_message,
            update_id,
            permitted,
        )
        complete_update(db, update_id, next_offset)
        return next_offset

    should_complete = route_message_update(
        services,
        db,
        fields,
        update.get("message") or {},
        update_id,
        permitted,
    )
    if should_complete:
        complete_update(db, update_id, next_offset)
    return next_offset

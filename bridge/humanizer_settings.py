"""Validated, session-scoped Humanizer preference using existing metadata storage."""

from __future__ import annotations

import sqlite3

from bridge.metadata import get_meta


def normalize_humanizer(value: str | None) -> str:
    normalized = str(value or "").strip().casefold()
    normalized = {
        "true": "on",
        "yes": "on",
        "enabled": "on",
        "1": "on",
        "false": "off",
        "no": "off",
        "disabled": "off",
        "0": "off",
    }.get(normalized, normalized)
    if normalized not in {"on", "off"}:
        raise ValueError("use on or off")
    return normalized


def humanizer_enabled(value: str | None) -> bool:
    return normalize_humanizer(value or "off") == "on"


def humanizer_label(value: str | None) -> str:
    return "On" if humanizer_enabled(value) else "Off"


def humanizer_key(chat_id: str, session_id: str) -> str:
    return f"humanizer:{chat_id}:{session_id}"


def session_humanizer(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    try:
        return normalize_humanizer(get_meta(db, humanizer_key(chat_id, session_id), "off"))
    except ValueError:
        # Invalid stored values must never opt a user into an extra provider call.
        return "off"

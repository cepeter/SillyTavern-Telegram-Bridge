"""Immutable request values shared by pure call contracts and application adapters."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from bridge.settings import AppSettings


@dataclass(frozen=True)
class RequestContext:
    db: sqlite3.Connection = field(repr=False)
    session_id: str
    actor_id: str = ""
    app_settings: AppSettings = field(kw_only=True)


@dataclass(frozen=True)
class PreparedMessage:
    stripped: str
    command: str
    fields: dict[str, Any]
    session: dict[str, Any]
    session_id: str
    current_model: str
    current_persona: str
    user_name: str
    group_turn: tuple[str, dict[str, Any]] | None
    group_context: str
    request_context: RequestContext

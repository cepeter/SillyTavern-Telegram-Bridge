"""Canonical model selection owner."""

from __future__ import annotations

import json
import re
import sqlite3
import time

import bridge.limits as _limits
from bridge.metadata import get_meta, set_meta
from bridge.settings import AppSettings


def task_model_key(chat_id: str, session_id: str, task: str = "utility") -> str:
    task_name = re.sub(r"[^a-z0-9_-]+", "-", str(task or "utility").casefold()).strip("-") or "utility"
    return f"task_model:{task_name}:{chat_id}:{session_id}"


def task_model_for_session(
    db: sqlite3.Connection, chat_id: str, session: dict[str, str], task: str = "utility", *, app_settings: AppSettings
) -> str:
    """Resolve a per-task model with utility -> main-model fallback."""
    session_id = str(session["session_id"])
    task_name = str(task or "utility").casefold()
    model = get_meta(db, task_model_key(chat_id, session_id, task_name), "").strip()
    if not model and task_name != "utility":
        model = get_meta(db, task_model_key(chat_id, session_id, "utility"), "").strip()
    return model or str(session.get("model_id") or app_settings.default_model)


def set_task_model(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    model: str,
    task: str = "utility",
) -> str:
    value = str(model or "").strip()
    if value.casefold() in {"main", "default", "off", "inherit"}:
        value = ""
    if value and (len(value) > 200 or any(ch.isspace() for ch in value)):
        raise ValueError("model id must be at most 200 characters and contain no whitespace")
    set_meta(db, task_model_key(chat_id, session_id, task), value)
    return value


def model_target_selection_key(chat_id: str, session_id: str) -> str:
    return f"model_target_selection:{chat_id}:{session_id}"


def set_model_target_selection(db: sqlite3.Connection, chat_id: str, session_id: str, target: str) -> None:
    if target not in {"story", "utility"}:
        raise ValueError("invalid model target")
    set_meta(
        db,
        model_target_selection_key(chat_id, session_id),
        json.dumps({"target": target, "expires_at": time.time() + _limits.PENDING_SETTINGS_TTL_SECONDS}),
    )


def get_model_target_selection(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    raw = get_meta(db, model_target_selection_key(chat_id, session_id), "")
    try:
        state = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return ""
    if float(state.get("expires_at", 0)) < time.time():
        set_meta(db, model_target_selection_key(chat_id, session_id), "")
        return ""
    target = str(state.get("target") or "")
    return target if target in {"story", "utility"} else ""


def clear_model_target_selection(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    set_meta(db, model_target_selection_key(chat_id, session_id), "")

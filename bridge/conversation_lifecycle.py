"""Session conversation policy. No transport or provider side effects."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from bridge.light_novel_repository import invalidate_choice_sets
from bridge.meta_repository import load_meta_value, store_meta_value
from bridge.sqlite_store import write_transaction

START_REQUIRED = "Please use /start command."
ALREADY_STARTED = "This session has already started."


@dataclass(frozen=True)
class ConversationState:
    mode: str = "normal"
    strategy: str = ""
    started: bool = False
    epoch: int = 0


def lifecycle_key(name: str, chat_id: str, session_id: str) -> str:
    if name not in {"mode", "strategy", "started", "epoch", "opening"}:
        raise ValueError("Invalid lifecycle key")
    prefix = "lightnovel_strategy" if name == "strategy" else f"conversation_{name}"
    return f"{prefix}:{chat_id}:{session_id}"


def conversation_state(db: sqlite3.Connection, chat_id: str, session_id: str) -> ConversationState:
    mode = load_meta_value(db, lifecycle_key("mode", chat_id, session_id), "normal")
    strategy = load_meta_value(db, lifecycle_key("strategy", chat_id, session_id), "")
    started = load_meta_value(db, lifecycle_key("started", chat_id, session_id), "0")
    epoch = load_meta_value(db, lifecycle_key("epoch", chat_id, session_id), "0")
    if mode not in {"normal", "lightnovel"} or (mode == "lightnovel" and strategy not in {"a", "b", "c"}):
        raise ValueError("Invalid conversation configuration; use /character")
    return ConversationState(mode, strategy if mode == "lightnovel" else "", started == "1", int(epoch))


def initialize_conversation(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    with write_transaction(db):
        for name, value in (("mode", "normal"), ("strategy", ""), ("started", "0"), ("epoch", "0")):
            db.execute(
                "INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)", (lifecycle_key(name, chat_id, session_id), value)
            )


def configure_conversation(
    db: sqlite3.Connection, chat_id: str, session_id: str, mode: str, strategy: str = ""
) -> None:
    if mode not in {"normal", "lightnovel"} or (mode == "lightnovel" and strategy not in {"a", "b", "c"}):
        raise ValueError("Choose Normal or Light Novel with strategy A, B or C")
    with write_transaction(db):
        current = conversation_state(db, chat_id, session_id)
        if current.started:
            raise ValueError("This session has already started. Use /reset or /new first.")
        for name, value in (
            ("mode", mode),
            ("strategy", strategy if mode == "lightnovel" else ""),
            ("epoch", str(current.epoch + 1)),
        ):
            store_meta_value(db, lifecycle_key(name, chat_id, session_id), value)
        invalidate_choice_sets(db, chat_id, session_id)


def mark_started(db: sqlite3.Connection, chat_id: str, session_id: str, epoch: int) -> bool:
    with write_transaction(db):
        current = conversation_state(db, chat_id, session_id)
        if current.epoch != epoch or current.started:
            return False
        store_meta_value(db, lifecycle_key("started", chat_id, session_id), "1")
        return True


def reset_conversation(db: sqlite3.Connection, chat_id: str, session_id: str) -> list[int]:
    with write_transaction(db):
        current = conversation_state(db, chat_id, session_id)
        store_meta_value(db, lifecycle_key("started", chat_id, session_id), "0")
        store_meta_value(db, lifecycle_key("epoch", chat_id, session_id), str(current.epoch + 1))
        store_meta_value(db, lifecycle_key("opening", chat_id, session_id), "")
        return invalidate_choice_sets(db, chat_id, session_id)


def is_group_conversation(db: sqlite3.Connection, chat_id: str, session_id: str) -> bool:
    return (
        db.execute("SELECT 1 FROM group_sessions WHERE chat_id=? AND session_id=?", (chat_id, session_id)).fetchone()
        is not None
    )


def require_started(db: sqlite3.Connection, chat_id: str, session_id: str) -> bool:
    return is_group_conversation(db, chat_id, session_id) or conversation_state(db, chat_id, session_id).started


def is_command_text(text: str) -> bool:
    pieces = text.lstrip().split(None, 1)
    if not pieces:
        return False
    return pieces[0].startswith("/") or (pieces[0].startswith("@") and len(pieces) > 1 and pieces[1].startswith("/"))


def has_pending_management_input(db: sqlite3.Connection, chat_id: str, session_id: str, actor_id: str) -> bool:
    # Classification only. The canonical input owner still validates and consumes the value.
    prefixes = (
        "session_name_input",
        "settings_input",
        "preset_save_input",
        "stt_language_input",
        "persona_input",
        "note_input",
        "text_action_input",
        "world_upload",
        "databank_upload",
    )
    keys = [f"{prefix}:{chat_id}" for prefix in prefixes]
    keys.extend((f"character_optimizer_input:{chat_id}:{actor_id}", f"conversation_setup:{chat_id}:{actor_id}"))
    for key in keys:
        try:
            state = json.loads(load_meta_value(db, key, "") or "{}")
            if not isinstance(state, dict):
                continue
            if state.get("actor_id") not in (None, "", actor_id) or state.get("session_id") not in (
                None,
                "",
                session_id,
            ):
                continue
            if float(state.get("expires_at") or 0) <= time.time():
                continue
            if key.startswith("conversation_setup:") and state.get("stage") != "session_name":
                continue
            return True
        except (TypeError, ValueError):
            continue
    return False

"""Canonical group persistence, turn ownership, and character selection."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time

from bridge.card_content import (
    card_fields,
    card_fields_from_file,
    character_card_paths,
    read_png_chara,
    safe_character_path,
)
from bridge.database import get_meta, set_meta, write_transaction
from bridge.repositories import (
    load_group_state_row as _repo_load_group_state_row,
    mark_group_operation_applied as _repo_mark_group_operation_applied,
    store_group_state_row as _repo_store_group_state_row,
    try_claim_group_operation as _repo_try_claim_group_operation,
)


def group_state(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, object]:
    row = _repo_load_group_state_row(db, chat_id, session_id)
    if not row:
        return {"title": "Group chat", "enabled": False, "turn_index": 0, "mode": "round_robin", "forced_speaker": "", "members": [], "turn_user_id": "", "turn_users": []}
    try:
        members = json.loads(row[5])
    except (TypeError, json.JSONDecodeError):
        members = []
    try:
        turn_users = json.loads(row[7])
    except (TypeError, json.JSONDecodeError):
        turn_users = []
    return {"title": row[0], "enabled": bool(row[1]), "turn_index": int(row[2]), "mode": str(row[3] or "round_robin"), "forced_speaker": str(row[4] or ""), "members": [str(item) for item in members if isinstance(item, str)], "turn_user_id": str(row[6] or ""), "turn_users": [str(item) for item in turn_users if isinstance(item, str)]}


def group_user_turn_allowed(db: sqlite3.Connection, chat_id: str, session_id: str, sender_id: str) -> bool:
    """Allow manual-mode messages only from the current user turn owner."""
    state = group_state(db, chat_id, session_id)
    if not state["enabled"] or state["mode"] != "manual":
        return True
    sender_id = str(sender_id or "")
    if not sender_id:
        return False
    users = list(state.get("turn_users") or [])
    changed = False
    if sender_id not in users:
        users.append(sender_id)
        changed = True
    if not state.get("turn_user_id"):
        state["turn_user_id"] = sender_id
        changed = True
    state["turn_users"] = users
    if changed:
        save_group_state(db, chat_id, session_id, state)
    return state["turn_user_id"] == sender_id


def claim_group_user_turn(db: sqlite3.Connection, chat_id: str, session_id: str, sender_id: str) -> bool:
    """Claim an unassigned manual user turn or confirm its current owner."""
    state = group_state(db, chat_id, session_id)
    sender_id = str(sender_id or "")
    if not sender_id or not state["enabled"] or state["mode"] != "manual":
        return False
    if state.get("turn_user_id") and state["turn_user_id"] != sender_id:
        return False
    users = list(state.get("turn_users") or [])
    if sender_id not in users:
        users.append(sender_id)
    state["turn_users"] = users
    state["turn_user_id"] = sender_id
    save_group_state(db, chat_id, session_id, state)
    return True


def pass_group_user_turn(db: sqlite3.Connection, chat_id: str, session_id: str, sender_id: str) -> bool:
    """Pass a manual user turn to the next known group participant."""
    state = group_state(db, chat_id, session_id)
    sender_id = str(sender_id or "")
    if not sender_id or state.get("mode") != "manual" or state.get("turn_user_id") != sender_id:
        return False
    users = list(state.get("turn_users") or [])
    if len(users) <= 1:
        state["turn_user_id"] = ""
    else:
        position = users.index(sender_id) if sender_id in users else -1
        state["turn_user_id"] = users[(position + 1) % len(users)]
    save_group_state(db, chat_id, session_id, state)
    return True


def group_setup_state(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict | None:
    """Load the short-lived topic-local New group session wizard state."""
    raw = get_meta(db, f"group_setup:{chat_id}", "")
    try:
        state = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        state = {}
    if not state or state.get("session_id") != session_id or float(state.get("expires_at", 0) or 0) < time.time():
        if state:
            set_meta(db, f"group_setup:{chat_id}", "")
        return None
    return state


def group_character_option_label(path: Path) -> str:
    try:
        return str(card_fields(read_png_chara(path)).get("name") or path.stem)
    except Exception:
        return path.stem


def save_group_state(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    state: dict[str, object],
    operation_id: int | str | None = None,
) -> bool:
    now = time.time()
    with write_transaction(db):
        if not _repo_try_claim_group_operation(
            db,
            operation_id,
            "group_state",
            now,
        ):
            return False
        _repo_store_group_state_row(
            db,
            chat_id,
            session_id,
            str(state.get("title") or "Group chat"),
            bool(state.get("enabled")),
            int(state.get("turn_index") or 0),
            str(state.get("mode") or "round_robin"),
            str(state.get("forced_speaker") or ""),
            json.dumps(state.get("members") or [], ensure_ascii=False),
            str(state.get("turn_user_id") or ""),
            json.dumps(state.get("turn_users") or [], ensure_ascii=False),
            now,
        )
        _repo_mark_group_operation_applied(
            db,
            operation_id,
            "group_state",
            now,
        )
    return True


def resolve_character_file(requested: str) -> str | None:
    wanted = requested.strip().casefold()
    for path in character_card_paths():
        try:
            label = card_fields(read_png_chara(path))["name"]
        except Exception:
            label = path.stem
        if wanted in {path.name.casefold(), path.stem.casefold(), label.casefold()}:
            return path.name
    return None


def group_member_labels(member_files: list[str]) -> list[str]:
    labels = []
    for filename in member_files:
        try:
            labels.append(card_fields_from_file(filename)["name"])
        except Exception:
            labels.append(Path(filename).stem)
    return labels


def group_current_speaker(db: sqlite3.Connection, chat_id: str, session: dict[str, str], user_text: str = "") -> tuple[str, dict[str, object]] | None:
    state = group_state(db, chat_id, session["session_id"])
    members = [name for name in state["members"] if safe_character_path(name)]
    if not state["enabled"] or len(members) < 2:
        return None
    forced = str(state.get("forced_speaker") or "")
    if forced in members:
        return forced, state
    if state.get("mode") == "contextual" and user_text:
        lowered = user_text.casefold()
        for filename in members:
            label = card_fields_from_file(filename)["name"]
            if label.casefold() in lowered or Path(filename).stem.casefold() in lowered:
                return filename, state
    index = int(state["turn_index"]) % len(members)
    return members[index], state


def advance_group_turn(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    operation_id: int | str | None = None,
) -> None:
    state = group_state(db, chat_id, session_id)
    if not state["enabled"] or len(state["members"]) < 2:
        return
    state["forced_speaker"] = ""
    if state.get("mode") != "manual":
        state["turn_index"] = int(state["turn_index"]) + 1
    save_group_state(db, chat_id, session_id, state, operation_id)

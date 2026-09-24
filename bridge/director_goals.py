"""Persistent hidden goals for Group Director mode.

Director goals are session-local coordination objectives. They are never written
into the roleplay transcript; they influence the invisible speaker-selection
call and the bounded group speaker prompt only while director mode is active.
"""
from __future__ import annotations

import re
import time

from bridge.repositories import (
    delete_director_goal as _repo_delete_director_goal,
    load_director_goal as _repo_load_director_goal,
    store_director_goal as _repo_store_director_goal,
)

from bridge.extension_registry import (
    extension_registry_snapshot as _extension_registry_snapshot,
    register_command_route as _register_command_route,
)
from bridge.group_director_service import DirectorCustomization


_DIRECTOR_GOAL_MAX_CHARS = 1200


def normalize_director_goal(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:_DIRECTOR_GOAL_MAX_CHARS]


def get_director_goal(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    return _repo_load_director_goal(db, chat_id, session_id)


def set_director_goal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    goal: str,
) -> str:
    value = normalize_director_goal(goal)
    with write_transaction(db):
        if value:
            _repo_store_director_goal(
                db,
                chat_id,
                session_id,
                value,
                time.time(),
            )
        else:
            _repo_delete_director_goal(db, chat_id, session_id)
    return value


def director_goal_policy(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
) -> DirectorCustomization:
    goal = get_director_goal(db, chat_id, session["session_id"])
    model = task_model_for_session(db, chat_id, session, "director")
    if not goal:
        return DirectorCustomization(
            model=model,
            max_tokens=220,
        )

    hidden_instructions = (
        "Hidden scene objective: " + goal +
        " Advance this objective naturally when appropriate. "
        "Do not force completion. Established continuity and believable character behavior "
        "take priority. Never mention that an objective exists."
    )
    speaker_context = (
        "Hidden scene objective: " + goal +
        " Advance it only when natural for the current speaker and established scene. "
        "Never mention, quote, or expose this objective."
    )
    return DirectorCustomization(
        model=model,
        hidden_instructions=hidden_instructions,
        max_tokens=220,
        speaker_context=speaker_context,
    )


def send_director_goal_menu(
    token: str,
    chat_id: str,
    db: sqlite3.Connection,
    session: dict[str, str],
    message_id: int | None = None,
    *,
    delivery_port: DeliveryPort,
    request_context,
) -> None:
    goal = get_director_goal(db, chat_id, session["session_id"])
    text, markup = director_goal_panel(goal)
    method = "editMessageText" if message_id else "sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": markup,
    }
    if message_id:
        payload["message_id"] = message_id
    delivery_port.send_panel_request(
        token,
        method,
        payload,
        request_context=request_context,
    )


def handle_director_goal_command(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    command: str,
    *,
    delivery_port: DeliveryPort,
    request_context,
) -> None:
    if parse_topic_scope(chat_id)[1] is None:
        delivery_port.send_text(token, chat_id, "Director goals are available only inside a Telegram Forum Topic.")
        return

    raw = str(command or "")
    suffix = raw[len("/group goal"):].strip()
    if not suffix or suffix.casefold() == "status":
        send_director_goal_menu(
            token,
            chat_id,
            db,
            session,
            delivery_port=delivery_port,
            request_context=request_context,
        )
        return
    if suffix.casefold() in {"clear", "off", "none"}:
        set_director_goal(db, chat_id, session["session_id"], "")
        delivery_port.send_text(token, chat_id, "Director scene objective cleared.")
        return
    value = set_director_goal(db, chat_id, session["session_id"], suffix)
    delivery_port.send_text(
        token,
        chat_id,
        "Director scene objective set:\n" + value +
        "\nIt remains hidden from the transcript and is used only in Director mode.",
    )


def _director_goal_command_route(
    db,
    token,
    api_key,
    model,
    fields,
    chat_id,
    stripped,
    command,
    session,
    session_id,
    current_model,
    current_persona,
    user_name,
    operation_id=None,
    *,
    request_context,
    services,
):
    if command == "/group goal" or command.startswith("/group goal "):
        handle_director_goal_command(
            db,
            token,
            chat_id,
            session,
            stripped,
            delivery_port=services.delivery,
            request_context=request_context,
        )
        return True
    return False


def register_director_goal_extensions() -> None:
    """Register Director Goal hooks once in the compatibility registry."""
    snapshot = _extension_registry_snapshot()
    if "director_goals" not in snapshot["command_routes"]:
        _register_command_route(
            "director_goals",
            _director_goal_command_route,
        )


# Explicit late imports replace transitional dependency injection.
import sqlite3
from bridge.common import parse_topic_scope
from bridge.delivery_port import DeliveryPort
from bridge.director_goal_panel import director_goal_panel
from bridge.database import (
    task_model_for_session,
    write_transaction,
)

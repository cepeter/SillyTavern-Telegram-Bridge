"""Persistent hidden goals for Group Director mode.

Director goals are session-local coordination objectives. They are never written
into the roleplay transcript; they influence the invisible speaker-selection
call and the bounded group speaker prompt only while director mode is active.
"""

import re
import time

from bridge.extension_registry import (
    DirectorCustomization as _DirectorCustomization,
    register_command_route as _register_command_route,
    register_director_customization_provider as _register_director_customization_provider,
)


_DIRECTOR_GOAL_MAX_CHARS = 1200


def normalize_director_goal(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:_DIRECTOR_GOAL_MAX_CHARS]


def get_director_goal(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    ensure_director_goal_schema(db)
    row = db.execute(
        "SELECT goal FROM director_goals WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    ).fetchone()
    return str(row[0]) if row else ""


def set_director_goal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    goal: str,
) -> str:
    ensure_director_goal_schema(db)
    value = normalize_director_goal(goal)
    if not value:
        db.execute(
            "DELETE FROM director_goals WHERE chat_id=? AND session_id=?",
            (str(chat_id), str(session_id)),
        )
    else:
        db.execute(
            "INSERT OR REPLACE INTO director_goals(chat_id,session_id,goal,updated_at) "
            "VALUES(?,?,?,?)",
            (str(chat_id), str(session_id), value, time.time()),
        )
    db.commit()
    return value


def _director_goal_customization(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
) -> _DirectorCustomization:
    goal = get_director_goal(db, chat_id, session["session_id"])
    model = task_model_for_session(db, chat_id, session, "director")
    if not goal:
        return _DirectorCustomization(
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
    return _DirectorCustomization(
        model=model,
        hidden_instructions=hidden_instructions,
        max_tokens=220,
        speaker_context=speaker_context,
    )


def handle_director_goal_command(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    command: str,
) -> None:
    if parse_topic_scope(chat_id)[1] is None:
        send_text(token, chat_id, "Director goals are available only inside a Telegram Forum Topic.")
        return

    raw = str(command or "")
    suffix = raw[len("/group goal"):].strip()
    current = get_director_goal(db, chat_id, session["session_id"])
    if not suffix or suffix.casefold() == "status":
        send_director_goal_menu(token, chat_id, db, session)
        return
    if suffix.casefold() in {"clear", "off", "none"}:
        set_director_goal(db, chat_id, session["session_id"], "")
        send_text(token, chat_id, "Director scene objective cleared.")
        return
    value = set_director_goal(db, chat_id, session["session_id"], suffix)
    send_text(
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
):
    if command == "/group goal" or command.startswith("/group goal "):
        handle_director_goal_command(db, token, chat_id, session, stripped)
        return True
    return False


_register_director_customization_provider(
    "director_goals",
    _director_goal_customization,
)
_register_command_route("director_goals", _director_goal_command_route)

"""SQL-only persistence primitives for current application domains."""
from __future__ import annotations

import sqlite3


def load_director_goal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> str:
    row = db.execute(
        "SELECT goal FROM director_goals WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    ).fetchone()
    return str(row[0]) if row else ""


def store_director_goal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    goal: str,
    updated_at: float,
) -> None:
    db.execute(
        "INSERT OR REPLACE INTO director_goals"
        "(chat_id,session_id,goal,updated_at) VALUES(?,?,?,?)",
        (str(chat_id), str(session_id), str(goal), float(updated_at)),
    )


def delete_director_goal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> None:
    db.execute(
        "DELETE FROM director_goals WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    )


def load_scene_state_row(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> tuple[str, int] | None:
    row = db.execute(
        "SELECT state_json,updated_through_rowid FROM scene_states "
        "WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    ).fetchone()
    return (str(row[0] or "{}"), int(row[1] or 0)) if row else None


def delete_scene_state(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> None:
    db.execute(
        "DELETE FROM scene_states WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    )


def upsert_scene_state_if_fresh(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    state_json: str,
    through_rowid: int,
    updated_at: float,
) -> bool:
    cursor = db.execute(
        """
        INSERT INTO scene_states(
            chat_id,session_id,state_json,updated_through_rowid,updated_at
        ) VALUES(?,?,?,?,?)
        ON CONFLICT(chat_id,session_id) DO UPDATE SET
            state_json=excluded.state_json,
            updated_through_rowid=excluded.updated_through_rowid,
            updated_at=excluded.updated_at
        WHERE excluded.updated_through_rowid >= scene_states.updated_through_rowid
        """,
        (
            str(chat_id),
            str(session_id),
            str(state_json),
            int(through_rowid),
            float(updated_at),
        ),
    )
    return cursor.rowcount > 0


def load_meta_value(
    db: sqlite3.Connection,
    key: str,
    default: str = "",
) -> str:
    row = db.execute(
        "SELECT value FROM meta WHERE key=?",
        (str(key),),
    ).fetchone()
    return str(row[0]) if row else str(default)


def store_meta_value(
    db: sqlite3.Connection,
    key: str,
    value: str,
) -> None:
    db.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
        (str(key), str(value)),
    )



def count_persona_references(
    db: sqlite3.Connection,
    persona_id: str,
) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM sessions WHERE persona_id=?",
        (str(persona_id),),
    ).fetchone()
    return int(row[0] or 0) if row else 0



def count_session_messages(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM messages "
        "WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def load_group_state_row(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
):
    return db.execute(
        "SELECT title,enabled,turn_index,mode,forced_speaker,"
        "members_json,turn_user_id,turn_users_json "
        "FROM group_sessions WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    ).fetchone()


def store_group_state_row(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    title: str,
    enabled: bool,
    turn_index: int,
    mode: str,
    forced_speaker: str,
    members_json: str,
    turn_user_id: str,
    turn_users_json: str,
    updated_at: float,
) -> None:
    db.execute(
        "INSERT OR REPLACE INTO group_sessions("
        "chat_id,session_id,title,enabled,turn_index,mode,forced_speaker,"
        "members_json,turn_user_id,turn_users_json,updated_at"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            str(chat_id),
            str(session_id),
            str(title),
            int(bool(enabled)),
            int(turn_index),
            str(mode),
            str(forced_speaker),
            str(members_json),
            str(turn_user_id),
            str(turn_users_json),
            float(updated_at),
        ),
    )


def try_claim_group_operation(
    db: sqlite3.Connection,
    operation_id: int | str | None,
    kind: str,
    now: float,
) -> bool:
    if operation_id is None:
        return True
    operation_id = str(operation_id)
    cursor = db.execute(
        "INSERT OR IGNORE INTO operations("
        "operation_id,kind,state,created_at,updated_at"
        ") VALUES(?,?,'in_progress',?,?)",
        (operation_id, str(kind), float(now), float(now)),
    )
    if cursor.rowcount == 1:
        return True
    row = db.execute(
        "SELECT state FROM operations WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    return not row or str(row[0]) != "applied"


def mark_group_operation_applied(
    db: sqlite3.Connection,
    operation_id: int | str | None,
    kind: str,
    now: float,
) -> None:
    if operation_id is None:
        return
    db.execute(
        "UPDATE operations SET state='applied',kind=?,updated_at=? "
        "WHERE operation_id=?",
        (str(kind), float(now), str(operation_id)),
    )

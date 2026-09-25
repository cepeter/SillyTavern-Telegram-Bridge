"""SQL-only response variant history and selection; callers own write transactions."""

from __future__ import annotations

import sqlite3

from bridge.repository_contracts import require_active_transaction


def find_variant_user(db: sqlite3.Connection, chat_id: str, session_id: str, content: str) -> int:
    row = db.execute(
        "SELECT rowid FROM messages WHERE chat_id=? AND session_id=? AND role='user' AND conte"
        "nt=? ORDER BY rowid DESC LIMIT 1",
        (chat_id, session_id, content),
    ).fetchone()
    return int(row[0]) if row else 0


def store_variant(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    user_rowid: int,
    user_content: str,
    response: str,
    now: float,
) -> int:
    require_active_transaction(db)
    row = db.execute(
        "SELECT COALESCE(MAX(variant_index),0) FROM response_variants WHERE chat_id=? AND sess"
        "ion_id=? AND user_rowid=?",
        (chat_id, session_id, user_rowid),
    ).fetchone()
    index = int(row[0]) + 1
    db.execute(
        "UPDATE response_variants SET selected=0 WHERE chat_id=? AND session_id=? AND user_rowid=?",
        (chat_id, session_id, user_rowid),
    )
    db.execute(
        "INSERT INTO response_variants(chat_id,session_id,user_rowid,user_content,response,var"
        "iant_index,selected,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (chat_id, session_id, user_rowid, user_content, response, index, 1, now),
    )
    return index


def last_user_variant_rows(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> tuple[tuple[int, str] | None, list[tuple[int, str, int]]]:
    row = db.execute(
        "SELECT rowid,content FROM messages WHERE chat_id=? AND session_id=? AND role='user' "
        "ORDER BY created_at DESC,rowid DESC LIMIT 1",
        (chat_id, session_id),
    ).fetchone()
    if not row:
        return None, []
    variants = db.execute(
        "SELECT variant_index,response,selected FROM response_variants WHERE chat_id=? AND ses"
        "sion_id=? AND user_rowid=? "
        "ORDER BY variant_index",
        (chat_id, session_id, int(row[0])),
    ).fetchall()
    return row, variants


def select_variant(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    user_rowid: int,
    index: int,
    response: str,
    now: float,
) -> None:
    require_active_transaction(db)
    db.execute(
        "UPDATE response_variants SET selected=0 WHERE chat_id=? AND session_id=? AND user_rowid=?",
        (chat_id, session_id, user_rowid),
    )
    db.execute(
        "UPDATE response_variants SET selected=1 WHERE chat_id=? AND session_id=? AND user_rowid=? AND variant_index=?",
        (chat_id, session_id, user_rowid, index),
    )
    db.execute("DELETE FROM messages WHERE chat_id=? AND session_id=? AND rowid>?", (chat_id, session_id, user_rowid))
    db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
        (chat_id, session_id, "assistant", response, now),
    )

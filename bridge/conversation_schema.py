"""Conversation lifecycle and durable Light Novel schema migration."""

from __future__ import annotations

import sqlite3


def migrate_conversation_modes(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS light_novel_choice_sets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        epoch INTEGER NOT NULL,
        turn_key TEXT NOT NULL,
        assistant_rowid INTEGER,
        story_hash TEXT NOT NULL DEFAULT '',
        nonce TEXT NOT NULL UNIQUE,
        strategy TEXT NOT NULL CHECK(strategy IN ('a','b','c')),
        requested_count INTEGER NOT NULL CHECK(requested_count BETWEEN 2 AND 4),
        choices_json TEXT NOT NULL DEFAULT '[]',
        generation_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(generation_status IN ('pending','ready','failed')),
        state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','consumed','invalidated')),
        actor_id TEXT NOT NULL,
        model_id TEXT NOT NULL,
        selected_index INTEGER,
        panel_message_id INTEGER,
        job_id INTEGER,
        lease_token TEXT NOT NULL DEFAULT '',
        lease_until REAL NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(chat_id,session_id,epoch,turn_key)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS novel_session_idx ON light_novel_choice_sets(chat_id,session_id,state,id)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS novel_assistant_idx ON light_novel_choice_sets(chat_id,session_id,assistant_rowid)"
    )
    # This is a migration/backfill only. Runtime gates never infer state from transcript length.
    db.execute("""INSERT OR IGNORE INTO meta(key,value)
        SELECT 'conversation_started:' || s.chat_id || ':' || s.session_id,
            CASE WHEN EXISTS(SELECT 1 FROM messages m WHERE m.chat_id=s.chat_id AND m.session_id=s.session_id)
                THEN '1' ELSE '0' END FROM sessions s""")
    for prefix, value in (("conversation_mode", "normal"), ("lightnovel_strategy", ""), ("conversation_epoch", "0")):
        db.execute(
            "INSERT OR IGNORE INTO meta(key,value) SELECT ? || ':' || chat_id || ':' || session_id, ? FROM sessions",
            (prefix, value),
        )
    db.execute("""CREATE TRIGGER IF NOT EXISTS conversation_session_delete AFTER DELETE ON sessions BEGIN
        DELETE FROM light_novel_choice_sets WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        DELETE FROM meta WHERE key IN (
            'conversation_started:' || OLD.chat_id || ':' || OLD.session_id,
            'conversation_mode:' || OLD.chat_id || ':' || OLD.session_id,
            'lightnovel_strategy:' || OLD.chat_id || ':' || OLD.session_id,
            'conversation_epoch:' || OLD.chat_id || ':' || OLD.session_id,
            'conversation_opening:' || OLD.chat_id || ':' || OLD.session_id
        );
    END""")

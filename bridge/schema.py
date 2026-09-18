def _ensure_core_tables(db: sqlite3.Connection) -> None:
    """Create metadata, messages, sessions, and response variant tables."""
    db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS messages (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT 'default',
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        telegram_message_id TEXT,
        created_at REAL NOT NULL
    )""")
    columns = {row[1] for row in db.execute("PRAGMA table_info(messages)").fetchall()}
    if "session_id" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN session_id TEXT NOT NULL DEFAULT 'default'")
    if "telegram_message_id" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN telegram_message_id TEXT")
    if "telegram_message_ids" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN telegram_message_ids TEXT NOT NULL DEFAULT '[]'")
    db.execute("""CREATE TABLE IF NOT EXISTS sessions (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        title TEXT NOT NULL,
        character_file TEXT NOT NULL,
        model_id TEXT NOT NULL,
        persona_id TEXT NOT NULL,
        world_file TEXT NOT NULL,
        author_note TEXT NOT NULL DEFAULT '',
        system_prompt TEXT NOT NULL DEFAULT '',
        response_language TEXT NOT NULL DEFAULT 'auto',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY(chat_id, session_id)
    )""")
    session_columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)").fetchall()}
    if "author_note" not in session_columns:
        db.execute("ALTER TABLE sessions ADD COLUMN author_note TEXT NOT NULL DEFAULT ''")
    if "system_prompt" not in session_columns:
        db.execute("ALTER TABLE sessions ADD COLUMN system_prompt TEXT NOT NULL DEFAULT ''")
    if "response_language" not in session_columns:
        db.execute("ALTER TABLE sessions ADD COLUMN response_language TEXT NOT NULL DEFAULT 'auto'")
    db.execute("CREATE INDEX IF NOT EXISTS messages_session_idx ON messages(chat_id, session_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS messages_telegram_idx ON messages(chat_id, telegram_message_id)")
    db.execute("""CREATE TABLE IF NOT EXISTS response_variants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        user_rowid INTEGER NOT NULL DEFAULT 0,
        user_content TEXT NOT NULL,
        response TEXT NOT NULL,
        variant_index INTEGER NOT NULL,
        selected INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL
    )""")
    variant_columns = {row[1] for row in db.execute("PRAGMA table_info(response_variants)").fetchall()}
    if "user_rowid" not in variant_columns:
        db.execute("ALTER TABLE response_variants ADD COLUMN user_rowid INTEGER NOT NULL DEFAULT 0")
    db.execute("CREATE INDEX IF NOT EXISTS variants_session_idx ON response_variants(chat_id, session_id, user_rowid, created_at)")


def _ensure_generation_tables(db: sqlite3.Connection) -> None:
    """Create generation settings, presets, and summary tables."""
    db.execute("""CREATE TABLE IF NOT EXISTS generation_settings (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        temperature REAL NOT NULL DEFAULT 0.85,
        max_tokens INTEGER NOT NULL DEFAULT 1800,
        top_p REAL NOT NULL DEFAULT 1.0,
        frequency_penalty REAL NOT NULL DEFAULT 0.0,
        presence_penalty REAL NOT NULL DEFAULT 0.0,
        reasoning_budget INTEGER NOT NULL DEFAULT 0,
        stop_sequences TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(chat_id, session_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS generation_presets (
        chat_id TEXT NOT NULL,
        preset_name TEXT NOT NULL,
        settings_json TEXT NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY(chat_id, preset_name)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS session_summaries (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        summary TEXT NOT NULL,
        covered_until_rowid INTEGER NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY(chat_id, session_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS hindsight_documents (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY(chat_id, session_id, document_id)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS hindsight_documents_session_idx ON hindsight_documents(chat_id, session_id)")



def _ensure_rag_tables(db: sqlite3.Connection) -> None:
    """Create Data Bank and embedding tables."""
    db.execute("""CREATE TABLE IF NOT EXISTS data_bank_documents (
        chat_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        byte_size INTEGER NOT NULL,
        chunk_count INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY(chat_id, document_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS data_bank_chunks (
        chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        content TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS data_bank_embeddings (
        chunk_id INTEGER PRIMARY KEY,
        embedding_namespace TEXT NOT NULL DEFAULT 'legacy',
        dimensions INTEGER NOT NULL,
        vector_json TEXT NOT NULL
    )""")
    embedding_columns = {row[1] for row in db.execute("PRAGMA table_info(data_bank_embeddings)").fetchall()}
    if "embedding_namespace" not in embedding_columns:
        db.execute("ALTER TABLE data_bank_embeddings ADD COLUMN embedding_namespace TEXT NOT NULL DEFAULT 'legacy'")
    db.execute("CREATE INDEX IF NOT EXISTS data_bank_chunks_chat_chunk_idx ON data_bank_chunks(chat_id, chunk_id)")
    db.execute("CREATE INDEX IF NOT EXISTS data_bank_embeddings_namespace_chunk_idx ON data_bank_embeddings(embedding_namespace, chunk_id)")
    db.execute("""CREATE TABLE IF NOT EXISTS rag_embedding_cache (
        cache_key TEXT PRIMARY KEY,
        dimensions INTEGER NOT NULL,
        vector_json TEXT NOT NULL,
        created_at REAL NOT NULL
    )""")



def _ensure_job_tables(db: sqlite3.Connection) -> None:
    """Create durable update, failed-turn, and job tables and clean old rows."""
    db.execute("""CREATE TABLE IF NOT EXISTS processed_updates (
        update_id INTEGER PRIMARY KEY,
        processed_at REAL NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS failed_turns (
        chat_id TEXT NOT NULL,
        telegram_message_id TEXT NOT NULL,
        text TEXT NOT NULL,
        model TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT '',
        attempts INTEGER NOT NULL DEFAULT 1,
        last_error TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY(chat_id, telegram_message_id)
    )""")
    failed_columns = {row[1] for row in db.execute("PRAGMA table_info(failed_turns)").fetchall()}
    if "session_id" not in failed_columns:
        db.execute("ALTER TABLE failed_turns ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")
    db.execute("""CREATE TABLE IF NOT EXISTS jobs (
        job_id INTEGER PRIMARY KEY AUTOINCREMENT,
        update_id INTEGER UNIQUE,
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        telegram_message_id TEXT,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'queued',
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(state, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS jobs_chat_idx ON jobs(chat_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS data_bank_chunks_chat_idx ON data_bank_chunks(chat_id, document_id)")
    db.execute("CREATE INDEX IF NOT EXISTS data_bank_embeddings_chunk_idx ON data_bank_embeddings(chunk_id)")
    db.execute("DELETE FROM processed_updates WHERE processed_at < ?", (time.time() - PROCESSED_UPDATE_RETENTION_SECONDS,))
    db.execute("DELETE FROM rag_embedding_cache WHERE created_at < ?", (time.time() - 30 * 86400,))
    db.execute("DELETE FROM jobs WHERE state='done' AND updated_at < ?", (time.time() - 30 * 86400,))
    db.execute("DELETE FROM jobs WHERE state='failed' AND updated_at < ?", (time.time() - 90 * 86400,))
    db.execute("DELETE FROM failed_turns WHERE updated_at < ?", (time.time() - 90 * 86400,))



def _ensure_panel_tables(db: sqlite3.Connection) -> None:
    """Create callback, panel, operation, FTS, and group tables."""
    db.execute("""CREATE TABLE IF NOT EXISTS callback_tokens (
        token TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        value TEXT NOT NULL,
        chat_id TEXT NOT NULL DEFAULT '',
        expires_at REAL NOT NULL
    )""")
    db.execute("DELETE FROM callback_tokens WHERE expires_at < ?", (time.time(),))
    db.execute("""CREATE TABLE IF NOT EXISTS panel_sessions (
        chat_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        owner_user_id TEXT NOT NULL DEFAULT '',
        expires_at REAL NOT NULL,
        PRIMARY KEY(chat_id, message_id)
    )""")
    panel_columns = {row[1] for row in db.execute("PRAGMA table_info(panel_sessions)").fetchall()}
    if "owner_user_id" not in panel_columns:
        db.execute("ALTER TABLE panel_sessions ADD COLUMN owner_user_id TEXT NOT NULL DEFAULT ''")
    db.execute("DELETE FROM panel_sessions WHERE expires_at < ?", (time.time(),))
    db.execute("""CREATE TABLE IF NOT EXISTS operations (
        operation_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'in_progress',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS operations_updated_idx ON operations(updated_at)")
    db.execute("""CREATE TABLE IF NOT EXISTS sync_bindings (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        sync_id TEXT NOT NULL,
        last_hash TEXT NOT NULL DEFAULT '',
        last_direction TEXT NOT NULL DEFAULT '',
        last_synced_at REAL NOT NULL DEFAULT 0,
        conflict TEXT NOT NULL DEFAULT '',
        last_error TEXT NOT NULL DEFAULT '',
        last_checked_at REAL NOT NULL DEFAULT 0,
        realtime_enabled INTEGER NOT NULL DEFAULT 0,
        realtime_failures INTEGER NOT NULL DEFAULT 0,
        realtime_next_retry_at REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(chat_id, session_id),
        UNIQUE(chat_id, sync_id)
    )""")
    sync_columns = {row[1] for row in db.execute("PRAGMA table_info(sync_bindings)").fetchall()}
    for column, definition in {
        "conflict": "TEXT NOT NULL DEFAULT ''",
        "last_error": "TEXT NOT NULL DEFAULT ''",
        "last_checked_at": "REAL NOT NULL DEFAULT 0",
        "realtime_enabled": "INTEGER NOT NULL DEFAULT 0",
        "realtime_failures": "INTEGER NOT NULL DEFAULT 0",
        "realtime_next_retry_at": "REAL NOT NULL DEFAULT 0",
    }.items():
        if column not in sync_columns:
            db.execute(f"ALTER TABLE sync_bindings ADD COLUMN {column} {definition}")
    db.execute("CREATE INDEX IF NOT EXISTS sync_bindings_realtime_idx ON sync_bindings(realtime_enabled, realtime_next_retry_at)")
    db.execute("DELETE FROM operations WHERE updated_at < ?", (time.time() - 90 * 86400,))
    db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS data_bank_fts USING fts5(
        content,
        chat_id UNINDEXED,
        document_id UNINDEXED,
        filename UNINDEXED,
        chunk_id UNINDEXED,
        tokenize='unicode61'
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS group_sessions (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT 'Group chat',
        enabled INTEGER NOT NULL DEFAULT 0,
        turn_index INTEGER NOT NULL DEFAULT 0,
        mode TEXT NOT NULL DEFAULT 'round_robin',
        forced_speaker TEXT NOT NULL DEFAULT '',
        members_json TEXT NOT NULL DEFAULT '[]',
        turn_user_id TEXT NOT NULL DEFAULT '',
        turn_users_json TEXT NOT NULL DEFAULT '[]',
        updated_at REAL NOT NULL,
        PRIMARY KEY(chat_id, session_id)
    )""")
    group_columns = {row[1] for row in db.execute("PRAGMA table_info(group_sessions)").fetchall()}
    if "mode" not in group_columns:
        db.execute("ALTER TABLE group_sessions ADD COLUMN mode TEXT NOT NULL DEFAULT 'round_robin'")
    if "forced_speaker" not in group_columns:
        db.execute("ALTER TABLE group_sessions ADD COLUMN forced_speaker TEXT NOT NULL DEFAULT ''")
    if "turn_user_id" not in group_columns:
        db.execute("ALTER TABLE group_sessions ADD COLUMN turn_user_id TEXT NOT NULL DEFAULT ''")
    if "turn_users_json" not in group_columns:
        db.execute("ALTER TABLE group_sessions ADD COLUMN turn_users_json TEXT NOT NULL DEFAULT '[]'")



def initialize_database_schema(db: sqlite3.Connection) -> None:
    """Create or migrate bridge tables while preserving existing data."""
    _ensure_core_tables(db)
    _ensure_generation_tables(db)
    _ensure_rag_tables(db)
    _ensure_job_tables(db)
    _ensure_panel_tables(db)
    db.commit()

import sqlite3
import time

from bridge.migrations import (
    Migration as _Migration,
    run_migrations as _run_migrations,
)

PROCESSED_UPDATE_RETENTION_SECONDS = 30 * 86400

def _create_core_tables(db: sqlite3.Connection) -> None:
    """Create metadata, messages, sessions, and response variant tables."""
    db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS messages (
        chat_id TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT 'default',
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        telegram_message_id TEXT,
        telegram_message_ids TEXT NOT NULL DEFAULT '[]',
        created_at REAL NOT NULL
    )""")
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
    db.execute("CREATE INDEX IF NOT EXISTS messages_session_idx ON messages(chat_id, session_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS messages_telegram_idx ON messages(chat_id, telegram_message_id)")
    db.execute("CREATE INDEX IF NOT EXISTS messages_session_created_idx ON messages(chat_id, session_id, created_at DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS messages_chat_telegram_session_idx ON messages(chat_id, telegram_message_id, session_id)")
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
    db.execute("CREATE INDEX IF NOT EXISTS variants_session_idx ON response_variants(chat_id, session_id, user_rowid, created_at)")


def _create_generation_tables(db: sqlite3.Connection) -> None:
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
    db.execute("CREATE INDEX IF NOT EXISTS hindsight_documents_chat_idx ON hindsight_documents(chat_id, kind)")



def _create_rag_tables(db: sqlite3.Connection) -> None:
    """Create Data Bank and embedding tables."""
    db.execute("""CREATE TABLE IF NOT EXISTS data_bank_documents (
        chat_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        byte_size INTEGER NOT NULL,
        chunk_count INTEGER NOT NULL,
        version_number INTEGER NOT NULL DEFAULT 1,
        active INTEGER NOT NULL DEFAULT 1,
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
        embedding_namespace TEXT NOT NULL,
        dimensions INTEGER NOT NULL,
        vector_json TEXT NOT NULL,
        vector_signature INTEGER NOT NULL,
        vector_norm REAL NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS data_bank_documents_active_idx ON data_bank_documents(chat_id, active, filename, version_number)")
    db.execute("CREATE INDEX IF NOT EXISTS data_bank_chunks_chat_chunk_idx ON data_bank_chunks(chat_id, chunk_id)")
    db.execute("CREATE INDEX IF NOT EXISTS data_bank_embeddings_namespace_chunk_idx ON data_bank_embeddings(embedding_namespace, chunk_id)")
    db.execute("CREATE INDEX IF NOT EXISTS data_bank_embeddings_namespace_signature_idx ON data_bank_embeddings(embedding_namespace, vector_signature, chunk_id)")
    db.execute("""CREATE TABLE IF NOT EXISTS rag_embedding_cache (
        cache_key TEXT PRIMARY KEY,
        dimensions INTEGER NOT NULL,
        vector_json TEXT NOT NULL,
        vector_norm REAL NOT NULL,
        created_at REAL NOT NULL
    )""")

def _create_job_tables(db: sqlite3.Connection) -> None:
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
    db.execute("CREATE INDEX IF NOT EXISTS jobs_state_chat_idx ON jobs(state, chat_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS jobs_updated_idx ON jobs(updated_at)")
    db.execute("CREATE INDEX IF NOT EXISTS data_bank_chunks_chat_idx ON data_bank_chunks(chat_id, document_id)")
    db.execute("CREATE INDEX IF NOT EXISTS data_bank_embeddings_chunk_idx ON data_bank_embeddings(chunk_id)")



def _create_application_tables(db: sqlite3.Connection) -> None:
    """Create callback, panel, operation, FTS, and group tables."""
    db.execute("""CREATE TABLE IF NOT EXISTS callback_tokens (
        token TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        value TEXT NOT NULL,
        chat_id TEXT NOT NULL DEFAULT '',
        expires_at REAL NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS callback_tokens_expires_idx ON callback_tokens(expires_at)")
    db.execute("""CREATE TABLE IF NOT EXISTS panel_sessions (
        chat_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        owner_user_id TEXT NOT NULL DEFAULT '',
        expires_at REAL NOT NULL,
        PRIMARY KEY(chat_id, message_id)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS panel_sessions_expires_idx ON panel_sessions(expires_at)")
    db.execute("""CREATE TABLE IF NOT EXISTS operations (
        operation_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'in_progress',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS operations_updated_idx ON operations(updated_at)")
    db.execute("CREATE INDEX IF NOT EXISTS operations_state_idx ON operations(state, updated_at)")
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
    db.execute("CREATE INDEX IF NOT EXISTS sync_bindings_realtime_idx ON sync_bindings(realtime_enabled, realtime_next_retry_at)")
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



def _run_startup_database_cleanup(db: sqlite3.Connection) -> None:
    now = time.time()
    db.execute(
        "DELETE FROM processed_updates WHERE processed_at < ?",
        (now - PROCESSED_UPDATE_RETENTION_SECONDS,),
    )
    db.execute(
        "DELETE FROM rag_embedding_cache WHERE created_at < ?",
        (now - 30 * 86400,),
    )
    db.execute(
        "DELETE FROM jobs WHERE state='done' AND updated_at < ?",
        (now - 30 * 86400,),
    )
    db.execute(
        "DELETE FROM jobs WHERE state='failed' AND updated_at < ?",
        (now - 90 * 86400,),
    )
    db.execute(
        "DELETE FROM failed_turns WHERE updated_at < ?",
        (now - 90 * 86400,),
    )
    db.execute(
        "DELETE FROM callback_tokens WHERE expires_at < ?",
        (now,),
    )
    db.execute(
        "DELETE FROM panel_sessions WHERE expires_at < ?",
        (now,),
    )
    db.execute(
        "DELETE FROM operations WHERE updated_at < ?",
        (now - 90 * 86400,),
    )
    db.execute(
        "DELETE FROM sync_bindings "
        "WHERE NOT EXISTS ("
        "SELECT 1 FROM sessions "
        "WHERE sessions.chat_id=sync_bindings.chat_id "
        "AND sessions.session_id=sync_bindings.session_id"
        ")"
    )


def _create_initial_schema(db: sqlite3.Connection) -> None:
    _create_core_tables(db)
    _create_generation_tables(db)
    _create_rag_tables(db)
    _create_job_tables(db)
    _create_application_tables(db)
    db.execute(
        """CREATE TABLE IF NOT EXISTS scene_states (
            chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            state_json TEXT NOT NULL DEFAULT '{}',
            updated_through_rowid INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL,
            PRIMARY KEY(chat_id, session_id)
        )"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS scene_states_updated_idx "
        "ON scene_states(chat_id, session_id, updated_through_rowid)"
    )
    db.execute(
        """CREATE TRIGGER IF NOT EXISTS scene_states_session_delete
        AFTER DELETE ON sessions
        BEGIN
            DELETE FROM scene_states
            WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        END"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS director_goals (
            chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            goal TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(chat_id, session_id)
        )"""
    )
    db.execute(
        """CREATE TRIGGER IF NOT EXISTS director_goals_session_delete
        AFTER DELETE ON sessions
        BEGIN
            DELETE FROM director_goals
            WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        END"""
    )
    db.execute(
        """CREATE TRIGGER IF NOT EXISTS sessions_delete_sync_binding
        AFTER DELETE ON sessions
        FOR EACH ROW
        BEGIN
            DELETE FROM sync_bindings
            WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        END"""
    )


SCHEMA_MIGRATIONS = (
    _Migration(1, "initial_schema", _create_initial_schema),
)


def initialize_database_schema(db: sqlite3.Connection) -> None:
    """Apply structural migrations, then run recurring startup cleanup."""
    _run_migrations(db, SCHEMA_MIGRATIONS)
    _run_startup_database_cleanup(db)
    db.commit()

import sqlite3
import time
import unittest

import bridge.schema as schema
from bridge.migrations import Migration, MigrationError, run_migrations


class MigrationEngineTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")

    def tearDown(self):
        self.db.close()

    def test_applies_missing_suffix_once_and_records_order(self):
        calls = []

        def first(db):
            calls.append("first")
            db.execute("CREATE TABLE first_table(id INTEGER PRIMARY KEY)")

        def second(db):
            calls.append("second")
            db.execute("CREATE TABLE second_table(id INTEGER PRIMARY KEY)")

        migrations = (
            Migration(1, "first", first),
            Migration(2, "second", second),
        )

        run_migrations(self.db, migrations)
        run_migrations(self.db, migrations)

        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(
            self.db.execute(
                "SELECT version,name FROM schema_migrations ORDER BY version"
            ).fetchall(),
            [(1, "first"), (2, "second")],
        )

    def test_failed_migration_rolls_back_and_stops(self):
        calls = []

        def first(db):
            calls.append("first")
            db.execute("CREATE TABLE first_table(id INTEGER PRIMARY KEY)")

        def broken(db):
            calls.append("broken")
            db.execute("CREATE TABLE rolled_back_table(id INTEGER PRIMARY KEY)")
            raise sqlite3.OperationalError("deliberate migration failure")

        def never(db):
            calls.append("never")

        with self.assertRaisesRegex(
            MigrationError,
            r"Migration 002 \(broken\) failed",
        ):
            run_migrations(
                self.db,
                (
                    Migration(1, "first", first),
                    Migration(2, "broken", broken),
                    Migration(3, "never", never),
                ),
            )

        self.assertEqual(calls, ["first", "broken"])
        self.assertEqual(
            self.db.execute(
                "SELECT version,name FROM schema_migrations ORDER BY version"
            ).fetchall(),
            [(1, "first")],
        )
        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='rolled_back_table'"
            ).fetchone()
        )

    def test_history_name_drift_is_rejected_before_apply(self):
        self.db.execute(
            "CREATE TABLE schema_migrations("
            "version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at REAL NOT NULL)"
        )
        self.db.execute(
            "INSERT INTO schema_migrations VALUES(1,'old_name',1.0)"
        )
        self.db.commit()
        called = []

        with self.assertRaisesRegex(MigrationError, "name mismatch"):
            run_migrations(
                self.db,
                (Migration(1, "current_name", lambda db: called.append(True)),),
            )

        self.assertEqual(called, [])

    def test_history_gap_is_rejected_before_apply(self):
        self.db.execute(
            "CREATE TABLE schema_migrations("
            "version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at REAL NOT NULL)"
        )
        self.db.executemany(
            "INSERT INTO schema_migrations VALUES(?,?,?)",
            [(1, "one", 1.0), (3, "three", 3.0)],
        )
        self.db.commit()

        with self.assertRaisesRegex(MigrationError, "valid prefix"):
            run_migrations(
                self.db,
                (
                    Migration(1, "one", lambda db: None),
                    Migration(2, "two", lambda db: None),
                    Migration(3, "three", lambda db: None),
                ),
            )

    def test_unknown_future_version_is_rejected(self):
        self.db.execute(
            "CREATE TABLE schema_migrations("
            "version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at REAL NOT NULL)"
        )
        self.db.executemany(
            "INSERT INTO schema_migrations VALUES(?,?,?)",
            [(1, "one", 1.0), (5, "future", 5.0)],
        )
        self.db.commit()

        with self.assertRaisesRegex(MigrationError, "unknown migration version 005"):
            run_migrations(
                self.db,
                (Migration(1, "one", lambda db: None),),
            )

    def test_invalid_declarations_are_rejected(self):
        cases = (
            (
                (
                    Migration(1, "one", lambda db: None),
                    Migration(1, "duplicate", lambda db: None),
                ),
                "duplicate migration version",
            ),
            (
                (
                    Migration(2, "two", lambda db: None),
                    Migration(1, "one", lambda db: None),
                ),
                "strictly increasing",
            ),
            (
                (Migration(0, "zero", lambda db: None),),
                "positive integer",
            ),
            (
                (Migration(1, "", lambda db: None),),
                "name must not be empty",
            ),
        )
        for migrations, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(MigrationError, message):
                    run_migrations(self.db, migrations)

    def test_active_transaction_is_rejected_without_committing_it(self):
        self.db.execute("CREATE TABLE caller_state(value TEXT)")
        self.db.execute("INSERT INTO caller_state VALUES('uncommitted')")
        self.assertTrue(self.db.in_transaction)

        with self.assertRaisesRegex(MigrationError, "active transaction"):
            run_migrations(
                self.db,
                (Migration(1, "one", lambda db: None),),
            )

        self.db.rollback()
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM caller_state").fetchone()[0],
            0,
        )


class ApplicationSchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")

    def tearDown(self):
        self.db.close()

    def _columns(self, table):
        return {
            row[1]
            for row in self.db.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }

    def _create_representative_legacy_schema(self):
        self.db.executescript(
            """
            CREATE TABLE messages (
                chat_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE sessions (
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                title TEXT NOT NULL,
                character_file TEXT NOT NULL,
                model_id TEXT NOT NULL,
                persona_id TEXT NOT NULL,
                world_file TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(chat_id, session_id)
            );
            CREATE TABLE response_variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                user_content TEXT NOT NULL,
                response TEXT NOT NULL,
                variant_index INTEGER NOT NULL,
                selected INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL
            );
            CREATE TABLE data_bank_documents (
                chat_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(chat_id, document_id)
            );
            CREATE TABLE data_bank_embeddings (
                chunk_id INTEGER PRIMARY KEY,
                dimensions INTEGER NOT NULL,
                vector_json TEXT NOT NULL
            );
            CREATE TABLE rag_embedding_cache (
                cache_key TEXT PRIMARY KEY,
                dimensions INTEGER NOT NULL,
                vector_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE failed_turns (
                chat_id TEXT NOT NULL,
                telegram_message_id TEXT NOT NULL,
                text TEXT NOT NULL,
                model TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 1,
                last_error TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(chat_id, telegram_message_id)
            );
            CREATE TABLE panel_sessions (
                chat_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                expires_at REAL NOT NULL,
                PRIMARY KEY(chat_id, message_id)
            );
            CREATE TABLE sync_bindings (
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                sync_id TEXT NOT NULL,
                last_hash TEXT NOT NULL DEFAULT '',
                last_direction TEXT NOT NULL DEFAULT '',
                last_synced_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(chat_id, session_id),
                UNIQUE(chat_id, sync_id)
            );
            CREATE TABLE group_sessions (
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT 'Group chat',
                enabled INTEGER NOT NULL DEFAULT 0,
                turn_index INTEGER NOT NULL DEFAULT 0,
                members_json TEXT NOT NULL DEFAULT '[]',
                updated_at REAL NOT NULL,
                PRIMARY KEY(chat_id, session_id)
            );
            """
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,role,content,created_at) "
            "VALUES('chat','user','legacy message',1.0)"
        )
        self.db.execute(
            "INSERT INTO sessions("
            "chat_id,session_id,title,character_file,model_id,persona_id,"
            "world_file,created_at,updated_at"
            ") VALUES('chat','legacy','Legacy','','model','','',1.0,1.0)"
        )
        self.db.execute(
            "INSERT INTO response_variants("
            "chat_id,session_id,user_content,response,variant_index,selected,created_at"
            ") VALUES('chat','legacy','u','a',0,1,1.0)"
        )
        self.db.execute(
            "INSERT INTO data_bank_documents("
            "chat_id,document_id,filename,byte_size,chunk_count,created_at,updated_at"
            ") VALUES('chat','doc-1','notes.txt',1,1,1.0,1.0)"
        )
        self.db.execute(
            "INSERT INTO failed_turns("
            "chat_id,telegram_message_id,text,model,attempts,last_error,created_at,updated_at"
            ") VALUES('chat','1','text','model',1,'err',1.0,1.0)"
        )
        self.db.execute(
            "INSERT INTO panel_sessions(chat_id,message_id,session_id,expires_at) "
            "VALUES('chat','1','legacy',9999999999.0)"
        )
        self.db.execute(
            "INSERT INTO sync_bindings(chat_id,session_id,sync_id) "
            "VALUES('chat','legacy','stb-legacy')"
        )
        self.db.execute(
            "INSERT INTO group_sessions("
            "chat_id,session_id,title,enabled,turn_index,members_json,updated_at"
            ") VALUES('chat','legacy','Legacy',1,0,'[]',1.0)"
        )
        self.db.commit()

    def test_core_baseline_bootstraps_empty_database(self):
        schema.initialize_database_schema(self.db)

        self.assertEqual(
            self.db.execute(
                "SELECT version,name FROM schema_migrations ORDER BY version"
            ).fetchall(),
            [(1, "core_baseline")],
        )
        for table in (
            "messages",
            "sessions",
            "generation_settings",
            "data_bank_documents",
            "jobs",
            "panel_sessions",
            "sync_bindings",
            "group_sessions",
        ):
            with self.subTest(table=table):
                self.assertIsNotNone(
                    self.db.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type IN ('table','view') AND name=?",
                        (table,),
                    ).fetchone()
                )

    def test_core_baseline_upgrades_representative_legacy_schema(self):
        self._create_representative_legacy_schema()

        schema.initialize_database_schema(self.db)

        expected_columns = {
            "messages": {
                "session_id",
                "telegram_message_id",
                "telegram_message_ids",
            },
            "sessions": {
                "author_note",
                "system_prompt",
                "response_language",
            },
            "response_variants": {"user_rowid"},
            "data_bank_documents": {"version_number", "active"},
            "data_bank_embeddings": {
                "embedding_namespace",
                "vector_signature",
                "vector_norm",
            },
            "rag_embedding_cache": {"vector_norm"},
            "failed_turns": {"session_id"},
            "panel_sessions": {"owner_user_id"},
            "sync_bindings": {
                "conflict",
                "last_error",
                "last_checked_at",
                "realtime_enabled",
                "realtime_failures",
                "realtime_next_retry_at",
            },
            "group_sessions": {
                "mode",
                "forced_speaker",
                "turn_user_id",
                "turn_users_json",
            },
        }
        for table, columns in expected_columns.items():
            with self.subTest(table=table):
                self.assertTrue(columns <= self._columns(table))

        message = self.db.execute(
            "SELECT content,session_id FROM messages"
        ).fetchone()
        self.assertEqual(message, ("legacy message", "default"))
        self.assertEqual(
            self.db.execute(
                "SELECT title,author_note,response_language FROM sessions "
                "WHERE session_id='legacy'"
            ).fetchone(),
            ("Legacy", "", "auto"),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT filename,version_number,active "
                "FROM data_bank_documents WHERE document_id='doc-1'"
            ).fetchone(),
            ("notes.txt", 1, 1),
        )

    def test_startup_cleanup_runs_when_core_migration_is_already_applied(self):
        schema.initialize_database_schema(self.db)
        old = time.time() - 200 * 86400
        self.db.execute(
            "INSERT INTO processed_updates(update_id,processed_at) VALUES(1,?)",
            (old,),
        )
        self.db.execute(
            "INSERT INTO callback_tokens(token,kind,value,chat_id,expires_at) "
            "VALUES('expired','x','x','chat',?)",
            (time.time() - 10,),
        )
        self.db.commit()

        schema.initialize_database_schema(self.db)

        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM processed_updates WHERE update_id=1"
            ).fetchone()
        )
        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM callback_tokens WHERE token='expired'"
            ).fetchone()
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()

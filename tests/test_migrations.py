from application_test_setup import ensure_application_extensions

ensure_application_extensions()

from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

import bridge.config as config
import bridge.director_goals as _m_director_goals
import bridge.memory_curator as _m_memory_curator
import bridge.scene_state as _m_scene_state
import bridge.session_naming as _m_session_naming
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

    def test_initial_schema_is_single_current_baseline(self):
        self.assertEqual(len(schema.SCHEMA_MIGRATIONS), 1)
        migration = schema.SCHEMA_MIGRATIONS[0]
        self.assertEqual((migration.version, migration.name), (1, "initial_schema"))

    def test_initial_schema_bootstraps_current_database(self):
        schema.initialize_database_schema(self.db)

        self.assertEqual(
            self.db.execute(
                "SELECT version,name FROM schema_migrations ORDER BY version"
            ).fetchall(),
            [(1, "initial_schema")],
        )

        for table in (
            "meta",
            "messages",
            "sessions",
            "response_variants",
            "generation_settings",
            "generation_presets",
            "session_summaries",
            "hindsight_documents",
            "data_bank_documents",
            "data_bank_chunks",
            "data_bank_embeddings",
            "rag_embedding_cache",
            "processed_updates",
            "failed_turns",
            "jobs",
            "callback_tokens",
            "panel_sessions",
            "operations",
            "sync_bindings",
            "data_bank_fts",
            "group_sessions",
            "scene_states",
            "director_goals",
        ):
            with self.subTest(table=table):
                self.assertIsNotNone(
                    self.db.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type IN ('table','view') AND name=?",
                        (table,),
                    ).fetchone()
                )

        for object_type, name in (
            ("index", "scene_states_updated_idx"),
            ("trigger", "scene_states_session_delete"),
            ("trigger", "director_goals_session_delete"),
            ("trigger", "sessions_delete_sync_binding"),
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(
                    self.db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type=? AND name=?",
                        (object_type, name),
                    ).fetchone()
                )

    def test_initial_schema_requires_current_rag_embedding_metadata(self):
        schema.initialize_database_schema(self.db)

        embedding_columns = {
            row[1]: row
            for row in self.db.execute(
                "PRAGMA table_info(data_bank_embeddings)"
            ).fetchall()
        }
        self.assertEqual(embedding_columns["embedding_namespace"][3], 1)
        self.assertIsNone(embedding_columns["embedding_namespace"][4])
        self.assertEqual(embedding_columns["vector_signature"][3], 1)
        self.assertEqual(embedding_columns["vector_norm"][3], 1)

        cache_columns = {
            row[1]: row
            for row in self.db.execute(
                "PRAGMA table_info(rag_embedding_cache)"
            ).fetchall()
        }
        self.assertEqual(cache_columns["vector_norm"][3], 1)

    def test_production_schema_has_no_preproduction_upgrade_paths(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "schema.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("ALTER TABLE", source)
        self.assertNotIn("PRAGMA table_info", source)
        self.assertNotIn("_migration_002_scene_state", source)
        self.assertNotIn("_migration_003_director_goals", source)
        self.assertNotIn("_migration_004_sync_lifecycle_trigger", source)
        self.assertNotIn('"core_baseline"', source)

    def test_preproduction_core_baseline_ledger_is_rejected(self):
        self.db.execute(
            "CREATE TABLE schema_migrations("
            "version INTEGER PRIMARY KEY,"
            "name TEXT NOT NULL,"
            "applied_at REAL NOT NULL)"
        )
        self.db.execute(
            "INSERT INTO schema_migrations VALUES(1,'core_baseline',1.0)"
        )
        self.db.execute(
            "CREATE TABLE sessions("
            "chat_id TEXT NOT NULL,"
            "session_id TEXT NOT NULL,"
            "PRIMARY KEY(chat_id, session_id))"
        )
        self.db.execute(
            "CREATE TABLE sync_bindings("
            "chat_id TEXT NOT NULL,"
            "session_id TEXT NOT NULL)"
        )
        self.db.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
        self.db.execute(
            "INSERT INTO sentinel(value) VALUES('preserve-me')"
        )
        self.db.commit()

        with self.assertRaisesRegex(MigrationError, "name mismatch"):
            run_migrations(self.db, schema.SCHEMA_MIGRATIONS)

        self.assertEqual(
            self.db.execute("SELECT value FROM sentinel").fetchone()[0],
            "preserve-me",
        )
        self.assertEqual(
            self.db.execute(
                "SELECT version,name FROM schema_migrations ORDER BY version"
            ).fetchall(),
            [(1, "core_baseline")],
        )

    def test_startup_cleanup_runs_when_initial_schema_is_already_applied(self):
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


class RequestTimeSchemaRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.session = _m_session_naming.create_session(
            self.db,
            "chat",
            "primary::main",
            session_id="migration-request",
            title="Migration request",
        )

    def tearDown(self):
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def _assert_no_structural_ddl(self, callback):
        traced = []
        self.db.set_trace_callback(traced.append)
        try:
            callback()
        finally:
            self.db.set_trace_callback(None)

        structural = [
            sql for sql in traced
            if sql.lstrip().upper().startswith(
                ("CREATE TABLE", "CREATE INDEX", "CREATE TRIGGER", "ALTER TABLE")
            )
        ]
        self.assertEqual(structural, [], structural)

    def test_scene_and_director_request_paths_issue_no_structural_ddl(self):
        self.db.execute(
            "INSERT OR REPLACE INTO scene_states("
            "chat_id,session_id,state_json,updated_through_rowid,updated_at"
            ") VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "{}", 0, 1.0),
        )
        self.db.commit()

        operations = (
            lambda: _m_scene_state.get_scene_state(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            lambda: _m_scene_state.clear_scene_state(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            lambda: _m_director_goals.get_director_goal(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            lambda: _m_director_goals.set_director_goal(
                self.db,
                "chat",
                self.session["session_id"],
                "Keep tension unresolved.",
            ),
            lambda: _m_director_goals.director_goal_policy(
                self.db,
                "chat",
                self.session,
            ),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                self._assert_no_structural_ddl(operation)


if __name__ == "__main__":
    unittest.main()

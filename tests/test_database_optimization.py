from application_test_setup import ensure_application_extensions

ensure_application_extensions()

import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

import bridge.config as config
import bridge.database as database
import bridge.main as _m_main
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.sync_api as _m_sync_api
class DatabaseOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = database.db_connect()

    def tearDown(self):
        self.db.close()
        config.DB_FILE = self.original_db
        self.tmp.cleanup()

    def test_connection_pragmas_on_primary_and_lightweight_connect(self):
        worker = database._lightweight_db_connect(timeout=15.0)
        for conn, expected_cache_kib in (
            (self.db, database._DB_PRIMARY_CACHE_KIB),
            (worker, database._DB_WORKER_CACHE_KIB),
        ):
            try:
                synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
                temp_store = conn.execute("PRAGMA temp_store").fetchone()[0]
                cache_size = conn.execute("PRAGMA cache_size").fetchone()[0]
                foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
                busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

                self.assertEqual(synchronous, 1)  # NORMAL
                self.assertEqual(temp_store, 2)  # MEMORY
                self.assertEqual(cache_size, -expected_cache_kib)
                self.assertEqual(foreign_keys, 1)
                self.assertGreaterEqual(busy_timeout, 15000)
            finally:
                if conn is worker:
                    conn.close()
        self.assertEqual(self.db.execute("PRAGMA journal_mode").fetchone()[0].casefold(), "wal")
        self.assertLess(database._DB_WORKER_CACHE_KIB, database._DB_PRIMARY_CACHE_KIB)

    def test_lightweight_connect_does_not_negotiate_journal_mode(self):
        # Worker startup must stay connection-local: a brand-new database file
        # touched only by _lightweight_db_connect must not be switched to WAL.
        fresh = Path(self.tmp.name) / "fresh.sqlite3"
        config.DB_FILE = fresh
        try:
            conn = database._lightweight_db_connect(timeout=5.0)
            try:
                journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertEqual(journal_mode.casefold(), "delete")
            finally:
                conn.close()
        finally:
            config.DB_FILE = self.original_db

    def test_auto_vacuum_incremental_initialized(self):
        auto_vacuum = self.db.execute("PRAGMA auto_vacuum").fetchone()[0]
        self.assertEqual(auto_vacuum, 2)  # INCREMENTAL

    def test_optimize_database_never_vacuums(self):
        # optimize_database() runs on request cleanup paths; it must only refresh
        # planner statistics and leave page reclamation to dedicated maintenance.
        self.db.execute("CREATE TABLE IF NOT EXISTS test_churn (id INT, payload TEXT)")
        for i in range(200):
            self.db.execute("INSERT INTO test_churn VALUES (?, ?)", (i, "x" * 2000))
        self.db.commit()
        self.db.execute("DELETE FROM test_churn")
        self.db.commit()
        self.assertGreater(self.db.execute("PRAGMA freelist_count").fetchone()[0], 0)

        isolation_before = self.db.isolation_level
        traced: list[str] = []
        self.db.set_trace_callback(traced.append)
        try:
            _m_message_commands.optimize_database(self.db)
        finally:
            self.db.set_trace_callback(None)

        self.assertEqual(self.db.isolation_level, isolation_before)
        self.assertTrue(any("PRAGMA optimize" in statement for statement in traced))
        self.assertFalse(any("vacuum" in statement.casefold() for statement in traced))
        # A full VACUUM would have reclaimed every free page.
        self.assertGreater(self.db.execute("PRAGMA freelist_count").fetchone()[0], 0)

    def test_run_database_maintenance_reclaims_freelist(self):
        self.db.execute("CREATE TABLE IF NOT EXISTS test_churn (id INT, payload TEXT)")
        for i in range(200):
            self.db.execute("INSERT INTO test_churn VALUES (?, ?)", (i, "x" * 2000))
        self.db.commit()
        self.db.execute("DELETE FROM test_churn")
        self.db.commit()

        reclaimed = _m_main.run_database_maintenance(vacuum_freelist_threshold=1)
        self.assertTrue(reclaimed)

        conn = database._lightweight_db_connect(timeout=5.0)
        try:
            freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
            self.assertEqual(freelist, 0)
        finally:
            conn.close()

    def test_run_database_maintenance_skips_small_freelist(self):
        self.db.execute("CREATE TABLE IF NOT EXISTS test_churn (id INT, payload TEXT)")
        for i in range(60):
            self.db.execute("INSERT INTO test_churn VALUES (?, ?)", (i, "x" * 2000))
        self.db.commit()
        self.db.execute("DELETE FROM test_churn")
        self.db.commit()
        self.assertGreater(self.db.execute("PRAGMA freelist_count").fetchone()[0], 0)

        reclaimed = _m_main.run_database_maintenance(vacuum_freelist_threshold=500)

        self.assertFalse(reclaimed)
        # A full VACUUM would have reclaimed every free page.
        self.assertGreater(self.db.execute("PRAGMA freelist_count").fetchone()[0], 0)

    def test_phase3_worker_reuses_connection_between_polls(self):
        class FakeStopEvent:
            def __init__(self):
                self.calls = 0

            def wait(self, _timeout):
                self.calls += 1
                return self.calls >= 3

        class FakeDb:
            def __init__(self):
                self.in_transaction = False
                self.closed = False

            def close(self):
                self.closed = True

        original_event = _m_sync_api._PHASE3_STOP_EVENT
        original_connect = _m_sync_api.db_connect
        stop_event = FakeStopEvent()
        connections = []
        polls = []

        class FakeSync:
            def poll(self, db):
                polls.append(db)

        def connect():
            connection = FakeDb()
            connections.append(connection)
            return connection

        _m_sync_api._PHASE3_STOP_EVENT = stop_event
        _m_sync_api.db_connect = connect
        try:
            _m_sync_api._phase3_worker_loop(FakeSync())
        finally:
            _m_sync_api._PHASE3_STOP_EVENT = original_event
            _m_sync_api.db_connect = original_connect

        self.assertEqual(len(connections), 1)
        self.assertEqual(polls, [connections[0], connections[0]])
        self.assertTrue(connections[0].closed)

    def test_load_optional_vector_extension_safe(self):
        loaded = database._load_optional_vector_extension(self.db)
        self.assertIsInstance(loaded, bool)

    def test_vector_extension_failure_disables_extension_loading(self):
        if not hasattr(sqlite3.Connection, "enable_load_extension"):
            self.skipTest("sqlite3 build lacks extension loading")
        fake = types.ModuleType("sqlite_vec")

        def _broken_load(db):
            raise RuntimeError("broken sqlite_vec build")

        fake.load = _broken_load
        original = sys.modules.get("sqlite_vec")
        sys.modules["sqlite_vec"] = fake
        try:
            loaded = database._load_optional_vector_extension(self.db)
            self.assertFalse(loaded)
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                self.db.execute("SELECT load_extension('no_such_extension')")
            self.assertIn("not authorized", str(ctx.exception).casefold())
        finally:
            if original is not None:
                sys.modules["sqlite_vec"] = original
            else:
                sys.modules.pop("sqlite_vec", None)

    def test_db_connect_runs_database_wide_schema_setup_once_per_process(self):
        self.assertIn(
            Path(config.DB_FILE).expanduser().resolve(),
            database._DB_CONNECTION_GATE._ready_paths,
        )

        traced = []
        second = database._lightweight_db_connect(timeout=5.0)
        second.set_trace_callback(traced.append)
        try:
            self.assertEqual(
                second.execute(
                    "SELECT COUNT(*) FROM schema_migrations"
                ).fetchone()[0],
                4,
            )
        finally:
            second.set_trace_callback(None)
            second.close()

        self.assertFalse(
            any(
                sql.lstrip().upper().startswith(
                    ("CREATE TABLE", "CREATE INDEX", "CREATE TRIGGER", "ALTER TABLE")
                )
                for sql in traced
            )
        )



if __name__ == "__main__":
    unittest.main()

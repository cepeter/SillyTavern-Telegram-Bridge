import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

import bridge.runtime as rt


class DatabaseOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = rt.DB_FILE
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()

    def tearDown(self):
        self.db.close()
        rt.DB_FILE = self.original_db
        self.tmp.cleanup()

    def test_connection_pragmas_on_primary_and_lightweight_connect(self):
        for conn in (self.db, rt._lightweight_db_connect(timeout=15.0)):
            try:
                synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
                temp_store = conn.execute("PRAGMA temp_store").fetchone()[0]
                cache_size = conn.execute("PRAGMA cache_size").fetchone()[0]
                foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
                busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

                self.assertEqual(synchronous, 1)  # NORMAL
                self.assertEqual(temp_store, 2)  # MEMORY
                self.assertEqual(cache_size, -64000)  # 64MB
                self.assertEqual(foreign_keys, 1)
                self.assertGreaterEqual(busy_timeout, 15000)
            finally:
                if conn is not self.db:
                    conn.close()
        self.assertEqual(self.db.execute("PRAGMA journal_mode").fetchone()[0].casefold(), "wal")

    def test_lightweight_connect_does_not_negotiate_journal_mode(self):
        # Worker startup must stay connection-local: a brand-new database file
        # touched only by _lightweight_db_connect must not be switched to WAL.
        fresh = Path(self.tmp.name) / "fresh.sqlite3"
        rt.DB_FILE = fresh
        try:
            conn = rt._lightweight_db_connect(timeout=5.0)
            try:
                journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertEqual(journal_mode.casefold(), "delete")
            finally:
                conn.close()
        finally:
            rt.DB_FILE = self.original_db

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
            rt.optimize_database(self.db)
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

        reclaimed = rt.run_database_maintenance(vacuum_freelist_threshold=1)
        self.assertTrue(reclaimed)

        conn = rt._lightweight_db_connect(timeout=5.0)
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

        reclaimed = rt.run_database_maintenance(vacuum_freelist_threshold=500)

        self.assertFalse(reclaimed)
        # A full VACUUM would have reclaimed every free page.
        self.assertGreater(self.db.execute("PRAGMA freelist_count").fetchone()[0], 0)

    def test_load_optional_vector_extension_safe(self):
        loaded = rt._load_optional_vector_extension(self.db)
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
            loaded = rt._load_optional_vector_extension(self.db)
            self.assertFalse(loaded)
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                self.db.execute("SELECT load_extension('no_such_extension')")
            self.assertIn("not authorized", str(ctx.exception).casefold())
        finally:
            if original is not None:
                sys.modules["sqlite_vec"] = original
            else:
                sys.modules.pop("sqlite_vec", None)


if __name__ == "__main__":
    unittest.main()

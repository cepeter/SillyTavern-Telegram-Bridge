import tempfile
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
                journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

                self.assertEqual(synchronous, 1)  # NORMAL
                self.assertEqual(temp_store, 2)  # MEMORY
                self.assertEqual(cache_size, -64000)  # 64MB
                self.assertEqual(journal_mode.casefold(), "wal")
                self.assertGreaterEqual(busy_timeout, 15000)
            finally:
                if conn is not self.db:
                    conn.close()

    def test_auto_vacuum_incremental_initialized(self):
        auto_vacuum = self.db.execute("PRAGMA auto_vacuum").fetchone()[0]
        self.assertEqual(auto_vacuum, 2)  # INCREMENTAL

    def test_optimize_database_runs_cleanly(self):
        result = rt.optimize_database(self.db)
        self.assertIsInstance(result, bool)

    def test_optimize_database_reclaims_freelist(self):
        # Insert enough rows to create pages, then delete them
        self.db.execute("CREATE TABLE IF NOT EXISTS test_churn (id INT, payload TEXT)")
        for i in range(200):
            self.db.execute("INSERT INTO test_churn VALUES (?, ?)", (i, "x" * 2000))
        self.db.commit()

        self.db.execute("DELETE FROM test_churn")
        self.db.commit()

        freelist_before = self.db.execute("PRAGMA freelist_count").fetchone()[0]
        self.assertGreater(freelist_before, 0)

        reclaimed = rt.optimize_database(self.db, vacuum_freelist_threshold=1)
        self.assertTrue(reclaimed)

        freelist_after = self.db.execute("PRAGMA freelist_count").fetchone()[0]
        self.assertEqual(freelist_after, 0)

    def test_load_optional_vector_extension_safe(self):
        loaded = rt._load_optional_vector_extension(self.db)
        self.assertIsInstance(loaded, bool)


if __name__ == "__main__":
    unittest.main()

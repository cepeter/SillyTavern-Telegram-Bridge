import sqlite3
import unittest

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


if __name__ == "__main__":
    unittest.main()

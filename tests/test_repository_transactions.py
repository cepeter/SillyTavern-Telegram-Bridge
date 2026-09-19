import sqlite3
import tempfile
import unittest
from pathlib import Path

import bridge.runtime as rt


class WriteTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "tx.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.db.execute("CREATE TABLE values_table(value TEXT)")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_owned_transaction_commits(self):
        with rt.write_transaction(self.db):
            self.db.execute("INSERT INTO values_table VALUES('committed')")

        observer = sqlite3.connect(self.path)
        try:
            self.assertEqual(
                observer.execute("SELECT value FROM values_table").fetchall(),
                [("committed",)],
            )
        finally:
            observer.close()

    def test_owned_transaction_rolls_back_on_exception(self):
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with rt.write_transaction(self.db):
                self.db.execute("INSERT INTO values_table VALUES('rolled-back')")
                raise RuntimeError("boom")

        self.assertEqual(
            self.db.execute("SELECT value FROM values_table").fetchall(),
            [],
        )

    def test_nested_scope_does_not_commit_outer_transaction(self):
        self.db.execute("BEGIN")
        with rt.write_transaction(self.db):
            self.db.execute("INSERT INTO values_table VALUES('pending')")

        self.assertTrue(self.db.in_transaction)
        observer = sqlite3.connect(self.path)
        try:
            self.assertEqual(
                observer.execute("SELECT value FROM values_table").fetchall(),
                [],
            )
        finally:
            observer.close()

        self.db.rollback()
        self.assertEqual(
            self.db.execute("SELECT value FROM values_table").fetchall(),
            [],
        )

    def test_nested_scope_does_not_rollback_outer_transaction(self):
        self.db.execute("BEGIN")
        self.db.execute("INSERT INTO values_table VALUES('outer')")

        with self.assertRaisesRegex(RuntimeError, "nested"):
            with rt.write_transaction(self.db):
                self.db.execute("INSERT INTO values_table VALUES('inner')")
                raise RuntimeError("nested")

        self.assertTrue(self.db.in_transaction)
        self.assertEqual(
            self.db.execute(
                "SELECT value FROM values_table ORDER BY rowid"
            ).fetchall(),
            [("outer",), ("inner",)],
        )
        self.db.rollback()


if __name__ == "__main__":
    unittest.main()

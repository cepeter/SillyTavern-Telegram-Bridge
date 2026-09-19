import inspect
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import bridge.runtime as rt
from bridge import repositories


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


class RepositorySourceInvariantTests(unittest.TestCase):
    def test_repository_module_contains_no_transaction_ownership_calls(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "repositories.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            ".commit(",
            ".rollback(",
            "run_write_txn(",
            "write_transaction(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class RepositoryPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(
            """
            CREATE TABLE director_goals(
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(chat_id, session_id)
            );
            CREATE TABLE scene_states(
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                state_json TEXT NOT NULL DEFAULT '{}',
                updated_through_rowid INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY(chat_id, session_id)
            );
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE group_sessions(
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
            );
            CREATE TABLE operations(
                operation_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_repository_reads_do_not_open_transaction(self):
        traced = []
        self.db.set_trace_callback(traced.append)
        try:
            self.assertEqual(
                repositories.load_director_goal(self.db, "chat", "session"),
                "",
            )
            self.assertIsNone(
                repositories.load_scene_state_row(self.db, "chat", "session")
            )
            self.assertEqual(
                repositories.load_meta_value(self.db, "missing", "fallback"),
                "fallback",
            )
            self.assertIsNone(
                repositories.load_group_state_row(self.db, "chat", "session")
            )
        finally:
            self.db.set_trace_callback(None)

        self.assertFalse(self.db.in_transaction)
        forbidden = (
            "INSERT", "UPDATE", "DELETE", "REPLACE",
            "CREATE", "ALTER", "DROP",
        )
        self.assertFalse(
            any(sql.lstrip().upper().startswith(forbidden) for sql in traced),
            traced,
        )

    def test_repository_write_does_not_commit(self):
        repositories.store_director_goal(
            self.db, "chat", "session", "goal", 1.0
        )
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(
            repositories.load_director_goal(self.db, "chat", "session"),
            "",
        )

    def test_scene_state_upsert_rejects_stale_candidate(self):
        repositories.upsert_scene_state_if_fresh(
            self.db, "chat", "session", '{"v":10}', 10, 1.0
        )
        self.db.commit()

        accepted = repositories.upsert_scene_state_if_fresh(
            self.db, "chat", "session", '{"v":9}', 9, 2.0
        )
        self.assertFalse(accepted)
        row = repositories.load_scene_state_row(self.db, "chat", "session")
        self.assertEqual(row, ('{"v":10}', 10))

    def test_group_operation_claim_allows_retry_but_rejects_applied(self):
        self.assertTrue(
            repositories.try_claim_group_operation(
                self.db, "op-1", "group_state", 1.0
            )
        )
        self.assertTrue(
            repositories.try_claim_group_operation(
                self.db, "op-1", "group_state", 2.0
            )
        )
        repositories.mark_group_operation_applied(
            self.db, "op-1", "group_state", 3.0
        )
        self.assertFalse(
            repositories.try_claim_group_operation(
                self.db, "op-1", "group_state", 4.0
            )
        )


class GenerationSettingsTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = rt.DB_FILE
        rt.DB_FILE = Path(self.tmp.name) / "settings.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()

    def tearDown(self):
        self.db.close()
        rt.DB_FILE = self.old_db
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.tmp.cleanup()

    def test_get_generation_settings_returns_defaults_without_inserting(self):
        chat_id = "settings-pure"
        session_id = "missing-row"

        traced = []
        self.db.set_trace_callback(traced.append)
        try:
            settings = rt.get_generation_settings(
                self.db,
                chat_id,
                session_id,
            )
        finally:
            self.db.set_trace_callback(None)

        self.assertEqual(settings, dict(rt.GENERATION_DEFAULTS))
        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM generation_settings "
                "WHERE chat_id=? AND session_id=?",
                (chat_id, session_id),
            ).fetchone()
        )
        self.assertFalse(self.db.in_transaction)
        self.assertFalse(
            any(
                sql.lstrip().upper().startswith(
                    ("INSERT", "UPDATE", "DELETE", "REPLACE")
                )
                for sql in traced
            ),
            traced,
        )

    def test_update_generation_settings_materializes_row_explicitly(self):
        updated = rt.update_generation_settings(
            self.db,
            "settings-write",
            "session",
            temperature=0.25,
        )
        self.assertEqual(updated["temperature"], 0.25)
        row = self.db.execute(
            "SELECT temperature FROM generation_settings "
            "WHERE chat_id=? AND session_id=?",
            ("settings-write", "session"),
        ).fetchone()
        self.assertEqual(row, (0.25,))


class GroupTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = rt.DB_FILE
        rt.DB_FILE = Path(self.tmp.name) / "group.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.chat_id = "group-chat"
        self.session_id = "group-session"
        self.initial = {
            "title": "Original",
            "enabled": True,
            "turn_index": 0,
            "mode": "round_robin",
            "forced_speaker": "",
            "members": ["one.png", "two.png"],
            "turn_user_id": "",
            "turn_users": [],
        }
        rt.save_group_state(
            self.db,
            self.chat_id,
            self.session_id,
            self.initial,
        )

    def tearDown(self):
        self.db.close()
        rt.DB_FILE = self.old_db
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.tmp.cleanup()

    def test_group_persistence_helpers_no_longer_expose_commit_flag(self):
        self.assertNotIn(
            "commit",
            inspect.signature(rt.save_group_state).parameters,
        )
        self.assertNotIn(
            "commit",
            inspect.signature(rt.advance_group_turn).parameters,
        )

    def test_save_group_state_joins_outer_transaction(self):
        changed = dict(self.initial)
        changed["title"] = "Pending"

        self.db.execute("BEGIN")
        rt.save_group_state(
            self.db,
            self.chat_id,
            self.session_id,
            changed,
        )
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()

        self.assertEqual(
            rt.group_state(
                self.db,
                self.chat_id,
                self.session_id,
            )["title"],
            "Original",
        )

    def test_group_state_and_operation_claim_roll_back_together(self):
        changed = dict(self.initial)
        changed["title"] = "Must rollback"

        with patch.object(
            rt,
            "_repo_mark_group_operation_applied",
            side_effect=RuntimeError("marker failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "marker failed"):
                rt.save_group_state(
                    self.db,
                    self.chat_id,
                    self.session_id,
                    changed,
                    operation_id="op-atomic",
                )

        self.assertEqual(
            rt.group_state(
                self.db,
                self.chat_id,
                self.session_id,
            )["title"],
            "Original",
        )
        self.assertIsNone(
            self.db.execute(
                "SELECT state FROM operations WHERE operation_id=?",
                ("op-atomic",),
            ).fetchone()
        )

    def test_group_state_and_operation_marker_commit_together(self):
        changed = dict(self.initial)
        changed["title"] = "Committed"

        self.assertTrue(
            rt.save_group_state(
                self.db,
                self.chat_id,
                self.session_id,
                changed,
                operation_id="op-success",
            )
        )

        self.assertEqual(
            rt.group_state(
                self.db,
                self.chat_id,
                self.session_id,
            )["title"],
            "Committed",
        )
        self.assertEqual(
            self.db.execute(
                "SELECT state FROM operations WHERE operation_id=?",
                ("op-success",),
            ).fetchone(),
            ("applied",),
        )


if __name__ == "__main__":
    unittest.main()

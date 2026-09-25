from application_test_setup import (
    ensure_application_extensions,
    make_test_application_services,
    make_test_group_service,
    make_test_memory_service,
    make_test_persona_service,
    make_test_provider_port,
    make_test_rag_service,
)
from settings_test_support import SettingsTestCase

import bridge.director_goal_repository as _owner_director_goal_repository
import bridge.group_repository as _owner_group_repository
import bridge.image_messages as _owner_image_messages
import bridge.meta_repository as _owner_meta_repository
import bridge.operation_repository as _owner_operation_repository
import bridge.reference_repository as _owner_reference_repository
import bridge.scene_repository as _owner_scene_repository
from bridge.rag_service import RagService

ensure_application_extensions()

import inspect
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge.group_core as _m_group_core
import bridge.group_core as group_core
import bridge.memory as _m_memory
import bridge.memory_backend as _m_memory_backend
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.session_naming as _m_session_naming
import bridge.sync_api as _m_sync_api
import bridge.sync_core as _m_sync_core


class WriteTransactionTests(SettingsTestCase):
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
        with _m_memory_curator.write_transaction(self.db):
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
            with _m_memory_curator.write_transaction(self.db):
                self.db.execute("INSERT INTO values_table VALUES('rolled-back')")
                raise RuntimeError("boom")

        self.assertEqual(
            self.db.execute("SELECT value FROM values_table").fetchall(),
            [],
        )

    def test_nested_scope_does_not_commit_outer_transaction(self):
        self.db.execute("BEGIN")
        with _m_memory_curator.write_transaction(self.db):
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
            with _m_memory_curator.write_transaction(self.db):
                self.db.execute("INSERT INTO values_table VALUES('inner')")
                raise RuntimeError("nested")

        self.assertTrue(self.db.in_transaction)
        self.assertEqual(
            self.db.execute("SELECT value FROM values_table ORDER BY rowid").fetchall(),
            [("outer",), ("inner",)],
        )
        self.db.rollback()


class RepositorySourceInvariantTests(SettingsTestCase):
    def test_repository_module_contains_no_transaction_ownership_calls(self):
        repositories = (Path(__file__).parents[1] / "bridge").glob("*_repository.py")
        for path in repositories:
            source = path.read_text(encoding="utf-8")
            for forbidden in (".commit(", ".rollback(", "write_transaction("):
                with self.subTest(owner=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)


class RepositoryPrimitiveTests(SettingsTestCase):
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
            CREATE TABLE sessions(
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                persona_id TEXT NOT NULL,
                PRIMARY KEY(chat_id, session_id)
            );
            CREATE TABLE messages(
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
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
                _owner_director_goal_repository.load_director_goal(self.db, "chat", "session"),
                "",
            )
            self.assertIsNone(_owner_scene_repository.load_scene_state_row(self.db, "chat", "session"))
            self.assertEqual(
                _owner_meta_repository.load_meta_value(self.db, "missing", "fallback"),
                "fallback",
            )
            self.assertIsNone(_owner_group_repository.load_group_state_row(self.db, "chat", "session"))
        finally:
            self.db.set_trace_callback(None)

        self.assertFalse(self.db.in_transaction)
        forbidden = (
            "INSERT",
            "UPDATE",
            "DELETE",
            "REPLACE",
            "CREATE",
            "ALTER",
            "DROP",
        )
        self.assertFalse(
            any(sql.lstrip().upper().startswith(forbidden) for sql in traced),
            traced,
        )

    def test_repository_write_does_not_commit(self):
        self.db.execute("BEGIN")
        _owner_director_goal_repository.store_director_goal(self.db, "chat", "session", "goal", 1.0)
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(
            _owner_director_goal_repository.load_director_goal(self.db, "chat", "session"),
            "",
        )

    def test_scene_state_upsert_rejects_stale_candidate(self):
        self.db.execute("BEGIN")
        _owner_scene_repository.upsert_scene_state_if_fresh(self.db, "chat", "session", '{"v":10}', 10, 1.0)
        self.db.commit()

        self.db.execute("BEGIN")
        accepted = _owner_scene_repository.upsert_scene_state_if_fresh(self.db, "chat", "session", '{"v":9}', 9, 2.0)
        self.assertFalse(accepted)
        row = _owner_scene_repository.load_scene_state_row(self.db, "chat", "session")
        self.assertEqual(row, ('{"v":10}', 10))

    def test_count_persona_references_is_read_only(self):
        self.db.execute(
            "INSERT INTO sessions(chat_id,session_id,persona_id) VALUES(?,?,?)",
            ("chat", "one", "native.png"),
        )
        self.db.commit()
        traced = []
        self.db.set_trace_callback(traced.append)
        try:
            self.assertEqual(
                _owner_reference_repository.count_persona_references(
                    self.db,
                    "native.png",
                ),
                1,
            )
        finally:
            self.db.set_trace_callback(None)
        self.assertFalse(self.db.in_transaction)
        self.assertFalse(
            any(
                sql.lstrip()
                .upper()
                .startswith(
                    (
                        "INSERT",
                        "UPDATE",
                        "DELETE",
                        "REPLACE",
                        "CREATE",
                        "ALTER",
                        "DROP",
                    )
                )
                for sql in traced
            ),
            traced,
        )

    def test_count_session_messages_is_read_only(self):
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", "session", "user", "one", 1.0),
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", "session", "assistant", "two", 2.0),
        )
        self.db.commit()

        traced = []
        self.db.set_trace_callback(traced.append)
        try:
            self.assertEqual(
                _owner_reference_repository.count_session_messages(
                    self.db,
                    "chat",
                    "session",
                ),
                2,
            )
        finally:
            self.db.set_trace_callback(None)

        self.assertFalse(self.db.in_transaction)
        self.assertFalse(
            any(
                sql.lstrip()
                .upper()
                .startswith(
                    (
                        "INSERT",
                        "UPDATE",
                        "DELETE",
                        "REPLACE",
                        "CREATE",
                        "ALTER",
                        "DROP",
                    )
                )
                for sql in traced
            ),
            traced,
        )

    def test_group_operation_claim_allows_retry_but_rejects_applied(self):
        self.db.execute("BEGIN")
        self.assertTrue(_owner_operation_repository.claim_operation(self.db, "op-1", "group_state", 1.0))
        self.assertTrue(_owner_operation_repository.claim_operation(self.db, "op-1", "group_state", 2.0))
        _owner_operation_repository.mark_operation_applied(self.db, "op-1", "group_state", 3.0)
        self.assertFalse(_owner_operation_repository.claim_operation(self.db, "op-1", "group_state", 4.0))


class GenerationSettingsTransactionTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "settings.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())

    def tearDown(self):
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def test_get_generation_settings_returns_defaults_without_inserting(self):
        chat_id = "settings-pure"
        session_id = "missing-row"

        traced = []
        self.db.set_trace_callback(traced.append)
        try:
            settings = _m_memory_curator.get_generation_settings(
                self.db,
                chat_id,
                session_id,
            )
        finally:
            self.db.set_trace_callback(None)

        self.assertEqual(settings, dict(_m_sync_core.GENERATION_DEFAULTS))
        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM generation_settings WHERE chat_id=? AND session_id=?",
                (chat_id, session_id),
            ).fetchone()
        )
        self.assertFalse(self.db.in_transaction)
        self.assertFalse(
            any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) for sql in traced),
            traced,
        )

    def test_update_generation_settings_materializes_row_explicitly(self):
        updated = _m_sync_core.update_generation_settings(
            self.db,
            "settings-write",
            "session",
            temperature=0.25,
        )
        self.assertEqual(updated["temperature"], 0.25)
        row = self.db.execute(
            "SELECT temperature FROM generation_settings WHERE chat_id=? AND session_id=?",
            ("settings-write", "session"),
        ).fetchone()
        self.assertEqual(row, (0.25,))


class GroupTransactionTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "group.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
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
        _m_group_core.save_group_state(
            self.db,
            self.chat_id,
            self.session_id,
            self.initial,
        )

    def tearDown(self):
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def test_group_persistence_helpers_no_longer_expose_commit_flag(self):
        self.assertNotIn(
            "commit",
            inspect.signature(group_core.save_group_state).parameters,
        )
        self.assertNotIn(
            "commit",
            inspect.signature(
                make_test_group_service(app_settings=self.app_settings_builder.build()).advance_turn
            ).parameters,
        )

    def test_save_group_state_joins_outer_transaction(self):
        changed = dict(self.initial)
        changed["title"] = "Pending"

        self.db.execute("BEGIN")
        _m_group_core.save_group_state(
            self.db,
            self.chat_id,
            self.session_id,
            changed,
        )
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()

        self.assertEqual(
            _m_sync_api.group_state(
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
            group_core,
            "_repo_mark_operation_applied",
            side_effect=RuntimeError("marker failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "marker failed"):
                _m_group_core.save_group_state(
                    self.db,
                    self.chat_id,
                    self.session_id,
                    changed,
                    operation_id="op-atomic",
                )

        self.assertEqual(
            _m_sync_api.group_state(
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

    def _assert_group_reply_transaction_committed(self):
        self.assertFalse(self.db.in_transaction)
        observer = sqlite3.connect(self.app_settings_builder.db_file)
        try:
            self.assertEqual(
                observer.execute(
                    "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?",
                    (self.chat_id, self.session_id),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                observer.execute(
                    "SELECT COUNT(*) FROM response_variants WHERE chat_id=? AND session_id=?",
                    (self.chat_id, self.session_id),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                observer.execute(
                    "SELECT turn_index FROM group_sessions WHERE chat_id=? AND session_id=?",
                    (self.chat_id, self.session_id),
                ).fetchone(),
                (1,),
            )
        finally:
            observer.close()

    def test_group_text_reply_use_case_commits_compound_transaction(self):
        _m_session_naming.set_meta(
            self.db,
            f"stream_mode:{self.chat_id}",
            "off",
        )
        group_turn = (
            "one.png",
            _m_sync_api.group_state(self.db, self.chat_id, self.session_id),
        )
        session = {
            "session_id": self.session_id,
            "response_language": "auto",
        }
        fields = {"name": "One"}

        with (
            patch.object(RagService, "bundle", return_value={}),
            patch.object(_m_message_commands, "build_chat_messages", return_value=[]),
            patch.object(_m_memory_backend, "recall_memory_context", return_value=""),
            patch.object(_m_memory, "session_summary_for_prompt", return_value=""),
            patch.object(RagService, "context_for_prompt", return_value=""),
            patch.object(_m_message_commands, "send_typing", return_value=None),
            patch.object(RagService, "citation_footer", return_value=""),
            patch.object(_m_message_commands, "render_response_language", return_value="Reply"),
            patch.object(_m_memory, "retain_session_memory", return_value=None),
            patch.object(_m_message_commands, "queue_user_quote_tts", return_value=None),
            patch.object(_m_message_commands, "send_reply", return_value=None),
        ):
            _m_message_commands.generate_and_store_reply(
                self.db,
                "token",
                "key",
                fields,
                self.chat_id,
                "hello",
                session,
                self.session_id,
                "primary::main",
                group_turn,
                "",
                None,
                None,
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                provider_port=make_test_provider_port(generate_backend=lambda *_args, **_kwargs: "Reply"),
                memory_service=make_test_memory_service(),
                persona_service=make_test_persona_service(),
                app_settings=self.app_settings_builder.build(),
                rag_service=make_test_rag_service(),
            )

        self._assert_group_reply_transaction_committed()

    def test_group_image_reply_use_case_commits_compound_transaction(self):
        group_turn = (
            "one.png",
            _m_sync_api.group_state(self.db, self.chat_id, self.session_id),
        )
        session = {
            "session_id": self.session_id,
            "model_id": "primary::main",
        }

        with (
            patch.object(_m_group_core, "group_current_speaker", return_value=group_turn),
            patch.object(_owner_image_messages, "card_fields_from_file", return_value={"name": "One"}),
            patch.object(RagService, "bundle", return_value={}),
            patch.object(_owner_image_messages, "build_chat_messages", return_value=[]),
            patch.object(RagService, "context_for_prompt", return_value=""),
            patch.object(_owner_image_messages, "send_typing", return_value=None),
            patch.object(RagService, "citation_footer", return_value=""),
            patch.object(_owner_image_messages, "render_session_response", return_value="Reply"),
            patch.object(_owner_image_messages, "send_reply", return_value=None),
        ):
            _owner_image_messages.process_image_message(
                self.db,
                "token",
                "key",
                session,
                {"name": "One"},
                self.chat_id,
                "caption",
                b"image",
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                provider_port=make_test_provider_port(generate_backend=lambda *_args, **_kwargs: "Reply"),
                memory_service=make_test_memory_service(),
                persona_service=make_test_persona_service(),
                group_director_service=make_test_application_services(
                    app_settings=self.app_settings_builder.build()
                ).group_director,
                app_settings=self.app_settings_builder.build(),
                rag_service=make_test_rag_service(),
            )

        self._assert_group_reply_transaction_committed()

    def test_group_state_and_operation_marker_commit_together(self):
        changed = dict(self.initial)
        changed["title"] = "Committed"

        self.assertTrue(
            _m_group_core.save_group_state(
                self.db,
                self.chat_id,
                self.session_id,
                changed,
                operation_id="op-success",
            )
        )

        self.assertEqual(
            _m_sync_api.group_state(
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

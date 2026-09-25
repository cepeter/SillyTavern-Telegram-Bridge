from application_test_setup import ensure_application_extensions, make_test_memory_service, make_test_request_context
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import bridge.cards as _m_cards
import bridge.memory as _m_memory
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.session_naming as _m_session_naming
import bridge.telegram as _m_telegram


class SessionDeletionTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.original_purge = _m_memory.purge_hindsight_session
        self.purged = []
        _m_memory.purge_hindsight_session = lambda _db, chat_id, session_id, *, app_settings=None: (
            self.purged.append((chat_id, session_id)) or 0
        )

    def tearDown(self):
        _m_memory.purge_hindsight_session = self.original_purge
        self.db.close()
        self.tmp.cleanup()

    def test_inactive_session_deletes_all_local_data(self):
        active = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        inactive = _m_session_naming.create_session(
            self.db,
            "chat",
            self.app_settings_builder.default_model,
            session_id="inactive",
            app_settings=self.app_settings_builder.build(),
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", inactive["session_id"], "user", "old", time.time()),
        )
        self.db.execute(
            "INSERT INTO group_sessions(chat_id,session_id,updated_at) VALUES(?,?,?)",
            ("chat", inactive["session_id"], time.time()),
        )
        self.db.commit()

        deleted, reason = _m_panel_callback_routes.delete_session_data(
            self.db,
            "chat",
            inactive["session_id"],
            active["session_id"],
            operation_id=701,
            memory_service=SimpleNamespace(
                purge_session=lambda _db, chat_id, session_id: self.purged.append((chat_id, session_id)) or 0
            ),
        )

        self.assertTrue(deleted, reason)
        self.assertEqual(_m_message_commands.operation_phase(self.db, 701), "applied")
        self.assertIsNone(
            _m_memory_curator.load_session(
                self.db,
                "chat",
                inactive["session_id"],
                self.app_settings_builder.default_model,
                app_settings=self.app_settings_builder.build(),
            )
            if self.db.execute(
                "SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?", ("chat", inactive["session_id"])
            ).fetchone()
            else None
        )
        self.assertIsNotNone(
            _m_memory_curator.load_session(
                self.db,
                "chat",
                active["session_id"],
                self.app_settings_builder.default_model,
                app_settings=self.app_settings_builder.build(),
            )
        )
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages WHERE session_id='inactive'").fetchone()[0], 0)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM group_sessions WHERE session_id='inactive'").fetchone()[0], 0
        )
        self.assertEqual(self.purged, [("chat", "inactive")])

    def test_hindsight_cleanup_failure_preserves_local_session(self):
        active = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        inactive = _m_session_naming.create_session(
            self.db,
            "chat",
            self.app_settings_builder.default_model,
            session_id="preserved",
            app_settings=self.app_settings_builder.build(),
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", inactive["session_id"], "user", "keep", time.time()),
        )
        self.db.commit()
        deleted, reason = _m_panel_callback_routes.delete_session_data(
            self.db,
            "chat",
            inactive["session_id"],
            active["session_id"],
            memory_service=SimpleNamespace(purge_session=lambda *_args: (_ for _ in ()).throw(RuntimeError("offline"))),
        )

        self.assertFalse(deleted)
        self.assertIn("Hindsight cleanup failed", reason)
        self.assertIsNotNone(
            self.db.execute("SELECT 1 FROM sessions WHERE chat_id='chat' AND session_id='preserved'").fetchone()
        )
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM messages WHERE chat_id='chat' AND session_id='preserved'").fetchone()[
                0
            ],
            1,
        )

    def test_active_session_and_busy_session_are_protected(self):
        active = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        denied, reason = _m_panel_callback_routes.delete_session_data(
            self.db, "chat", active["session_id"], active["session_id"], memory_service=make_test_memory_service()
        )
        self.assertFalse(denied)
        self.assertEqual(reason, "active session")
        inactive = _m_session_naming.create_session(
            self.db,
            "chat",
            self.app_settings_builder.default_model,
            session_id="busy",
            app_settings=self.app_settings_builder.build(),
        )
        self.db.execute(
            (
                "INSERT INTO jobs(update_id,chat_id,session_id,telegram_message_id,kind,p"
                "ayload_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?, ?,?)"
            ),
            (801, "chat", inactive["session_id"], "1", "generation", "{}", "queued", time.time(), time.time()),
        )
        self.db.commit()
        denied, reason = _m_panel_callback_routes.delete_session_data(
            self.db, "chat", inactive["session_id"], active["session_id"], memory_service=make_test_memory_service()
        )
        self.assertFalse(denied)
        self.assertEqual(reason, "session has active jobs")

    def test_session_panel_has_inline_delete_actions_and_protects_active_selection(self):
        active = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        inactive = _m_session_naming.create_session(
            self.db,
            "chat",
            self.app_settings_builder.default_model,
            session_id="inactive",
            app_settings=self.app_settings_builder.build(),
        )
        calls = []
        original_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _m_panel_callback_routes.send_session_menu(
                "token",
                "chat",
                [active, inactive],
                active["session_id"],
                request_context=make_test_request_context(
                    self.db, active["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            _m_cards.send_panel_request = original_request
        rows = calls[0][1]["reply_markup"]["inline_keyboard"]
        callbacks = [button["callback_data"] for row in rows for button in row]
        self.assertEqual(sum(value.startswith("sessiondelete:") for value in callbacks), 1)
        self.assertIn("session:protected", callbacks)
        self.assertIn("session:" + active["session_id"], callbacks)
        self.assertIn("session:" + inactive["session_id"], callbacks)
        self.assertNotIn("session:delete", callbacks)


if __name__ == "__main__":
    unittest.main()

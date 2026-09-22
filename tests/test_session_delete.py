from application_test_setup import ensure_application_extensions

ensure_application_extensions()

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import bridge.config as config
import time
import bridge.callbacks as _m_callbacks
import bridge.main as _m_main
import bridge.memory as _m_memory
import bridge.cards as _m_cards
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.session_naming as _m_session_naming
class SessionDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.original_purge = _m_memory.purge_hindsight_session
        self.purged = []
        _m_memory.purge_hindsight_session = lambda _db, chat_id, session_id: self.purged.append((chat_id, session_id)) or 0

    def tearDown(self):
        _m_memory.purge_hindsight_session = self.original_purge
        self.db.close()
        self.tmp.cleanup()

    def test_inactive_session_deletes_all_local_data(self):
        active = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        inactive = _m_session_naming.create_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL, session_id="inactive")
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
            self.db, "chat", inactive["session_id"], active["session_id"], operation_id=701,
            memory_service=SimpleNamespace(purge_session=lambda _db, chat_id, session_id: self.purged.append((chat_id, session_id)) or 0),
        )

        self.assertTrue(deleted, reason)
        self.assertEqual(_m_message_commands.operation_phase(self.db, 701), "applied")
        self.assertIsNone(_m_memory_curator.load_session(self.db, "chat", inactive["session_id"], _m_memory_curator.DEFAULT_MODEL) if self.db.execute("SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?", ("chat", inactive["session_id"])).fetchone() else None)
        self.assertIsNotNone(_m_memory_curator.load_session(self.db, "chat", active["session_id"], _m_memory_curator.DEFAULT_MODEL))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages WHERE session_id='inactive'").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM group_sessions WHERE session_id='inactive'").fetchone()[0], 0)
        self.assertEqual(self.purged, [("chat", "inactive")])

    def test_hindsight_cleanup_failure_preserves_local_session(self):
        active = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        inactive = _m_session_naming.create_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL, session_id="preserved")
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", inactive["session_id"], "user", "keep", time.time()),
        )
        self.db.commit()
        deleted, reason = _m_panel_callback_routes.delete_session_data(
            self.db, "chat", inactive["session_id"], active["session_id"],
            memory_service=SimpleNamespace(purge_session=lambda *_args: (_ for _ in ()).throw(RuntimeError("offline"))),
        )

        self.assertFalse(deleted)
        self.assertIn("Hindsight cleanup failed", reason)
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM sessions WHERE chat_id='chat' AND session_id='preserved'").fetchone())
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages WHERE chat_id='chat' AND session_id='preserved'").fetchone()[0], 1)

    def test_active_session_and_busy_session_are_protected(self):
        active = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        denied, reason = _m_panel_callback_routes.delete_session_data(self.db, "chat", active["session_id"], active["session_id"])
        self.assertFalse(denied)
        self.assertEqual(reason, "active session")
        inactive = _m_session_naming.create_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL, session_id="busy")
        self.db.execute(
            "INSERT INTO jobs(update_id,chat_id,session_id,telegram_message_id,kind,payload_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?, ?,?)",
            (801, "chat", inactive["session_id"], "1", "generation", "{}", "queued", time.time(), time.time()),
        )
        self.db.commit()
        denied, reason = _m_panel_callback_routes.delete_session_data(self.db, "chat", inactive["session_id"], active["session_id"])
        self.assertFalse(denied)
        self.assertEqual(reason, "session has active jobs")

    def test_session_panel_has_inline_delete_actions_and_protects_active_selection(self):
        active = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        inactive = _m_session_naming.create_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL, session_id="inactive")
        calls = []
        original_request = _m_cards.telegram_request
        _m_cards.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            _m_panel_callback_routes.send_session_menu("token", "chat", [active, inactive], active["session_id"])
        finally:
            _m_cards.telegram_request = original_request
        rows = calls[0][1]["reply_markup"]["inline_keyboard"]
        callbacks = [button["callback_data"] for row in rows for button in row]
        self.assertEqual(sum(value.startswith("sessiondelete:") for value in callbacks), 1)
        self.assertIn("session:protected", callbacks)
        self.assertIn("session:" + active["session_id"], callbacks)
        self.assertIn("session:" + inactive["session_id"], callbacks)
        self.assertNotIn("session:delete", callbacks)


if __name__ == "__main__":
    unittest.main()

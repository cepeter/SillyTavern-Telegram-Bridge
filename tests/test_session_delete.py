from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class SessionDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.original_purge = rt.purge_hindsight_session
        self.purged = []
        rt.purge_hindsight_session = lambda _db, chat_id, session_id: self.purged.append((chat_id, session_id)) or 0

    def tearDown(self):
        rt.purge_hindsight_session = self.original_purge
        self.db.close()
        self.tmp.cleanup()

    def test_inactive_session_deletes_all_local_data(self):
        active = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        inactive = rt.create_session(self.db, "chat", rt.DEFAULT_MODEL, session_id="inactive")
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", inactive["session_id"], "user", "old", rt.time.time()),
        )
        self.db.execute(
            "INSERT INTO group_sessions(chat_id,session_id,updated_at) VALUES(?,?,?)",
            ("chat", inactive["session_id"], rt.time.time()),
        )
        self.db.commit()

        deleted, reason = rt.delete_session_data(
            self.db, "chat", inactive["session_id"], active["session_id"], operation_id=701
        )

        self.assertTrue(deleted, reason)
        self.assertEqual(rt.operation_phase(self.db, 701), "applied")
        self.assertIsNone(rt.load_session(self.db, "chat", inactive["session_id"], rt.DEFAULT_MODEL) if self.db.execute("SELECT 1 FROM sessions WHERE chat_id=? AND session_id=?", ("chat", inactive["session_id"])).fetchone() else None)
        self.assertIsNotNone(rt.load_session(self.db, "chat", active["session_id"], rt.DEFAULT_MODEL))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages WHERE session_id='inactive'").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM group_sessions WHERE session_id='inactive'").fetchone()[0], 0)
        self.assertEqual(self.purged, [("chat", "inactive")])

    def test_hindsight_cleanup_failure_preserves_local_session(self):
        active = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        inactive = rt.create_session(self.db, "chat", rt.DEFAULT_MODEL, session_id="preserved")
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", inactive["session_id"], "user", "keep", rt.time.time()),
        )
        self.db.commit()
        rt.purge_hindsight_session = lambda *_args: (_ for _ in ()).throw(RuntimeError("offline"))

        deleted, reason = rt.delete_session_data(self.db, "chat", inactive["session_id"], active["session_id"])

        self.assertFalse(deleted)
        self.assertIn("Hindsight cleanup failed", reason)
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM sessions WHERE chat_id='chat' AND session_id='preserved'").fetchone())
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages WHERE chat_id='chat' AND session_id='preserved'").fetchone()[0], 1)

    def test_active_session_and_busy_session_are_protected(self):
        active = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        denied, reason = rt.delete_session_data(self.db, "chat", active["session_id"], active["session_id"])
        self.assertFalse(denied)
        self.assertEqual(reason, "active session")
        inactive = rt.create_session(self.db, "chat", rt.DEFAULT_MODEL, session_id="busy")
        self.db.execute(
            "INSERT INTO jobs(update_id,chat_id,session_id,telegram_message_id,kind,payload_json,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?, ?,?)",
            (801, "chat", inactive["session_id"], "1", "generation", "{}", "queued", rt.time.time(), rt.time.time()),
        )
        self.db.commit()
        denied, reason = rt.delete_session_data(self.db, "chat", inactive["session_id"], active["session_id"])
        self.assertFalse(denied)
        self.assertEqual(reason, "session has active jobs")

    def test_delete_panel_excludes_active_session(self):
        active = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        inactive = rt.create_session(self.db, "chat", rt.DEFAULT_MODEL, session_id="inactive")
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_session_delete_menu("token", "chat", [active, inactive], active["session_id"])
        finally:
            rt.telegram_request = original_request
        callbacks = {button["callback_data"] for row in calls[0][1]["reply_markup"]["inline_keyboard"] for button in row}
        self.assertTrue(any(value.startswith("sessiondelete:") for value in callbacks))
        self.assertFalse(any(value.endswith(active["session_id"]) for value in callbacks))


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class PanelExpiryFeedbackTests(unittest.TestCase):
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

    def test_queued_expired_panel_sends_visible_feedback_and_purges_binding(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.update_session(self.db, "chat", session["session_id"], author_note="keep me")
        rt.bind_panel_session(self.db, "chat", 501, session["session_id"])
        self.db.execute(
            "UPDATE panel_sessions SET expires_at=? WHERE chat_id=? AND message_id=?",
            (rt.time.time() - 1, "chat", "501"),
        )
        self.db.commit()

        sent = []
        answered = []
        original_send = rt.send_text
        original_answer = rt.answer_callback
        rt.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        rt.answer_callback = lambda _token, _callback_id, text: answered.append(text)
        callback = {
            "id": "callback-501",
            "from": {"id": "user-1"},
            "data": "note:off",
            "_queued": True,
            "message": {"message_id": 501, "chat": {"id": "chat"}},
        }
        try:
            rt.process_callback(self.db, "token", callback)
        finally:
            rt.send_text = original_send
            rt.answer_callback = original_answer

        self.assertEqual(sent, ["Panel expired; reopen it"])
        self.assertEqual(answered, [])
        stale = self.db.execute(
            "SELECT 1 FROM panel_sessions WHERE chat_id=? AND message_id=?",
            ("chat", "501"),
        ).fetchone()
        self.assertIsNone(stale)
        stored = rt.load_session(self.db, "chat", session["session_id"], rt.DEFAULT_MODEL)
        self.assertEqual(stored["author_note"], "keep me")

    def test_nonqueued_expired_panel_keeps_callback_toast_behavior(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.bind_panel_session(self.db, "chat", 502, session["session_id"])
        self.db.execute(
            "UPDATE panel_sessions SET expires_at=? WHERE chat_id=? AND message_id=?",
            (rt.time.time() - 1, "chat", "502"),
        )
        self.db.commit()

        sent = []
        answered = []
        original_send = rt.send_text
        original_answer = rt.answer_callback
        rt.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        rt.answer_callback = lambda _token, _callback_id, text: answered.append(text)
        callback = {
            "id": "callback-502",
            "from": {"id": "user-1"},
            "data": "persona:off",
            "message": {"message_id": 502, "chat": {"id": "chat"}},
        }
        try:
            rt.process_callback(self.db, "token", callback)
        finally:
            rt.send_text = original_send
            rt.answer_callback = original_answer

        self.assertEqual(sent, [])
        self.assertEqual(answered, ["Panel expired; reopen it"])


if __name__ == "__main__":
    unittest.main()

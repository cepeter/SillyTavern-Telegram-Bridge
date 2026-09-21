from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import bridge.runtime as rt


class PanelExpiryFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = rt.db_connect()

    def tearDown(self):
        self.db.close()
        config.DB_FILE = self.original_db
        self.tmp.cleanup()

    def test_queued_expired_panel_sends_visible_feedback_and_purges_binding(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.update_session(self.db, "chat", session["session_id"], author_note="keep me")
        rt.bind_panel_session(self.db, "chat", 501, session["session_id"])
        self.db.execute("UPDATE panel_sessions SET expires_at=? WHERE chat_id=? AND message_id=?", (rt.time.time() - 1, "chat", "501"))
        self.db.commit()
        sent, answered, closed = [], [], []
        original_send, original_answer, original_close = rt.send_text, rt.answer_callback, rt.close_panel_message
        rt.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        rt.answer_callback = lambda _token, _callback_id, text: answered.append(text)
        rt.close_panel_message = lambda _token, _chat_id, value: closed.append(value)
        callback = {"id": "callback-501", "from": {"id": "user-1"}, "data": "note:off", "_queued": True, "message": {"message_id": 501, "chat": {"id": "chat"}}}
        try:
            rt.process_callback(self.db, "token", callback)
        finally:
            rt.send_text, rt.answer_callback, rt.close_panel_message = original_send, original_answer, original_close
        self.assertEqual(sent, ["Panel expired; reopen it"])
        self.assertEqual(answered, [])
        self.assertEqual(closed, [callback])
        self.assertIsNone(self.db.execute("SELECT 1 FROM panel_sessions WHERE chat_id=? AND message_id=?", ("chat", "501")).fetchone())
        self.assertEqual(rt.load_session(self.db, "chat", session["session_id"], rt.DEFAULT_MODEL)["author_note"], "keep me")

    def test_nonqueued_expired_panel_keeps_callback_toast_behavior(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.bind_panel_session(self.db, "chat", 502, session["session_id"])
        self.db.execute("UPDATE panel_sessions SET expires_at=? WHERE chat_id=? AND message_id=?", (rt.time.time() - 1, "chat", "502"))
        self.db.commit()
        sent, answered, closed = [], [], []
        original_send, original_answer, original_close = rt.send_text, rt.answer_callback, rt.close_panel_message
        rt.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        rt.answer_callback = lambda _token, _callback_id, text: answered.append(text)
        rt.close_panel_message = lambda _token, _chat_id, value: closed.append(value)
        callback = {"id": "callback-502", "from": {"id": "user-1"}, "data": "persona:off", "message": {"message_id": 502, "chat": {"id": "chat"}}}
        try:
            rt.process_callback(self.db, "token", callback)
        finally:
            rt.send_text, rt.answer_callback, rt.close_panel_message = original_send, original_answer, original_close
        self.assertEqual(sent, [])
        self.assertEqual(answered, ["Panel expired; reopen it"])
        self.assertEqual(closed, [callback])
        self.assertIsNone(self.db.execute("SELECT 1 FROM panel_sessions WHERE chat_id=? AND message_id=?", ("chat", "502")).fetchone())


if __name__ == "__main__":
    unittest.main()

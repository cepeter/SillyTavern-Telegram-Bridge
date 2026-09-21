from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import time
from dependency_patch import dependency_module

_m_callbacks = dependency_module("bridge.callbacks")
_m_memory_curator = dependency_module("bridge.memory_curator")
_m_session_naming = dependency_module("bridge.session_naming")
_m_telegram = dependency_module("bridge.telegram")


class PanelExpiryFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()

    def tearDown(self):
        self.db.close()
        config.DB_FILE = self.original_db
        self.tmp.cleanup()

    def test_queued_expired_panel_sends_visible_feedback_and_purges_binding(self):
        session = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        _m_session_naming.update_session(self.db, "chat", session["session_id"], author_note="keep me")
        _m_telegram.bind_panel_session(self.db, "chat", 501, session["session_id"])
        self.db.execute("UPDATE panel_sessions SET expires_at=? WHERE chat_id=? AND message_id=?", (time.time() - 1, "chat", "501"))
        self.db.commit()
        sent, answered, closed = [], [], []
        original_send, original_answer, original_close = _m_memory_curator.send_text, _m_callbacks.answer_callback, _m_session_naming.close_panel_message
        _m_memory_curator.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        _m_callbacks.answer_callback = lambda _token, _callback_id, text: answered.append(text)
        _m_session_naming.close_panel_message = lambda _token, _chat_id, value: closed.append(value)
        callback = {"id": "callback-501", "from": {"id": "user-1"}, "data": "note:off", "_queued": True, "message": {"message_id": 501, "chat": {"id": "chat"}}}
        try:
            _m_callbacks.process_callback(self.db, "token", callback)
        finally:
            _m_memory_curator.send_text, _m_callbacks.answer_callback, _m_session_naming.close_panel_message = original_send, original_answer, original_close
        self.assertEqual(sent, ["Panel expired; reopen it"])
        self.assertEqual(answered, [])
        self.assertEqual(closed, [callback])
        self.assertIsNone(self.db.execute("SELECT 1 FROM panel_sessions WHERE chat_id=? AND message_id=?", ("chat", "501")).fetchone())
        self.assertEqual(_m_memory_curator.load_session(self.db, "chat", session["session_id"], _m_memory_curator.DEFAULT_MODEL)["author_note"], "keep me")

    def test_nonqueued_expired_panel_keeps_callback_toast_behavior(self):
        session = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        _m_telegram.bind_panel_session(self.db, "chat", 502, session["session_id"])
        self.db.execute("UPDATE panel_sessions SET expires_at=? WHERE chat_id=? AND message_id=?", (time.time() - 1, "chat", "502"))
        self.db.commit()
        sent, answered, closed = [], [], []
        original_send, original_answer, original_close = _m_memory_curator.send_text, _m_callbacks.answer_callback, _m_session_naming.close_panel_message
        _m_memory_curator.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        _m_callbacks.answer_callback = lambda _token, _callback_id, text: answered.append(text)
        _m_session_naming.close_panel_message = lambda _token, _chat_id, value: closed.append(value)
        callback = {"id": "callback-502", "from": {"id": "user-1"}, "data": "persona:off", "message": {"message_id": 502, "chat": {"id": "chat"}}}
        try:
            _m_callbacks.process_callback(self.db, "token", callback)
        finally:
            _m_memory_curator.send_text, _m_callbacks.answer_callback, _m_session_naming.close_panel_message = original_send, original_answer, original_close
        self.assertEqual(sent, [])
        self.assertEqual(answered, ["Panel expired; reopen it"])
        self.assertEqual(closed, [callback])
        self.assertIsNone(self.db.execute("SELECT 1 FROM panel_sessions WHERE chat_id=? AND message_id=?", ("chat", "502")).fetchone())


if __name__ == "__main__":
    unittest.main()

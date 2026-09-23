from application_test_setup import ensure_application_extensions, make_test_application_services

ensure_application_extensions()

from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import time
import bridge.callbacks as _m_callbacks
import bridge.callback_dispatch as _m_callback_dispatch
import bridge.memory_curator as _m_memory_curator
import bridge.session_naming as _m_session_naming
import bridge.telegram as _m_telegram
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
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        _m_session_naming.update_session(self.db, "chat", session["session_id"], author_note="keep me")
        _m_telegram.bind_panel_session(self.db, "chat", 501, session["session_id"])
        self.db.execute("UPDATE panel_sessions SET expires_at=? WHERE chat_id=? AND message_id=?", (time.time() - 1, "chat", "501"))
        self.db.commit()
        sent, answered, closed = [], [], []
        original_send, original_answer, original_close = _m_callback_dispatch.send_text, _m_callback_dispatch.answer_callback, _m_callback_dispatch.close_panel_message
        _m_callback_dispatch.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        _m_callback_dispatch.answer_callback = lambda _token, _callback_id, text: answered.append(text)
        _m_callback_dispatch.close_panel_message = lambda _db, _token, _chat_id, value: closed.append(value)
        callback = {"id": "callback-501", "from": {"id": "user-1"}, "data": "note:off", "_queued": True, "message": {"message_id": 501, "chat": {"id": "chat"}}}
        try:
            _m_callback_dispatch.process_callback(self.db, "token", callback, services=make_test_application_services())
        finally:
            _m_callback_dispatch.send_text, _m_callback_dispatch.answer_callback, _m_callback_dispatch.close_panel_message = original_send, original_answer, original_close
        self.assertEqual(sent, ["Panel expired; reopen it"])
        self.assertEqual(answered, [])
        self.assertEqual(closed, [callback])
        self.assertIsNone(self.db.execute("SELECT 1 FROM panel_sessions WHERE chat_id=? AND message_id=?", ("chat", "501")).fetchone())
        self.assertEqual(_m_memory_curator.load_session(self.db, "chat", session["session_id"], _m_memory_curator.DEFAULT_MODEL)["author_note"], "keep me")

    def test_nonqueued_expired_panel_keeps_callback_toast_behavior(self):
        session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        _m_telegram.bind_panel_session(self.db, "chat", 502, session["session_id"])
        self.db.execute("UPDATE panel_sessions SET expires_at=? WHERE chat_id=? AND message_id=?", (time.time() - 1, "chat", "502"))
        self.db.commit()
        sent, answered, closed = [], [], []
        original_send, original_answer, original_close = _m_callback_dispatch.send_text, _m_callback_dispatch.answer_callback, _m_callback_dispatch.close_panel_message
        _m_callback_dispatch.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        _m_callback_dispatch.answer_callback = lambda _token, _callback_id, text: answered.append(text)
        _m_callback_dispatch.close_panel_message = lambda _db, _token, _chat_id, value: closed.append(value)
        callback = {"id": "callback-502", "from": {"id": "user-1"}, "data": "persona:off", "message": {"message_id": 502, "chat": {"id": "chat"}}}
        try:
            _m_callback_dispatch.process_callback(self.db, "token", callback, services=make_test_application_services())
        finally:
            _m_callback_dispatch.send_text, _m_callback_dispatch.answer_callback, _m_callback_dispatch.close_panel_message = original_send, original_answer, original_close
        self.assertEqual(sent, [])
        self.assertEqual(answered, ["Panel expired; reopen it"])
        self.assertEqual(closed, [callback])
        self.assertIsNone(self.db.execute("SELECT 1 FROM panel_sessions WHERE chat_id=? AND message_id=?", ("chat", "502")).fetchone())


if __name__ == "__main__":
    unittest.main()

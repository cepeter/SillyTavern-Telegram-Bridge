from application_test_setup import ensure_application_extensions, make_test_application_services, make_test_memory_service

ensure_application_extensions()

# pyright: reportAttributeAccessIssue=false

import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

import bridge.config as config
import time
import bridge.callbacks as _m_callbacks
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.session_naming as _m_session_naming
import bridge.media as _m_media
import bridge.telegram as _m_telegram
class ResetBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        self.old_reset = getattr(_m_message_commands, "reset_session")
        self.old_send = getattr(_m_panel_callback_routes, "send_text")
        self.old_remove = getattr(_m_media, "remove_inline_keyboard")
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)

    def tearDown(self):
        _m_panel_callback_routes.reset_session = self.old_reset
        _m_panel_callback_routes.send_text = self.old_send
        _m_panel_callback_routes.remove_inline_keyboard = self.old_remove
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def test_reset_command_preempts_pending_input(self):
        _m_session_naming.set_meta(self.db, "text_action_input:chat", json.dumps({
            "session_id": self.session["session_id"],
            "action": "edit",
            "expires_at": time.time() + 600,
        }))
        opened = []
        original_menu = _m_message_commands.send_reset_confirmation_menu
        setattr(_m_message_commands, "send_reset_confirmation_menu", lambda *_args, **_kwargs: opened.append(True))
        try:
            _m_message_commands.process_message(self.db, "token", "key", _m_memory_curator.DEFAULT_MODEL, {}, "chat", "/reset", services=make_test_application_services(memory=make_test_memory_service()))
        finally:
            setattr(_m_message_commands, "send_reset_confirmation_menu", original_menu)
        self.assertEqual(opened, [True])
        self.assertIn('"action": "edit"', _m_session_naming.get_meta(self.db, "text_action_input:chat", ""))

    def test_confirmed_reset_sends_visible_completion_message(self):
        sent = []
        removed = []
        original_answer = lambda *_args, **_kwargs: None
        setattr(_m_panel_callback_routes, "reset_session", lambda *_args, **_kwargs: None)
        setattr(_m_panel_callback_routes, "send_text", lambda _token, _chat, text: sent.append(text) or [])
        setattr(_m_panel_callback_routes, "remove_inline_keyboard", lambda _db, _token, callback: removed.append(callback))
        callback = {"id": "callback-1", "message": {"message_id": 10, "chat": {"id": "chat"}}}
        handled = _m_panel_callback_routes.handle_reset_callback(
            self.db, "token", callback, original_answer, "reset:confirm", "chat",
            callback["message"], self.session, self.session["session_id"], None,
         memory_service=make_test_memory_service())
        self.assertTrue(handled)
        self.assertEqual(sent, ["Reset complete. The active session was cleared."])
        self.assertEqual(removed, [callback])


if __name__ == "__main__":
    unittest.main()

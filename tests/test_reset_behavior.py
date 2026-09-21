# pyright: reportAttributeAccessIssue=false

import json
import tempfile
import unittest
from pathlib import Path

import bridge.config as config
import time
from dependency_patch import dependency_module

_m_callbacks = dependency_module("bridge.callbacks")
_m_memory_curator = dependency_module("bridge.memory_curator")
_m_message_commands = dependency_module("bridge.message_commands")
_m_panel_callback_routes = dependency_module("bridge.panel_callback_routes")
_m_session_naming = dependency_module("bridge.session_naming")
_m_media = dependency_module("bridge.media")
_m_telegram = dependency_module("bridge.telegram")


class ResetBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        self.old_reset = getattr(_m_message_commands, "reset_session")
        self.old_send = getattr(_m_telegram, "send_text")
        self.old_remove = getattr(_m_media, "remove_inline_keyboard")
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.session = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)

    def tearDown(self):
        _m_panel_callback_routes.reset_session = self.old_reset
        _m_memory_curator.send_text = self.old_send
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
            _m_message_commands.process_message(self.db, "token", "key", _m_memory_curator.DEFAULT_MODEL, {}, "chat", "/reset")
        finally:
            setattr(_m_message_commands, "send_reset_confirmation_menu", original_menu)
        self.assertEqual(opened, [True])
        self.assertIn('"action": "edit"', _m_session_naming.get_meta(self.db, "text_action_input:chat", ""))

    def test_confirmed_reset_sends_visible_completion_message(self):
        sent = []
        removed = []
        original_answer = lambda *_args, **_kwargs: None
        setattr(_m_message_commands, "reset_session", lambda *_args, **_kwargs: None)
        setattr(_m_telegram, "send_text", lambda _token, _chat, text: sent.append(text) or [])
        setattr(_m_media, "remove_inline_keyboard", lambda _token, callback: removed.append(callback))
        callback = {"id": "callback-1", "message": {"message_id": 10, "chat": {"id": "chat"}}}
        handled = _m_panel_callback_routes.handle_reset_callback(
            self.db, "token", callback, original_answer, "reset:confirm", "chat",
            callback["message"], self.session, self.session["session_id"], None,
        )
        self.assertTrue(handled)
        self.assertEqual(sent, ["Reset complete. The active session was cleared."])
        self.assertEqual(removed, [callback])


if __name__ == "__main__":
    unittest.main()

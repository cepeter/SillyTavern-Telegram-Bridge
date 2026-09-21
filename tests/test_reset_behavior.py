# pyright: reportAttributeAccessIssue=false

import json
import tempfile
import unittest
from pathlib import Path

import bridge.config as config
from runtime_test_facade import runtime as rt


class ResetBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        self.old_reset = getattr(rt, "reset_session")
        self.old_send = getattr(rt, "send_text")
        self.old_remove = getattr(rt, "remove_inline_keyboard")
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = rt.db_connect()
        self.session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)

    def tearDown(self):
        rt.reset_session = self.old_reset
        rt.send_text = self.old_send
        rt.remove_inline_keyboard = self.old_remove
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def test_reset_command_preempts_pending_input(self):
        rt.set_meta(self.db, "text_action_input:chat", json.dumps({
            "session_id": self.session["session_id"],
            "action": "edit",
            "expires_at": rt.time.time() + 600,
        }))
        opened = []
        original_menu = rt.send_reset_confirmation_menu
        setattr(rt, "send_reset_confirmation_menu", lambda *_args, **_kwargs: opened.append(True))
        try:
            rt.process_message(self.db, "token", "key", rt.DEFAULT_MODEL, {}, "chat", "/reset")
        finally:
            setattr(rt, "send_reset_confirmation_menu", original_menu)
        self.assertEqual(opened, [True])
        self.assertIn('"action": "edit"', rt.get_meta(self.db, "text_action_input:chat", ""))

    def test_confirmed_reset_sends_visible_completion_message(self):
        sent = []
        removed = []
        original_answer = lambda *_args, **_kwargs: None
        setattr(rt, "reset_session", lambda *_args, **_kwargs: None)
        setattr(rt, "send_text", lambda _token, _chat, text: sent.append(text) or [])
        setattr(rt, "remove_inline_keyboard", lambda _token, callback: removed.append(callback))
        callback = {"id": "callback-1", "message": {"message_id": 10, "chat": {"id": "chat"}}}
        handled = rt.handle_reset_callback(
            self.db, "token", callback, original_answer, "reset:confirm", "chat",
            callback["message"], self.session, self.session["session_id"], None,
        )
        self.assertTrue(handled)
        self.assertEqual(sent, ["Reset complete. The active session was cleared."])
        self.assertEqual(removed, [callback])


if __name__ == "__main__":
    unittest.main()

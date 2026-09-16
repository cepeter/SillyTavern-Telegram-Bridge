from pathlib import Path
import json
import tempfile
import unittest

import bridge.runtime as rt


class CharacterSessionChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _callback(self, data, message_id=10):
        return {"id": "callback", "from": {"id": "user"}, "data": data, "message": {"message_id": message_id, "chat": {"id": "chat"}}}

    def test_character_selection_opens_session_panel(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        opened = []
        original_resolve = rt.resolve_dynamic_callback_token
        original_safe = rt.safe_character_path
        original_fields = rt.card_fields_from_file
        original_close = rt.close_panel_message
        original_menu = rt.send_session_menu
        rt.resolve_dynamic_callback_token = lambda *_args: "chosen.png"
        rt.safe_character_path = lambda _name: Path("/tmp/chosen.png")
        rt.card_fields_from_file = lambda _name: {"name": "Chosen"}
        rt.close_panel_message = lambda *_args, **_kwargs: None
        rt.send_session_menu = lambda *_args, **_kwargs: opened.append(True)
        try:
            callback = self._callback("character:token")
            handled = rt.handle_character_callback(self.db, "token", callback, lambda *_args: None, callback["data"], "chat", callback["message"], session, session["session_id"], None)
        finally:
            rt.resolve_dynamic_callback_token = original_resolve
            rt.safe_character_path = original_safe
            rt.card_fields_from_file = original_fields
            rt.close_panel_message = original_close
            rt.send_session_menu = original_menu
        self.assertTrue(handled)
        self.assertEqual(opened, [True])
        self.assertEqual(rt.load_session(self.db, "chat", session["session_id"], rt.DEFAULT_MODEL)["character_file"], rt.DEFAULT_CHARACTER_FILE)
        pending = json.loads(rt.get_meta(self.db, "character_session_input:chat", "{}"))
        self.assertEqual(pending["character_file"], "chosen.png")
        self.assertEqual(pending["character_name"], "Chosen")

    def test_session_selection_applies_pending_character(self):
        current = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        target = rt.create_session(self.db, "chat", rt.DEFAULT_MODEL, session_id="target")
        rt.set_meta(self.db, "active_session:chat", current["session_id"])
        rt.set_meta(self.db, "character_session_input:chat", json.dumps({"character_file": "chosen.png", "character_name": "Chosen", "expires_at": rt.time.time() + 600}))
        original_safe = rt.safe_character_path
        original_remove = rt.remove_inline_keyboard
        original_send = rt.send_text
        rt.safe_character_path = lambda _name: Path("/tmp/chosen.png")
        rt.remove_inline_keyboard = lambda *_args, **_kwargs: None
        sent = []
        rt.send_text = lambda _token, _chat, text: sent.append(text) or []
        try:
            callback = self._callback("session:target")
            handled = rt.handle_session_callback(self.db, "token", callback, lambda *_args: None, callback["data"], "chat", callback["message"], current, current["session_id"], None)
        finally:
            rt.safe_character_path = original_safe
            rt.remove_inline_keyboard = original_remove
            rt.send_text = original_send
        self.assertTrue(handled)
        self.assertEqual(rt.load_session(self.db, "chat", target["session_id"], rt.DEFAULT_MODEL)["character_file"], "chosen.png")
        self.assertEqual(rt.get_meta(self.db, "active_session:chat", ""), "target")
        self.assertEqual(rt.get_meta(self.db, "character_session_input:chat", ""), "")
        self.assertIn("Chosen", sent[0])


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import json
import tempfile
import unittest

import bridge.config as config
import time
from dependency_patch import dependency_module

_m_callbacks = dependency_module("bridge.callbacks")
_m_character_identity = dependency_module("bridge.character_identity")
_m_memory_curator = dependency_module("bridge.memory_curator")
_m_panel_callback_routes = dependency_module("bridge.panel_callback_routes")
_m_session_naming = dependency_module("bridge.session_naming")
_m_sync_api = dependency_module("bridge.sync_api")


class CharacterSessionChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _callback(self, data, message_id=10):
        return {"id": "callback", "from": {"id": "user"}, "data": data, "message": {"message_id": message_id, "chat": {"id": "chat"}}}

    def test_character_selection_opens_session_panel(self):
        session = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        opened = []
        original_resolve = _m_panel_callback_routes.resolve_dynamic_callback_token
        original_safe = _m_character_identity.safe_character_path
        original_fields = _m_sync_api.card_fields_from_file
        original_close = _m_session_naming.close_panel_message
        original_menu = _m_panel_callback_routes.send_session_menu
        _m_panel_callback_routes.resolve_dynamic_callback_token = lambda *_args: "chosen.png"
        _m_character_identity.safe_character_path = lambda _name: Path("/tmp/chosen.png")
        _m_sync_api.card_fields_from_file = lambda _name: {"name": "Chosen"}
        _m_session_naming.close_panel_message = lambda *_args, **_kwargs: None
        _m_panel_callback_routes.send_session_menu = lambda *_args, **_kwargs: opened.append(True)
        try:
            callback = self._callback("character:token")
            handled = _m_panel_callback_routes.handle_character_callback(self.db, "token", callback, lambda *_args: None, callback["data"], "chat", callback["message"], session, session["session_id"], None)
        finally:
            _m_panel_callback_routes.resolve_dynamic_callback_token = original_resolve
            _m_character_identity.safe_character_path = original_safe
            _m_sync_api.card_fields_from_file = original_fields
            _m_session_naming.close_panel_message = original_close
            _m_panel_callback_routes.send_session_menu = original_menu
        self.assertTrue(handled)
        self.assertEqual(opened, [True])
        self.assertEqual(_m_memory_curator.load_session(self.db, "chat", session["session_id"], _m_memory_curator.DEFAULT_MODEL)["character_file"], _m_panel_callback_routes.DEFAULT_CHARACTER_FILE)
        pending = json.loads(_m_session_naming.get_meta(self.db, "character_session_input:chat", "{}"))
        self.assertEqual(pending["character_file"], "chosen.png")
        self.assertEqual(pending["character_name"], "Chosen")

    def test_session_selection_applies_pending_character(self):
        current = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        target = _m_session_naming.create_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL, session_id="target")
        _m_session_naming.set_meta(self.db, "active_session:chat", current["session_id"])
        _m_session_naming.set_meta(self.db, "character_session_input:chat", json.dumps({"character_file": "chosen.png", "character_name": "Chosen", "expires_at": time.time() + 600}))
        original_safe = _m_character_identity.safe_character_path
        original_remove = _m_panel_callback_routes.remove_inline_keyboard
        original_send = _m_memory_curator.send_text
        _m_character_identity.safe_character_path = lambda _name: Path("/tmp/chosen.png")
        _m_panel_callback_routes.remove_inline_keyboard = lambda *_args, **_kwargs: None
        sent = []
        _m_memory_curator.send_text = lambda _token, _chat, text: sent.append(text) or []
        try:
            callback = self._callback("session:target")
            handled = _m_panel_callback_routes.handle_session_callback(self.db, "token", callback, lambda *_args: None, callback["data"], "chat", callback["message"], current, current["session_id"], None)
        finally:
            _m_character_identity.safe_character_path = original_safe
            _m_panel_callback_routes.remove_inline_keyboard = original_remove
            _m_memory_curator.send_text = original_send
        self.assertTrue(handled)
        self.assertEqual(_m_memory_curator.load_session(self.db, "chat", target["session_id"], _m_memory_curator.DEFAULT_MODEL)["character_file"], "chosen.png")
        self.assertEqual(_m_session_naming.get_meta(self.db, "active_session:chat", ""), "target")
        self.assertEqual(_m_session_naming.get_meta(self.db, "character_session_input:chat", ""), "")
        self.assertIn("Chosen", sent[0])


if __name__ == "__main__":
    unittest.main()

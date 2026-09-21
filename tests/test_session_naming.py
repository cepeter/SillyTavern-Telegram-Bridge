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
_m_message_commands = dependency_module("bridge.message_commands")
_m_panel_callback_routes = dependency_module("bridge.panel_callback_routes")
_m_session_naming = dependency_module("bridge.session_naming")
_m_sync_api = dependency_module("bridge.sync_api")


class SessionNamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.session = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        self.sent = []
        self.old_send = _m_memory_curator.send_text
        _m_memory_curator.send_text = lambda _token, _chat, text: self.sent.append(text) or []

    def tearDown(self):
        _m_memory_curator.send_text = self.old_send
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def _state(self, chat_id="chat"):
        return json.loads(_m_session_naming.get_meta(self.db, f"session_name_input:{chat_id}", "{}"))

    def test_standard_session_is_created_only_after_valid_name(self):
        _m_session_naming.start_session_name_input(self.db, "token", "chat", self.session)
        self.assertEqual(len(_m_panel_callback_routes.list_sessions(self.db, "chat")), 1)
        self.assertTrue(_m_message_commands.handle_pending_input(self.db, "token", "chat", self.session, "  Project   Alpha  ", operation_id=42))
        created = _m_memory_curator.load_session(self.db, "chat", "job-42", _m_memory_curator.DEFAULT_MODEL)
        self.assertEqual(created["title"], "Project Alpha")
        self.assertEqual(_m_session_naming.get_meta(self.db, "active_session:chat", ""), "job-42")
        self.assertEqual(_m_session_naming.get_meta(self.db, "session_name_input:chat", ""), "")

    def test_status_displays_custom_session_title_with_technical_id(self):
        _m_session_naming.update_session(self.db, "chat", self.session["session_id"], title="Evening Story")
        original_card = _m_sync_api.card_fields_from_file
        _m_sync_api.card_fields_from_file = lambda _filename: {"name": "Test", "post_history_instructions": ""}
        try:
            _m_message_commands.process_message(self.db, "token", "key", _m_memory_curator.DEFAULT_MODEL, {}, "chat", "/status")
        finally:
            _m_sync_api.card_fields_from_file = original_card
        self.assertIn("📊 Session status", self.sent[-1])
        self.assertIn("🗂️ Session: Evening Story (default)", self.sent[-1])
        self.assertIn("• System Prompt: off", self.sent[-1])

    def test_invalid_name_reprompts_without_creating_session(self):
        _m_session_naming.start_session_name_input(self.db, "token", "chat", self.session)
        self.assertTrue(_m_message_commands.handle_pending_input(self.db, "token", "chat", self.session, "/bad", operation_id=43))
        self.assertEqual(len(_m_panel_callback_routes.list_sessions(self.db, "chat")), 1)
        self.assertTrue(self._state())
        self.assertIn("cannot start with /", self.sent[-1])

    def test_cancel_leaves_no_empty_session_or_pending_character(self):
        _m_session_naming.set_meta(self.db, "character_session_input:chat", json.dumps({"character_file": "Chosen.png", "character_name": "Chosen", "expires_at": time.time() + 600}))
        _m_session_naming.start_session_name_input(self.db, "token", "chat", self.session)
        self.assertTrue(_m_message_commands.handle_pending_input(self.db, "token", "chat", self.session, "/cancel", operation_id=44))
        self.assertEqual(len(_m_panel_callback_routes.list_sessions(self.db, "chat")), 1)
        self.assertEqual(_m_session_naming.get_meta(self.db, "character_session_input:chat", ""), "")

    def test_cancel_with_bot_mention_leaves_no_empty_session(self):
        _m_session_naming.start_session_name_input(self.db, "token", "chat|topic:7", self.session, kind="group")
        self.assertTrue(_m_message_commands.handle_pending_input(self.db, "token", "chat|topic:7", self.session, "/cancel@SillyTavernPunzmeBot", operation_id=47))
        self.assertEqual(_m_session_naming.get_meta(self.db, "session_name_input:chat|topic:7", ""), "")
        self.assertIn("New session cancelled.", self.sent[-1])

    def test_character_chain_applies_character_after_name(self):
        _m_session_naming.set_meta(self.db, "character_session_input:chat", json.dumps({"character_file": "Chosen.png", "character_name": "Chosen", "expires_at": time.time() + 600}))
        _m_session_naming.start_session_name_input(self.db, "token", "chat", self.session)
        original_safe = _m_character_identity.safe_character_path
        _m_character_identity.safe_character_path = lambda name: Path("/tmp/Chosen.png") if name == "Chosen.png" else None
        try:
            _m_message_commands.handle_pending_input(self.db, "token", "chat", self.session, "Chosen Story", operation_id=45)
        finally:
            _m_character_identity.safe_character_path = original_safe
        created = _m_memory_curator.load_session(self.db, "chat", "job-45", _m_memory_curator.DEFAULT_MODEL)
        self.assertEqual(created["title"], "Chosen Story")
        self.assertEqual(created["character_file"], "Chosen.png")

    def test_group_session_waits_for_name_then_opens_character_stage(self):
        chat_id = "chat|topic:7"
        session = _m_callbacks.ensure_session(self.db, chat_id, _m_memory_curator.DEFAULT_MODEL)
        old_menu = _m_session_naming.send_character_menu
        opened = []
        _m_session_naming.send_character_menu = lambda _token, _chat, character: opened.append(character)
        try:
            _m_session_naming.start_session_name_input(self.db, "token", chat_id, session, kind="group")
            self.assertEqual(len(_m_panel_callback_routes.list_sessions(self.db, chat_id)), 1)
            _m_message_commands.handle_pending_input(self.db, "token", chat_id, session, "Mystery Team", operation_id=46)
        finally:
            _m_session_naming.send_character_menu = old_menu
        created = _m_memory_curator.load_session(self.db, chat_id, "group-46", _m_memory_curator.DEFAULT_MODEL)
        self.assertEqual(created["title"], "Mystery Team")
        setup = _m_panel_callback_routes.group_setup_state(self.db, chat_id, "group-46")
        self.assertEqual(setup["stage"], "character")
        self.assertEqual(_m_sync_api.group_state(self.db, chat_id, "group-46")["title"], "Mystery Team")
        self.assertTrue(opened)

    def test_title_length_is_bounded(self):
        self.assertEqual(_m_session_naming.normalize_session_title(" A   name "), "A name")
        with self.assertRaises(ValueError):
            _m_session_naming.normalize_session_title("x" * 81)


if __name__ == "__main__":
    unittest.main()

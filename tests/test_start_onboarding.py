from application_test_setup import ensure_application_extensions, make_test_application_services, make_test_request_context

ensure_application_extensions()

from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import bridge.callbacks as _m_callbacks
import bridge.command_routes as _m_command_routes
import bridge.memory_curator as _m_memory_curator
import bridge.sync_core as _m_sync_core
class StartOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.session = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        self.fields = {
            "name": "Test Character",
            "first_mes": "Hello from the character.",
            "description": "",
            "personality": "",
            "scenario": "",
            "mes_example": "",
            "system_prompt": "",
            "post_history_instructions": "",
        }

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_slash_start_explains_optional_setup_without_greeting(self):
        sent = []
        original = _m_command_routes.send_text
        _m_command_routes.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        try:
            handled = _m_command_routes._handle_basic(
                self.db, "token", "key", _m_memory_curator.DEFAULT_MODEL, self.fields, "chat", "/start", "/start",
                self.session, self.session["session_id"], _m_memory_curator.DEFAULT_MODEL, "", "User", None, make_test_application_services(), request_context=make_test_request_context(self.db, self.session["session_id"]),
            )
        finally:
            _m_command_routes.send_text = original
        self.assertTrue(handled)
        self.assertEqual(len(sent), 1)
        self.assertIn("Persona, World Info, and System Prompt are optional", sent[0])
        self.assertIn("Type `start`", sent[0])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_plain_start_opens_greeting_choice_panel_without_storing_message(self):
        opened = []
        original_menu = _m_command_routes.send_greeting_menu
        _m_command_routes.send_greeting_menu = lambda _token, chat_id, fields, user_name, **_kwargs: opened.append((chat_id, fields["first_mes"], user_name)) or True
        try:
            handled = _m_command_routes._handle_basic(
                self.db, "token", "key", _m_memory_curator.DEFAULT_MODEL, self.fields, "chat", "start", "start",
                self.session, self.session["session_id"], _m_memory_curator.DEFAULT_MODEL, "", "User", None, make_test_application_services(), request_context=make_test_request_context(self.db, self.session["session_id"]),
            )
        finally:
            _m_command_routes.send_greeting_menu = original_menu
        self.assertTrue(handled)
        self.assertEqual(opened, [("chat", "Hello from the character.", "User")])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_slash_start_opens_greeting_choice_panel_when_all_setup_is_enabled(self):
        opened = []
        original_send = _m_command_routes.send_text
        original_menu = _m_command_routes.send_greeting_menu
        original_worlds = _m_command_routes.active_world_files
        _m_command_routes.send_text = lambda *_args, **_kwargs: self.fail("ready /start should open the greeting chooser")
        _m_command_routes.send_greeting_menu = lambda _token, chat_id, fields, user_name, **_kwargs: opened.append((chat_id, fields["first_mes"], user_name)) or True
        _m_command_routes.active_world_files = lambda _value: ["world.json"]
        ready_session = dict(self.session)
        ready_session.update({"persona_id": "punto.png", "world_file": "world.json", "system_prompt": "Prompt"})
        try:
            handled = _m_command_routes._handle_basic(
                self.db, "token", "key", _m_memory_curator.DEFAULT_MODEL, self.fields, "chat", "/start", "/start",
                ready_session, ready_session["session_id"], _m_memory_curator.DEFAULT_MODEL, "punto.png", "User", None, make_test_application_services(), request_context=make_test_request_context(self.db, self.session["session_id"]),
            )
        finally:
            _m_command_routes.send_text = original_send
            _m_command_routes.send_greeting_menu = original_menu
            _m_command_routes.active_world_files = original_worlds
        self.assertTrue(handled)
        self.assertEqual(opened, [("chat", "Hello from the character.", "User")])


if __name__ == "__main__":
    unittest.main()

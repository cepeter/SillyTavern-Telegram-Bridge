from application_test_setup import ensure_application_extensions, make_test_application_services

ensure_application_extensions()

import json
from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import random
import bridge.callbacks as _m_callbacks
import bridge.command_routes as _m_command_routes
import bridge.greetings as _m_greetings
import bridge.main as _m_main
import bridge.memory_curator as _m_memory_curator
import bridge.panel_callback_routes as _m_panel_callback_routes
class AlternateGreetingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.session = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_card_parser_preserves_native_alternate_greetings(self):
        fields = _m_main.card_fields({
            "data": {
                "name": "Test",
                "first_mes": "Primary {{user}}",
                "alternate_greetings": ["Alt one", "Alt two"],
            }
        })
        self.assertEqual(_m_greetings.greeting_options(fields), ["Primary {{user}}", "Alt one", "Alt two"])

    def test_first_greeting_can_choose_a_random_alternate(self):
        sent = []
        original_send = _m_greetings.send_text
        original_randrange = _m_greetings.random.randrange
        _m_greetings.send_text = lambda _token, _chat_id, text: sent.append(text) or [88]
        _m_greetings.random.randrange = lambda _length: 1
        try:
            fields = {"name": "Character", "first_mes": "Primary", "alternate_greetings": json.dumps(["Alt {{user}}"])}
            self.assertTrue(_m_greetings.send_character_greeting(self.db, "token", "chat", fields, self.session["session_id"], "User", None))
        finally:
            _m_greetings.send_text = original_send
            _m_greetings.random.randrange = original_randrange
        self.assertEqual(sent, ["Alt"])
        row = self.db.execute("SELECT role, content FROM messages").fetchone()
        self.assertEqual(tuple(row), ("assistant", "Alt User"))

    def test_greeting_menu_lists_default_alternates_and_preview(self):
        calls = []
        original_request = getattr(_m_greetings, "telegram_request", None)
        _m_greetings.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {"message_id": 55}
        fields = {
            "name": "Character",
            "first_mes": "Primary {{user}}",
            "alternate_greetings": json.dumps(["Alt one", "Alt two"]),
        }
        try:
            self.assertTrue(_m_greetings.send_greeting_menu("token", "chat", fields, "User"))
        finally:
            if original_request is None:
                delattr(_m_greetings, "telegram_request")
            else:
                _m_greetings.telegram_request = original_request
        self.assertEqual(calls[0][0], "sendMessage")
        payload = calls[0][1]
        self.assertIn("Selected: Default", payload["text"])
        self.assertIn("Primary User", payload["text"])
        callbacks = [
            button["callback_data"]
            for row in payload["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertIn("greeting:preview:0", callbacks)
        self.assertIn("greeting:preview:1", callbacks)
        self.assertIn("greeting:preview:2", callbacks)
        self.assertIn("greeting:use:0", callbacks)

    def test_greeting_callback_uses_selected_alternate_once(self):
        services = make_test_application_services()
        fields = {
            "name": "Character",
            "first_mes": "Primary",
            "alternate_greetings": json.dumps(["Alt one", "Alt {{user}}"]),
        }
        sent = []
        answers = []
        original_fields = _m_panel_callback_routes.card_fields_from_file
        original_send = _m_greetings.send_text
        original_close = _m_panel_callback_routes.close_panel_message
        _m_panel_callback_routes.card_fields_from_file = lambda _filename: fields
        _m_greetings.send_text = lambda _token, _chat_id, text: sent.append(text) or [99]
        _m_panel_callback_routes.close_panel_message = lambda *_args, **_kwargs: None
        callback = {
            "id": "cb",
            "from": {"id": "user"},
            "data": "greeting:use:2",
            "message": {"message_id": 77, "chat": {"id": "chat"}},
        }
        try:
            handled = _m_panel_callback_routes.handle_greeting_callback(
                self.db,
                "token",
                callback,
                lambda _token, _callback_id, text: answers.append(text),
                "greeting:use:2",
                "chat",
                callback["message"],
                self.session,
                self.session["session_id"],
                123,
                persona_service=services.persona,
            )
        finally:
            _m_panel_callback_routes.card_fields_from_file = original_fields
            _m_greetings.send_text = original_send
            _m_panel_callback_routes.close_panel_message = original_close
        self.assertTrue(handled)
        self.assertEqual(sent, ["Alt User"])
        self.assertIn("Started with Alternate 2", answers)
        rows = self.db.execute("SELECT role, content FROM messages").fetchall()
        self.assertEqual([tuple(row) for row in rows], [("assistant", "Alt")])
        self.assertTrue(_m_callbacks.is_session_scoped_panel_callback("greeting:preview:0"))


if __name__ == "__main__":
    unittest.main()

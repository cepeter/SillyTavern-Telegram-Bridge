import json
from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class AlternateGreetingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_card_parser_preserves_native_alternate_greetings(self):
        fields = rt.card_fields({
            "data": {
                "name": "Test",
                "first_mes": "Primary {{user}}",
                "alternate_greetings": ["Alt one", "Alt two"],
            }
        })
        self.assertEqual(rt.greeting_options(fields), ["Primary {{user}}", "Alt one", "Alt two"])

    def test_greeting_menu_lists_primary_and_alternates(self):
        fields = {"first_mes": "Primary", "alternate_greetings": json.dumps(["Alt one", "Alt two"])}
        markup = rt.greeting_menu_markup(fields)
        labels = [row[0]["text"] for row in markup["inline_keyboard"] if row and row[0]["callback_data"].startswith("greeting:") and row[0]["callback_data"] != "greeting:cancel"]
        self.assertEqual(labels, ["⭐ Greeting 1", "💬 Greeting 2", "💬 Greeting 3"])

    def test_selected_alternate_is_stored_as_assistant_turn(self):
        sent = []
        original_send = rt.send_text
        rt.send_text = lambda _token, _chat_id, text: sent.append(text) or [88]
        try:
            fields = {"name": "Character", "first_mes": "Primary", "alternate_greetings": json.dumps(["Alt {{user}}"])}
            self.assertTrue(rt.send_character_greeting(self.db, "token", "chat", fields, self.session["session_id"], "User", 1))
        finally:
            rt.send_text = original_send
        self.assertEqual(sent, ["Alt User"])
        row = self.db.execute("SELECT role, content FROM messages").fetchone()
        self.assertEqual(tuple(row), ("assistant", "Alt User"))


if __name__ == "__main__":
    unittest.main()

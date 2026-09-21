import json
from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import bridge.runtime as rt


class AlternateGreetingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
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

    def test_first_greeting_can_choose_a_random_alternate(self):
        sent = []
        original_send = rt.send_text
        original_randrange = rt.random.randrange
        rt.send_text = lambda _token, _chat_id, text: sent.append(text) or [88]
        rt.random.randrange = lambda _length: 1
        try:
            fields = {"name": "Character", "first_mes": "Primary", "alternate_greetings": json.dumps(["Alt {{user}}"])}
            self.assertTrue(rt.send_character_greeting(self.db, "token", "chat", fields, self.session["session_id"], "User", None))
        finally:
            rt.send_text = original_send
            rt.random.randrange = original_randrange
        self.assertEqual(sent, ["Alt User"])
        row = self.db.execute("SELECT role, content FROM messages").fetchone()
        self.assertEqual(tuple(row), ("assistant", "Alt User"))


if __name__ == "__main__":
    unittest.main()

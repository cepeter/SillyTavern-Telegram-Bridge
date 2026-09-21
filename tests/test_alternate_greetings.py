import json
from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import random
from dependency_patch import dependency_module

_m_callbacks = dependency_module("bridge.callbacks")
_m_command_routes = dependency_module("bridge.command_routes")
_m_greetings = dependency_module("bridge.greetings")
_m_main = dependency_module("bridge.main")
_m_memory_curator = dependency_module("bridge.memory_curator")


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
            self.assertTrue(_m_command_routes.send_character_greeting(self.db, "token", "chat", fields, self.session["session_id"], "User", None))
        finally:
            _m_greetings.send_text = original_send
            _m_greetings.random.randrange = original_randrange
        self.assertEqual(sent, ["Alt User"])
        row = self.db.execute("SELECT role, content FROM messages").fetchone()
        self.assertEqual(tuple(row), ("assistant", "Alt User"))


if __name__ == "__main__":
    unittest.main()

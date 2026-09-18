from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class StartOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
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
        original = rt.send_text
        rt.send_text = lambda _token, _chat_id, text: sent.append(text) or []
        try:
            handled = rt._handle_basic(
                self.db, "token", "key", rt.DEFAULT_MODEL, self.fields, "chat", "/start", "/start",
                self.session, self.session["session_id"], rt.DEFAULT_MODEL, "", "User", None,
            )
        finally:
            rt.send_text = original
        self.assertTrue(handled)
        self.assertEqual(len(sent), 1)
        self.assertIn("Persona, World Info, and System Prompt selection are optional", sent[0])
        self.assertIn("Type `start`", sent[0])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_plain_start_sends_and_stores_character_greeting(self):
        sent = []
        original = rt.send_text
        rt.send_text = lambda _token, _chat_id, text: sent.append(text) or [77]
        try:
            handled = rt._handle_basic(
                self.db, "token", "key", rt.DEFAULT_MODEL, self.fields, "chat", "start", "start",
                self.session, self.session["session_id"], rt.DEFAULT_MODEL, "", "User", None,
            )
        finally:
            rt.send_text = original
        self.assertTrue(handled)
        self.assertEqual(sent, ["Hello from the character."])
        row = self.db.execute("SELECT role, content FROM messages").fetchone()
        self.assertEqual(tuple(row), ("assistant", "Hello from the character."))


if __name__ == "__main__":
    unittest.main()

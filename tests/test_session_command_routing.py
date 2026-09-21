from pathlib import Path
import tempfile
import unittest

import bridge.config as config
from runtime_test_facade import runtime as rt


class SessionCommandRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = rt.db_connect()
        self.session = rt.create_session(self.db, "chat", "provider/model", session_id="active")
        rt.update_session(self.db, "chat", "active", character_file="missing.png")

    def tearDown(self):
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def test_session_command_routes_before_character_card_load(self):
        calls = []
        old_menu = rt.send_session_menu
        old_loader = rt.card_fields_from_file
        rt.send_session_menu = lambda _token, chat_id, sessions, active_id, *_args: calls.append((chat_id, sessions, active_id))
        rt.card_fields_from_file = lambda _name: (_ for _ in ()).throw(AssertionError("character loader must not run"))
        try:
            rt.process_message(self.db, "token", "key", "provider/model", {}, "chat", "/session", telegram_message_id=1)
        finally:
            rt.send_session_menu = old_menu
            rt.card_fields_from_file = old_loader
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "chat")
        self.assertEqual(calls[0][2], "active")


if __name__ == "__main__":
    unittest.main()

from application_test_setup import make_test_conversation_service
from application_test_setup import ensure_application_extensions, make_test_application_services

ensure_application_extensions()

from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.session_naming as _m_session_naming
import bridge.sync_api as _m_sync_api
class SessionCommandRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.session = _m_session_naming.create_session(self.db, "chat", "provider/model", session_id="active")
        _m_session_naming.update_session(self.db, "chat", "active", character_file="missing.png")

    def tearDown(self):
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def test_session_command_routes_before_character_card_load(self):
        calls = []
        old_menu = _m_message_commands.send_session_menu
        old_loader = _m_message_commands.card_fields_from_file
        _m_message_commands.send_session_menu = lambda _token, chat_id, sessions, active_id, *_args, **_kwargs: calls.append((chat_id, sessions, active_id))
        _m_message_commands.card_fields_from_file = lambda _name: (_ for _ in ()).throw(AssertionError("character loader must not run"))
        try:
            make_test_conversation_service().process_message(self.db, "token", "key", "provider/model", {}, "chat", "/session", telegram_message_id=1, services=make_test_application_services())
        finally:
            _m_message_commands.send_session_menu = old_menu
            _m_message_commands.card_fields_from_file = old_loader
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "chat")
        self.assertEqual(calls[0][2], "active")


if __name__ == "__main__":
    unittest.main()

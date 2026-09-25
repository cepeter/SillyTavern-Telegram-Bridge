from application_test_setup import (
    ensure_application_extensions,
    make_test_application_services,
    make_test_conversation_service,
)
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import tempfile
import unittest
from pathlib import Path

import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.session_naming as _m_session_naming


class SessionCommandRoutingTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _m_session_naming.create_session(
            self.db, "chat", "provider/model", session_id="active", app_settings=self.app_settings_builder.build()
        )
        _m_session_naming.update_session(self.db, "chat", "active", character_file="missing.png")

    def tearDown(self):
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def test_session_command_routes_before_character_card_load(self):
        calls = []
        old_menu = _m_message_commands.send_session_menu
        old_loader = _m_message_commands.card_fields_from_file
        _m_message_commands.send_session_menu = lambda _token, chat_id, sessions, active_id, *_args, **_kwargs: (
            calls.append((chat_id, sessions, active_id))
        )
        _m_message_commands.card_fields_from_file = lambda _name, *, app_settings=None: (_ for _ in ()).throw(
            AssertionError("character loader must not run")
        )
        try:
            make_test_conversation_service(app_settings=self.app_settings_builder.build()).process_message(
                self.db,
                "token",
                "key",
                "provider/model",
                {},
                "chat",
                "/session",
                telegram_message_id=1,
                services=make_test_application_services(app_settings=self.app_settings_builder.build()),
            )
        finally:
            _m_message_commands.send_session_menu = old_menu
            _m_message_commands.card_fields_from_file = old_loader
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "chat")
        self.assertEqual(calls[0][2], "active")


if __name__ == "__main__":
    unittest.main()

from application_test_setup import (
    ensure_application_extensions,
    make_test_application_services,
    make_test_provider_port,
    make_test_request_context,
)
from settings_test_support import SettingsTestCase

import bridge.session_core as _owner_session_core

ensure_application_extensions()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bridge.command_routes as _m_command_routes
import bridge.memory_curator as _m_memory_curator

PLACEHOLDER_MODEL = "provider-one::provider-one/model-a"
REAL_MODEL = "real-provider::real-model"


class StartOnboardingTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
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

    def _handle_start(
        self,
        command,
        current_model=REAL_MODEL,
        *,
        session=None,
        current_persona="",
        generate_backend=None,
    ):
        active_session = session or self.session
        services = make_test_application_services(
            provider=(
                make_test_provider_port(generate_backend=generate_backend) if generate_backend is not None else None
            ),
            app_settings=self.app_settings_builder.build(),
        )
        return _m_command_routes._handle_basic(
            self.db,
            "token",
            "key",
            current_model,
            self.fields,
            "chat",
            command,
            command,
            active_session,
            active_session["session_id"],
            current_model,
            current_persona,
            "User",
            None,
            request_context=make_test_request_context(
                self.db, active_session["session_id"], app_settings=self.app_settings_builder.build()
            ),
            conversation_service=services.conversation,
            delivery_port=services.delivery,
            group_service=services.group,
            memory_service=services.memory,
            provider_port=services.provider,
        )

    def test_slash_start_opens_card_greeting_without_provider_probe(self):
        generate = Mock(side_effect=AssertionError("/start must not call a story model"))
        with patch.object(_m_command_routes, "send_greeting_menu", return_value=True) as chooser:
            self.assertTrue(self._handle_start("/start", generate_backend=generate))
        generate.assert_not_called()
        chooser.assert_called_once()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_slash_start_does_not_require_provider_credentials_for_card_greeting(self):
        generate = Mock(side_effect=RuntimeError("Missing provider credential"))
        with patch.object(_m_command_routes, "send_greeting_menu", return_value=True) as chooser:
            self.assertTrue(self._handle_start("/start", PLACEHOLDER_MODEL, generate_backend=generate))
        generate.assert_not_called()
        chooser.assert_called_once()

    def test_slash_start_with_optional_setup_off_still_opens_chooser(self):
        with patch.object(_m_command_routes, "send_greeting_menu", return_value=True) as chooser:
            self.assertTrue(self._handle_start("/start", current_persona=""))
        chooser.assert_called_once()

    def test_plain_start_is_not_a_command_alias(self):
        generate = Mock()
        with patch.object(_m_command_routes, "send_greeting_menu") as chooser:
            self.assertFalse(self._handle_start("start", generate_backend=generate))
        generate.assert_not_called()
        chooser.assert_not_called()

    def test_started_session_cannot_reopen_start_chooser(self):
        from bridge.conversation_lifecycle import mark_started

        mark_started(self.db, "chat", self.session["session_id"], 0)
        sent = []
        with (
            patch.object(_m_command_routes, "send_greeting_menu") as chooser,
            patch.object(_m_command_routes, "send_text", side_effect=lambda *a: sent.append(a[2]) or []),
        ):
            self.assertTrue(self._handle_start("/start"))
        chooser.assert_not_called()
        self.assertEqual(sent, ["This session has already started."])

    def test_light_novel_has_a_dedicated_command(self):
        with patch.object(_m_command_routes, "send_light_novel_menu") as menu:
            self.assertTrue(self._handle_start("/lightnovel"))
        menu.assert_called_once()


if __name__ == "__main__":
    unittest.main()

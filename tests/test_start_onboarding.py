from application_test_setup import (
    ensure_application_extensions,
    make_test_application_services,
    make_test_request_context,
)

ensure_application_extensions()

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bridge.config as config
import bridge.callbacks as _m_callbacks
import bridge.command_routes as _m_command_routes
import bridge.memory_curator as _m_memory_curator


PLACEHOLDER_MODEL = "provider-one::provider-one/model-a"
REAL_MODEL = "real-provider::real-model"


class StartOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.session = _m_callbacks.ensure_session(
            self.db, "chat", _m_memory_curator.DEFAULT_MODEL
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
    ):
        active_session = session or self.session
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
            make_test_application_services(),
            request_context=make_test_request_context(
                self.db, active_session["session_id"]
            ),
        )

    def test_slash_start_rejects_installation_placeholder_before_probe(self):
        sent = []
        with patch.object(
            _m_command_routes, "generate_text"
        ) as generate, patch.object(
            _m_command_routes,
            "send_text",
            side_effect=lambda _token, _chat_id, text: sent.append(text) or [],
        ):
            handled = self._handle_start("/start", PLACEHOLDER_MODEL)
        self.assertTrue(handled)
        generate.assert_not_called()
        self.assertEqual(
            sent, ["please set your model first in /providers command"]
        )

    def test_slash_start_reports_model_probe_error_and_stops(self):
        sent = []
        with patch.object(
            _m_command_routes,
            "generate_text",
            side_effect=RuntimeError(
                "Missing provider credential: PROVIDER_ONE_API_KEY"
            ),
        ) as generate, patch.object(
            _m_command_routes,
            "send_text",
            side_effect=lambda _token, _chat_id, text: sent.append(text) or [],
        ), patch.object(
            _m_command_routes, "send_greeting_menu"
        ) as greeting:
            handled = self._handle_start("/start")

        self.assertTrue(handled)
        generate.assert_called_once()
        greeting.assert_not_called()
        self.assertEqual(
            sent,
            ["Model check failed: Missing provider credential: PROVIDER_ONE_API_KEY"],
        )

    def test_slash_start_limits_probe_timeout_and_reports_timeout(self):
        sent = []
        with patch.object(
            _m_command_routes,
            "generate_text",
            side_effect=TimeoutError("timed out"),
        ) as generate, patch.object(
            _m_command_routes,
            "send_text",
            side_effect=lambda _token, _chat_id, text: sent.append(text) or [],
        ), patch.object(
            _m_command_routes, "send_greeting_menu"
        ) as greeting:
            handled = self._handle_start("/start")

        self.assertTrue(handled)
        generate.assert_called_once()
        self.assertEqual(generate.call_args.kwargs["request_timeout"], 30)
        greeting.assert_not_called()
        self.assertEqual(sent, ["Model check failed: timed out"])

    def test_slash_start_probes_model_then_explains_optional_setup(self):
        sent = []
        with patch.object(
            _m_command_routes,
            "generate_text",
            return_value="OK",
        ) as generate, patch.object(
            _m_command_routes,
            "send_text",
            side_effect=lambda _token, _chat_id, text: sent.append(text) or [],
        ):
            handled = self._handle_start("/start")

        self.assertTrue(handled)
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[1], REAL_MODEL)
        self.assertEqual(len(sent), 1)
        self.assertIn(
            "Persona, World Info, and System Prompt are optional", sent[0]
        )
        self.assertIn("Type `start`", sent[0])
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0
        )

    def test_plain_start_probes_model_then_opens_greeting_choice(self):
        opened = []
        with patch.object(
            _m_command_routes, "generate_text", return_value="OK"
        ) as generate, patch.object(
            _m_command_routes,
            "send_greeting_menu",
            side_effect=lambda _token, chat_id, fields, user_name, **_kwargs: (
                opened.append((chat_id, fields["first_mes"], user_name)) or True
            ),
        ):
            handled = self._handle_start("start")

        self.assertTrue(handled)
        generate.assert_called_once()
        self.assertEqual(opened, [("chat", "Hello from the character.", "User")])
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0
        )

    def test_slash_start_probes_model_then_opens_greeting_when_setup_ready(self):
        opened = []
        ready_session = dict(self.session)
        ready_session.update(
            {
                "persona_id": "punto.png",
                "world_file": "world.json",
                "system_prompt": "Prompt",
            }
        )
        with patch.object(
            _m_command_routes, "generate_text", return_value="OK"
        ) as generate, patch.object(
            _m_command_routes, "active_world_files", return_value=["world.json"]
        ), patch.object(
            _m_command_routes,
            "send_text",
            side_effect=AssertionError(
                "ready /start should open the greeting chooser"
            ),
        ), patch.object(
            _m_command_routes,
            "send_greeting_menu",
            side_effect=lambda _token, chat_id, fields, user_name, **_kwargs: (
                opened.append((chat_id, fields["first_mes"], user_name)) or True
            ),
        ):
            handled = self._handle_start(
                "/start",
                session=ready_session,
                current_persona="punto.png",
            )

        self.assertTrue(handled)
        generate.assert_called_once()
        self.assertEqual(opened, [("chat", "Hello from the character.", "User")])


if __name__ == "__main__":
    unittest.main()

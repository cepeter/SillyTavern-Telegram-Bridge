from application_test_setup import (
    ensure_application_extensions,
    make_test_conversation_service,
    make_test_group_service,
    make_test_input_flow_service,
    make_test_memory_service,
    make_test_persona_service,
    make_test_provider_port,
    make_test_request_context,
)
from settings_test_support import SettingsTestCase

import bridge.session_core as _owner_session_core

ensure_application_extensions()

import json
import tempfile
import time
import unittest
from pathlib import Path

import bridge.command_routes as _m_command_routes
import bridge.input_flows as _m_input_flows
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.session_naming as _m_session_naming
import bridge.session_titles as _m_session_titles
import bridge.sync_api as _m_sync_api


class SessionNamingTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.group = make_test_group_service(app_settings=self.app_settings_builder.build())
        self.session = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        self.sent = []
        self.old_session_send = _m_session_naming.send_text
        self.old_pending_send = _m_message_commands.send_text
        self.old_route_send = _m_command_routes.send_text

        def send_stub(_token, _chat, text):
            return self.sent.append(text) or []

        _m_session_naming.send_text = send_stub
        _m_message_commands.send_text = send_stub
        _m_command_routes.send_text = send_stub

    def tearDown(self):
        _m_session_naming.send_text = self.old_session_send
        _m_message_commands.send_text = self.old_pending_send
        _m_command_routes.send_text = self.old_route_send
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def _state(self, chat_id="chat"):
        return json.loads(_m_session_naming.get_meta(self.db, f"session_name_input:{chat_id}", "{}"))

    def test_standard_session_is_created_only_after_valid_name(self):
        _m_session_naming.start_session_name_input(
            self.db,
            "token",
            "chat",
            self.session,
            group_service=self.group,
            app_settings=self.app_settings_builder.build(),
        )
        self.assertEqual(len(_m_panel_callback_routes.list_sessions(self.db, "chat")), 1)
        self.assertTrue(
            make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                self.db,
                "token",
                "chat",
                self.session,
                "  Project   Alpha  ",
                operation_id=42,
                group_service=self.group,
                provider_port=make_test_provider_port(),
                memory_service=make_test_memory_service(),
                persona_service=make_test_persona_service(),
                request_context=make_test_request_context(
                    self.db, self.session["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
        )
        created = _m_memory_curator.load_session(
            self.db,
            "chat",
            "job-42",
            self.app_settings_builder.default_model,
            app_settings=self.app_settings_builder.build(),
        )
        self.assertEqual(created["title"], "Project Alpha")
        self.assertEqual(_m_session_naming.get_meta(self.db, "active_session:chat", ""), "job-42")
        self.assertEqual(_m_session_naming.get_meta(self.db, "session_name_input:chat", ""), "")

    def test_status_displays_custom_session_title_with_technical_id(self):
        _m_session_naming.update_session(self.db, "chat", self.session["session_id"], title="Evening Story")
        original_card = _m_message_commands.card_fields_from_file
        _m_message_commands.card_fields_from_file = lambda _filename, *, app_settings=None: {
            "name": "Test",
            "post_history_instructions": "",
        }
        try:
            make_test_conversation_service(app_settings=self.app_settings_builder.build()).process_message(
                self.db,
                "token",
                "key",
                self.app_settings_builder.default_model,
                {},
                "chat",
                "/status",
            )
        finally:
            _m_message_commands.card_fields_from_file = original_card
        self.assertIn("📊 Session status", self.sent[-1])
        self.assertIn("🗂️ Session: Evening Story (default)", self.sent[-1])
        self.assertIn("• System Prompt: off", self.sent[-1])

    def test_invalid_name_reprompts_without_creating_session(self):
        _m_session_naming.start_session_name_input(
            self.db,
            "token",
            "chat",
            self.session,
            group_service=self.group,
            app_settings=self.app_settings_builder.build(),
        )
        self.assertTrue(
            make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                self.db,
                "token",
                "chat",
                self.session,
                "/bad",
                operation_id=43,
                group_service=self.group,
                provider_port=make_test_provider_port(),
                memory_service=make_test_memory_service(),
                persona_service=make_test_persona_service(),
                request_context=make_test_request_context(
                    self.db, self.session["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
        )
        self.assertEqual(len(_m_panel_callback_routes.list_sessions(self.db, "chat")), 1)
        self.assertTrue(self._state())
        self.assertIn("cannot start with /", self.sent[-1])

    def test_cancel_leaves_no_empty_session_or_pending_character(self):
        _m_session_naming.set_meta(
            self.db,
            "character_session_input:chat",
            json.dumps({"character_file": "Chosen.png", "character_name": "Chosen", "expires_at": time.time() + 600}),
        )
        _m_session_naming.start_session_name_input(
            self.db,
            "token",
            "chat",
            self.session,
            group_service=self.group,
            app_settings=self.app_settings_builder.build(),
        )
        self.assertTrue(
            make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                self.db,
                "token",
                "chat",
                self.session,
                "/cancel",
                operation_id=44,
                group_service=self.group,
                provider_port=make_test_provider_port(),
                memory_service=make_test_memory_service(),
                persona_service=make_test_persona_service(),
                request_context=make_test_request_context(
                    self.db, self.session["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
        )
        self.assertEqual(len(_m_panel_callback_routes.list_sessions(self.db, "chat")), 1)
        self.assertEqual(_m_session_naming.get_meta(self.db, "character_session_input:chat", ""), "")

    def test_cancel_with_bot_mention_leaves_no_empty_session(self):
        _m_session_naming.start_session_name_input(
            self.db,
            "token",
            "chat|topic:7",
            self.session,
            kind="group",
            group_service=self.group,
            app_settings=self.app_settings_builder.build(),
        )
        self.assertTrue(
            make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                self.db,
                "token",
                "chat|topic:7",
                self.session,
                "/cancel@SillyTavernPunzmeBot",
                operation_id=47,
                group_service=self.group,
                provider_port=make_test_provider_port(),
                memory_service=make_test_memory_service(),
                persona_service=make_test_persona_service(),
                request_context=make_test_request_context(
                    self.db, self.session["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
        )
        self.assertEqual(_m_session_naming.get_meta(self.db, "session_name_input:chat|topic:7", ""), "")
        self.assertIn("New session cancelled.", self.sent[-1])

    def test_character_chain_applies_character_after_name(self):
        _m_session_naming.set_meta(
            self.db,
            "character_session_input:chat",
            json.dumps({"character_file": "Chosen.png", "character_name": "Chosen", "expires_at": time.time() + 600}),
        )
        _m_session_naming.start_session_name_input(
            self.db,
            "token",
            "chat",
            self.session,
            group_service=self.group,
            app_settings=self.app_settings_builder.build(),
        )
        original_safe = _m_input_flows.safe_character_path
        _m_input_flows.safe_character_path = lambda name, *, app_settings=None: (
            Path("/tmp/Chosen.png") if name == "Chosen.png" else None
        )
        try:
            make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                self.db,
                "token",
                "chat",
                self.session,
                "Chosen Story",
                operation_id=45,
                group_service=self.group,
                provider_port=make_test_provider_port(),
                memory_service=make_test_memory_service(),
                persona_service=make_test_persona_service(),
                request_context=make_test_request_context(
                    self.db, self.session["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            _m_input_flows.safe_character_path = original_safe
        created = _m_memory_curator.load_session(
            self.db,
            "chat",
            "job-45",
            self.app_settings_builder.default_model,
            app_settings=self.app_settings_builder.build(),
        )
        self.assertEqual(created["title"], "Chosen Story")
        self.assertEqual(created["character_file"], "Chosen.png")

    def test_group_session_waits_for_name_then_opens_character_stage(self):
        chat_id = "chat|topic:7"
        session = _owner_session_core.ensure_session(
            self.db, chat_id, self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        old_menu = _m_session_naming.send_character_menu
        opened = []
        _m_session_naming.send_character_menu = lambda _token, _chat, character, **kwargs: opened.append(
            (character, kwargs["request_context"].session_id)
        )
        try:
            _m_session_naming.start_session_name_input(
                self.db,
                "token",
                chat_id,
                session,
                kind="group",
                group_service=self.group,
                app_settings=self.app_settings_builder.build(),
            )
            self.assertEqual(len(_m_panel_callback_routes.list_sessions(self.db, chat_id)), 1)
            make_test_input_flow_service(app_settings=self.app_settings_builder.build()).handle_pending(
                self.db,
                "token",
                chat_id,
                session,
                "Mystery Team",
                operation_id=46,
                group_service=self.group,
                provider_port=make_test_provider_port(),
                memory_service=make_test_memory_service(),
                persona_service=make_test_persona_service(),
                request_context=make_test_request_context(
                    self.db, session["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            _m_session_naming.send_character_menu = old_menu
        created = _m_memory_curator.load_session(
            self.db,
            chat_id,
            "group-46",
            self.app_settings_builder.default_model,
            app_settings=self.app_settings_builder.build(),
        )
        self.assertEqual(created["title"], "Mystery Team")
        setup = self.group.setup_state(self.db, chat_id, "group-46")
        self.assertEqual(setup["stage"], "character")
        self.assertEqual(_m_sync_api.group_state(self.db, chat_id, "group-46")["title"], "Mystery Team")
        self.assertEqual(opened, [(created["character_file"], "group-46")])

    def test_title_length_is_bounded(self):
        self.assertEqual(_m_session_titles.normalize_session_title(" A   name "), "A name")
        with self.assertRaises(ValueError):
            _m_session_titles.normalize_session_title("x" * 81)


if __name__ == "__main__":
    unittest.main()

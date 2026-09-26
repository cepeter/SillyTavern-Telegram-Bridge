from unittest.mock import patch

from application_test_setup import (
    ensure_application_extensions,
    make_test_application_services,
    make_test_delivery_port,
    make_test_memory_service,
    make_test_rag_service,
    make_test_request_context,
)
from settings_test_support import SettingsTestCase

import bridge.command_panels as _command_panels
import bridge.provider_discovery as _owner_provider_discovery
import bridge.world_panels as _owner_world_panels
from bridge import feature_panels, group_panels, prompt_panels, provider_panels, text_action_input, world_panels

ensure_application_extensions()

import tempfile
import unittest
from pathlib import Path

import bridge.cards as _m_cards
import bridge.command_routes as _m_command_routes
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.session_naming as _m_session_naming


class PanelificationTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _m_session_naming.create_session(
            self.db,
            "chat",
            self.app_settings_builder.default_model,
            session_id="panel",
            app_settings=self.app_settings_builder.build(),
        )
        self.calls = []
        self.old_card = _m_message_commands.card_fields_from_file
        self.old_groups = _owner_provider_discovery.get_model_groups

        def panel_stub(*args, **kwargs):
            return self.calls.append((args, kwargs))

        for owner in (_m_cards, prompt_panels, feature_panels, provider_panels, world_panels, group_panels):
            patcher = patch.object(owner, "send_panel_message", side_effect=panel_stub)
            patcher.start()
            self.addCleanup(patcher.stop)
        _m_message_commands.card_fields_from_file = lambda _filename, *, app_settings=None: {"name": "Test"}
        _owner_provider_discovery.get_model_groups = lambda *, app_settings=None: {}

    def tearDown(self):
        _m_message_commands.card_fields_from_file = self.old_card
        _owner_provider_discovery.get_model_groups = self.old_groups
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def _route(self, text, chat_id="chat", session=None):
        session = session or self.session
        return _m_command_routes.handle_command_route(
            self.db,
            "token",
            "",
            self.app_settings_builder.default_model,
            {"name": "Test"},
            chat_id,
            text,
            text.casefold(),
            session,
            session["session_id"],
            self.app_settings_builder.default_model,
            session.get("persona_id") or "",
            "user",
            request_context=make_test_request_context(
                self.db, session["session_id"], app_settings=self.app_settings_builder.build()
            ),
            conversation_service=make_test_application_services(
                memory=make_test_memory_service(),
                delivery=make_test_delivery_port(
                    send_panel_request=lambda *args, **kwargs: self.calls.append((args, kwargs)) or {},
                ),
                app_settings=self.app_settings_builder.build(),
            ).conversation,
            delivery_port=make_test_application_services(
                memory=make_test_memory_service(),
                delivery=make_test_delivery_port(
                    send_panel_request=lambda *args, **kwargs: self.calls.append((args, kwargs)) or {},
                ),
                app_settings=self.app_settings_builder.build(),
            ).delivery,
            group_service=make_test_application_services(
                memory=make_test_memory_service(),
                delivery=make_test_delivery_port(
                    send_panel_request=lambda *args, **kwargs: self.calls.append((args, kwargs)) or {},
                ),
                app_settings=self.app_settings_builder.build(),
            ).group,
            memory_service=make_test_application_services(
                memory=make_test_memory_service(),
                delivery=make_test_delivery_port(
                    send_panel_request=lambda *args, **kwargs: self.calls.append((args, kwargs)) or {},
                ),
                app_settings=self.app_settings_builder.build(),
            ).memory,
            persona_service=make_test_application_services(
                memory=make_test_memory_service(),
                delivery=make_test_delivery_port(
                    send_panel_request=lambda *args, **kwargs: self.calls.append((args, kwargs)) or {},
                ),
                app_settings=self.app_settings_builder.build(),
            ).persona,
            provider_port=make_test_application_services(
                memory=make_test_memory_service(),
                delivery=make_test_delivery_port(
                    send_panel_request=lambda *args, **kwargs: self.calls.append((args, kwargs)) or {},
                ),
                app_settings=self.app_settings_builder.build(),
            ).provider,
            sync_service=make_test_application_services(
                memory=make_test_memory_service(),
                delivery=make_test_delivery_port(
                    send_panel_request=lambda *args, **kwargs: self.calls.append((args, kwargs)) or {},
                ),
                app_settings=self.app_settings_builder.build(),
            ).sync,
            rag_service=make_test_rag_service(),
        )

    def test_character_panel_has_rank_name_and_action_columns(self):
        old_paths = _m_cards.character_card_paths
        old_display = _m_cards.character_display_name
        old_callback_token = _m_cards.dynamic_callback_token
        old_rank = _m_cards.character_rank
        _m_cards.character_card_paths = lambda *, app_settings=None: [Path("active.png"), Path("other.png")]
        _m_cards.character_display_name = lambda path, *, app_settings=None: path.stem
        _m_cards.dynamic_callback_token = lambda _kind, filename, _chat, **_kwargs: "cb-" + filename
        _m_cards.character_rank = lambda _db, filename, **_kwargs: "S" if filename == "active.png" else ""
        settings = self.app_settings_builder.build()
        try:
            _m_session_naming.send_character_menu(
                "bot-token",
                "chat",
                "active.png",
                request_context=make_test_request_context(self.db, app_settings=settings),
            )
        finally:
            _m_cards.character_card_paths = old_paths
            _m_cards.character_display_name = old_display
            _m_cards.dynamic_callback_token = old_callback_token
            _m_cards.character_rank = old_rank
        rows = self.calls[0][0][3]["inline_keyboard"]
        item_rows = rows[:2]
        self.assertTrue(all(len(row) == 3 for row in item_rows))
        self.assertEqual(item_rows[0][0]["text"], "S")
        self.assertEqual(item_rows[0][0]["callback_data"], "character:rank:S")
        self.assertEqual(item_rows[0][0]["icon_custom_emoji_id"], "6176891226302718197")
        self.assertEqual(item_rows[1][0], {"text": "—", "callback_data": "character:rank:unranked"})
        self.assertEqual(item_rows[0][1]["callback_data"], "character:cb-active.png")
        self.assertEqual(item_rows[1][1]["callback_data"], "character:cb-other.png")
        callbacks = [button["callback_data"] for row in item_rows for button in row]
        self.assertEqual(sum(value.startswith("characterdelete:") for value in callbacks), 1)
        self.assertIn("character:protected", callbacks)

        for command, marker in (("/prompt", "prompt:budget"), ("/summarize", "summary:confirm")):
            self.calls.clear()
            self.assertTrue(self._route(command))
            self.assertIn(marker, str(self.calls[-1]))

    def test_character_rank_buttons_use_hardcoded_custom_emoji_ids(self):
        expected = {
            "S": "6176891226302718197",
            "A": "6176955294329871952",
            "B": "6178981105849344346",
            "C": "6177219258724917819",
            "D": "6176733025477337215",
        }
        for tier, custom_emoji_id in expected.items():
            self.assertEqual(
                _m_cards.character_rank_button(tier),
                {
                    "text": tier,
                    "callback_data": f"character:rank:{tier}",
                    "icon_custom_emoji_id": custom_emoji_id,
                },
            )
        self.assertEqual(
            _m_cards.character_rank_button(None),
            {"text": "—", "callback_data": "character:rank:unranked"},
        )

    def test_character_rank_button_has_no_configuration_parameter(self):
        import inspect

        self.assertEqual(list(inspect.signature(_m_cards.character_rank_button).parameters), ["rank"])

    def test_scene_and_director_goal_open_topic_panels(self):
        topic_id = "chat|topic:1"
        topic_session = _m_session_naming.create_session(
            self.db,
            topic_id,
            self.app_settings_builder.default_model,
            session_id="topic-panel",
            app_settings=self.app_settings_builder.build(),
        )
        self.assertTrue(self._route("/scene", topic_id, topic_session))
        self.assertIn("scene:refresh", str(self.calls[-1]))
        self.calls.clear()
        self.assertTrue(self._route("/group goal", topic_id, topic_session))
        self.assertIn("goal:set", str(self.calls[-1]))

    def test_typed_search_group_and_inline_text_forms_preserve_payloads(self):
        memory_calls = []
        group_calls = []
        macro_calls = []
        old_memory = _command_panels.handle_memory_command
        old_group = _command_panels.handle_group_command
        old_macro = text_action_input.handle_macro_command
        _command_panels.handle_memory_command = lambda *args, app_settings=None, **_kwargs: memory_calls.append(
            args[-1]
        )
        _command_panels.handle_group_command = lambda *args, **_kwargs: group_calls.append(args[4])
        text_action_input.handle_macro_command = lambda *args, **_kwargs: macro_calls.append(args[-1])
        try:
            self.assertTrue(self._route("/memory search hidden fact"))
            self.assertEqual(memory_calls, ["/memory search hidden fact"])
            self.assertTrue(
                self._route(
                    "/group add Mira",
                    "chat|topic:1",
                    _m_session_naming.create_session(
                        self.db,
                        "chat|topic:1",
                        self.app_settings_builder.default_model,
                        session_id="group",
                        app_settings=self.app_settings_builder.build(),
                    ),
                )
            )
            self.assertEqual(group_calls, ["/group add Mira"])
            self.assertTrue(self._route("/macro {{char}} waves"))
            self.assertEqual(macro_calls, ["/macro {{char}} waves"])
        finally:
            _command_panels.handle_memory_command = old_memory
            _command_panels.handle_group_command = old_group
            text_action_input.handle_macro_command = old_macro

    def test_world_menu_keeps_bot_token_for_telegram_request(self):
        old_world_paths = _owner_world_panels.world_file_paths
        old_active_worlds = _owner_world_panels.active_world_files
        old_callback_token = _owner_world_panels.dynamic_callback_token
        _owner_world_panels.world_file_paths = lambda *, app_settings=None: [Path("lore.json")]
        _owner_world_panels.active_world_files = lambda _current, *, app_settings=None: []
        _owner_world_panels.dynamic_callback_token = lambda _kind, _name, _chat, **_kwargs: "callback-token"
        try:
            _owner_world_panels.send_world_menu(
                "bot-token",
                "chat",
                "",
                request_context=make_test_request_context(self.db, app_settings=self.app_settings_builder.build()),
            )
        finally:
            _owner_world_panels.world_file_paths = old_world_paths
            _owner_world_panels.active_world_files = old_active_worlds
            _owner_world_panels.dynamic_callback_token = old_callback_token
        self.assertEqual(self.calls[0][0][0], "bot-token")
        payload = self.calls[0][0][3]
        self.assertEqual(payload["inline_keyboard"][0][0]["callback_data"], "world:callback-token")


if __name__ == "__main__":
    unittest.main()

from application_test_setup import ensure_application_extensions, make_test_application_services, make_test_memory_service, make_test_request_context

ensure_application_extensions()

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

import bridge.config as config
import bridge.catalog as _m_catalog
import bridge.cards as _m_cards
import bridge.groups as _m_groups
import bridge.input_flows as _m_input_flows
import bridge.status_panels as _m_status_panels
import bridge.character_identity as _m_character_identity
import bridge.command_routes as _m_command_routes
import bridge.commands as _m_commands
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.session_naming as _m_session_naming
import bridge.sync_api as _m_sync_api
import bridge.sync_core as _m_sync_core
class PanelificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.session = _m_session_naming.create_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL, session_id="panel")
        self.calls = []
        self.old_panels = {
            "cards": _m_cards.send_panel_message,
            "status": _m_status_panels.send_panel_message,
            "catalog": _m_catalog.send_panel_message,
            "groups": _m_groups.send_panel_message,
        }
        self.old_card = _m_message_commands.card_fields_from_file
        self.old_groups = _m_catalog.get_model_groups
        panel_stub = lambda *args, **kwargs: self.calls.append((args, kwargs))
        _m_cards.send_panel_message = panel_stub
        _m_status_panels.send_panel_message = panel_stub
        _m_catalog.send_panel_message = panel_stub
        _m_groups.send_panel_message = panel_stub
        _m_message_commands.card_fields_from_file = lambda _filename: {"name": "Test"}
        _m_catalog.get_model_groups = lambda: {}

    def tearDown(self):
        _m_cards.send_panel_message = self.old_panels["cards"]
        _m_status_panels.send_panel_message = self.old_panels["status"]
        _m_catalog.send_panel_message = self.old_panels["catalog"]
        _m_groups.send_panel_message = self.old_panels["groups"]
        _m_message_commands.card_fields_from_file = self.old_card
        _m_catalog.get_model_groups = self.old_groups
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def _route(self, text, chat_id="chat", session=None):
        session = session or self.session
        return _m_command_routes.handle_command_route(
            self.db,
            "token",
            "",
            _m_memory_curator.DEFAULT_MODEL,
            {"name": "Test"},
            chat_id,
            text,
            text.casefold(),
            session,
            session["session_id"],
            _m_memory_curator.DEFAULT_MODEL,
            session.get("persona_id") or "",
            "user",
            request_context=make_test_request_context(self.db, session["session_id"]),
            services=make_test_application_services(memory=make_test_memory_service()),
        )

    def test_character_panel_has_inline_delete_actions(self):
        old_paths = _m_cards.character_card_paths
        old_display = _m_cards.character_display_name
        old_callback_token = _m_cards.dynamic_callback_token
        _m_cards.character_card_paths = lambda: [Path("active.png"), Path("other.png")]
        _m_cards.character_display_name = lambda path: path.stem
        _m_cards.dynamic_callback_token = lambda _kind, filename, _chat, **_kwargs: "cb-" + filename
        try:
            _m_session_naming.send_character_menu("bot-token", "chat", "active.png", request_context=make_test_request_context(self.db))
        finally:
            _m_cards.character_card_paths = old_paths
            _m_cards.character_display_name = old_display
            _m_cards.dynamic_callback_token = old_callback_token
        rows = self.calls[0][0][3]["inline_keyboard"]
        item_rows = rows[:2]
        self.assertTrue(all(len(row) == 2 for row in item_rows))
        callbacks = [button["callback_data"] for row in item_rows for button in row]
        self.assertEqual(sum(value.startswith("characterdelete:") for value in callbacks), 1)
        self.assertIn("character:protected", callbacks)
        self.assertIn("character:cb-active.png", callbacks)
        self.assertIn("character:cb-other.png", callbacks)

        for command, marker in (("/prompt", "prompt:budget"), ("/summarize", "summary:confirm")):
            self.calls.clear()
            self.assertTrue(self._route(command))
            self.assertIn(marker, str(self.calls[-1]))

    def test_scene_and_director_goal_open_topic_panels(self):
        topic_id = "chat|topic:1"
        topic_session = _m_session_naming.create_session(self.db, topic_id, _m_memory_curator.DEFAULT_MODEL, session_id="topic-panel")
        self.assertTrue(self._route("/scene", topic_id, topic_session))
        self.assertIn("scene:refresh", str(self.calls[-1]))
        self.calls.clear()
        self.assertTrue(self._route("/group goal", topic_id, topic_session))
        self.assertIn("goal:set", str(self.calls[-1]))

    def test_typed_search_group_and_inline_text_forms_preserve_payloads(self):
        memory_calls = []
        group_calls = []
        macro_calls = []
        old_memory = _m_command_routes.handle_memory_command
        old_group = _m_command_routes.handle_group_command
        old_macro = _m_input_flows.handle_macro_command
        _m_command_routes.handle_memory_command = lambda *args: memory_calls.append(args[-1])
        _m_command_routes.handle_group_command = lambda *args: group_calls.append(args[4])
        _m_input_flows.handle_macro_command = lambda *args: macro_calls.append(args[-1])
        try:
            self.assertTrue(self._route("/memory search hidden fact"))
            self.assertEqual(memory_calls, ["/memory search hidden fact"])
            self.assertTrue(self._route("/group add Mira", "chat|topic:1", _m_session_naming.create_session(self.db, "chat|topic:1", _m_memory_curator.DEFAULT_MODEL, session_id="group")))
            self.assertEqual(group_calls, ["/group add Mira"])
            self.assertTrue(self._route("/macro {{char}} waves"))
            self.assertEqual(macro_calls, ["/macro {{char}} waves"])
        finally:
            _m_command_routes.handle_memory_command = old_memory
            _m_command_routes.handle_group_command = old_group
            _m_input_flows.handle_macro_command = old_macro
    def test_world_menu_keeps_bot_token_for_telegram_request(self):
        old_world_paths = _m_catalog.world_file_paths
        old_active_worlds = _m_catalog.active_world_files
        old_callback_token = _m_catalog.dynamic_callback_token
        _m_catalog.world_file_paths = lambda: [Path("lore.json")]
        _m_catalog.active_world_files = lambda _current: []
        _m_catalog.dynamic_callback_token = lambda _kind, _name, _chat, **_kwargs: "callback-token"
        try:
            _m_panel_callback_routes.send_world_menu("bot-token", "chat", "", request_context=make_test_request_context(self.db))
        finally:
            _m_catalog.world_file_paths = old_world_paths
            _m_catalog.active_world_files = old_active_worlds
            _m_catalog.dynamic_callback_token = old_callback_token
        self.assertEqual(self.calls[0][0][0], "bot-token")
        payload = self.calls[0][0][3]
        self.assertEqual(payload["inline_keyboard"][0][0]["callback_data"], "world:callback-token")


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import tempfile
import unittest

import bridge.config as config
from runtime_test_facade import runtime as rt


class PanelificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = rt.db_connect()
        self.session = rt.create_session(self.db, "chat", rt.DEFAULT_MODEL, session_id="panel")
        self.calls = []
        self.old_panel = rt.send_panel_message
        self.old_card = rt.card_fields_from_file
        self.old_groups = rt.get_model_groups
        rt.send_panel_message = lambda *args, **kwargs: self.calls.append((args, kwargs))
        rt.card_fields_from_file = lambda _filename: {"name": "Test"}
        rt.get_model_groups = lambda: {}

    def tearDown(self):
        rt.send_panel_message = self.old_panel
        rt.card_fields_from_file = self.old_card
        rt.get_model_groups = self.old_groups
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def _route(self, text, chat_id="chat", session=None):
        session = session or self.session
        return rt.handle_command_route(
            self.db,
            "token",
            "",
            rt.DEFAULT_MODEL,
            {"name": "Test"},
            chat_id,
            text,
            text.casefold(),
            session,
            session["session_id"],
            rt.DEFAULT_MODEL,
            session.get("persona_id") or "",
            "user",
        )

    def test_character_panel_has_inline_delete_actions(self):
        old_paths = rt.character_card_paths
        old_display = rt.character_display_name
        old_callback_token = rt.dynamic_callback_token
        rt.character_card_paths = lambda: [Path("active.png"), Path("other.png")]
        rt.character_display_name = lambda path: path.stem
        rt.dynamic_callback_token = lambda _kind, filename, _chat: "cb-" + filename
        try:
            rt.send_character_menu("bot-token", "chat", "active.png")
        finally:
            rt.character_card_paths = old_paths
            rt.character_display_name = old_display
            rt.dynamic_callback_token = old_callback_token
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
        topic_session = rt.create_session(self.db, topic_id, rt.DEFAULT_MODEL, session_id="topic-panel")
        self.assertTrue(self._route("/scene", topic_id, topic_session))
        self.assertIn("scene:refresh", str(self.calls[-1]))
        self.calls.clear()
        self.assertTrue(self._route("/group goal", topic_id, topic_session))
        self.assertIn("goal:set", str(self.calls[-1]))

    def test_typed_search_group_and_inline_text_forms_preserve_payloads(self):
        memory_calls = []
        group_calls = []
        macro_calls = []
        old_memory = rt.handle_memory_command
        old_group = rt.handle_group_command
        old_macro = rt.handle_macro_command
        rt.handle_memory_command = lambda *args: memory_calls.append(args[-1])
        rt.handle_group_command = lambda *args: group_calls.append(args[4])
        rt.handle_macro_command = lambda *args: macro_calls.append(args[-1])
        try:
            self.assertTrue(self._route("/memory search hidden fact"))
            self.assertEqual(memory_calls, ["/memory search hidden fact"])
            self.assertTrue(self._route("/group add Mira", "chat|topic:1", rt.create_session(self.db, "chat|topic:1", rt.DEFAULT_MODEL, session_id="group")))
            self.assertEqual(group_calls, ["/group add Mira"])
            self.assertTrue(self._route("/macro {{char}} waves"))
            self.assertEqual(macro_calls, ["/macro {{char}} waves"])
        finally:
            rt.handle_memory_command = old_memory
            rt.handle_group_command = old_group
            rt.handle_macro_command = old_macro
    def test_world_menu_keeps_bot_token_for_telegram_request(self):
        old_world_paths = rt.world_file_paths
        old_active_worlds = rt.active_world_files
        old_callback_token = rt.dynamic_callback_token
        rt.world_file_paths = lambda: [Path("lore.json")]
        rt.active_world_files = lambda _current: []
        rt.dynamic_callback_token = lambda _kind, _name, _chat: "callback-token"
        try:
            rt.send_world_menu("bot-token", "chat", "")
        finally:
            rt.world_file_paths = old_world_paths
            rt.active_world_files = old_active_worlds
            rt.dynamic_callback_token = old_callback_token
        self.assertEqual(self.calls[0][0][0], "bot-token")
        payload = self.calls[0][0][3]
        self.assertEqual(payload["inline_keyboard"][0][0]["callback_data"], "world:callback-token")


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path
import tempfile
import unittest

import bridge.config as config
from runtime_test_facade import runtime as rt


class HelpDrilldownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = rt.db_connect()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_category_renders_command_buttons_and_detail(self):
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_help_menu("token", "chat", "basic")
            buttons = [button for row in calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row]
            self.assertIn("help:cmd:basic:0", {button["callback_data"] for button in buttons})
            self.assertIn("/start", {button["text"] for button in buttons})
            rt.send_help_menu("token", "chat", "basic", 77, 0)
            detail = calls[-1][1]
            self.assertEqual(calls[-1][0], "editMessageText")
            self.assertIn("Help — /start", detail["text"])
            self.assertIn("opening message", detail["text"])
            detail_callbacks = {button["callback_data"] for row in detail["reply_markup"]["inline_keyboard"] for button in row}
            self.assertEqual(detail_callbacks, {"help:basic", "help:close"})
        finally:
            rt.telegram_request = original_request

    def test_help_copy_explains_topics_and_actions(self):
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_help_menu("token", "chat")
            root = calls[-1][1]
            self.assertIn("Choose a topic below", root["text"])
            labels = {button["text"] for row in root["reply_markup"]["inline_keyboard"] for button in row}
            self.assertIn("💬 Start & Sessions", labels)
            rt.send_help_menu("token", "chat", "basic")
            category = calls[-1][1]
            self.assertIn("Start a conversation", category["text"])
            self.assertIn("Tap a command below", category["text"])
            rt.send_help_menu("token", "chat", "basic", 77, 0)
            self.assertIn("What it does:", calls[-1][1]["text"])
        finally:
            rt.telegram_request = original_request

    def test_command_detail_callback_rejects_stale_index(self):
        answers = []
        original_answer = rt.answer_callback
        rt.answer_callback = lambda _token, _callback_id, text: answers.append(text)
        try:
            handled = rt.handle_help_callback(self.db, "token", {"id": "cb"}, rt.answer_callback, "help:cmd:basic:99", "chat", {"message_id": 77}, {}, "default", None)
        finally:
            rt.answer_callback = original_answer
        self.assertTrue(handled)
        self.assertEqual(answers, ["Help choice expired"])

    def test_category_command_list_is_paginated_at_eight(self):
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_help_menu("token", "chat", "generation")
        finally:
            rt.telegram_request = original_request
        buttons = [button for row in calls[0][1]["reply_markup"]["inline_keyboard"] for button in row]
        command_buttons = [button for button in buttons if button["callback_data"].startswith("help:cmd:")]
        self.assertEqual(len(command_buttons), 8)
        self.assertIn("help:cmdpage:generation:1", {button["callback_data"] for row in calls[0][1]["reply_markup"]["inline_keyboard"] for button in row})

    def test_standalone_export_import_are_not_public_help_commands(self):
        public_commands = {command for entries in rt.HELP_CATEGORIES.values() for command, _summary in entries}
        self.assertNotIn("/export", public_commands)
        self.assertNotIn("/import", public_commands)
        self.assertIn("/sync", public_commands)

    def test_sync_help_exposes_only_live_api_sync(self):
        summary = dict(rt.HELP_CATEGORIES["basic"])["/sync"]
        detail = rt.command_detail("/sync", summary)
        self.assertIn("Live API Sync", summary)
        self.assertIn("Live API Sync controls", summary)
        self.assertNotIn("file sync", summary.lower())
        self.assertNotIn("Phase ", summary)
        self.assertIn("Sync now runs a one-shot API reconciliation", detail)
        self.assertNotIn("file sync", detail.lower())
        self.assertIn("Refresh status only redraws state", detail)
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.set_bot_commands("token")
        finally:
            rt.telegram_request = original_request
        commands = {item["command"]: item["description"] for item in calls[-1][1]["commands"]}
        self.assertEqual(commands["sync"], "Open Live API Sync controls")

    def test_sync_binding_is_stable_and_panel_is_scoped(self):
        session = rt.create_session(self.db, "chat", "provider/model", session_id="sync-session")
        first = rt.ensure_sync_binding(self.db, "chat", session["session_id"])
        second = rt.ensure_sync_binding(self.db, "chat", session["session_id"])
        self.assertEqual(first["sync_id"], second["sync_id"])
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_sync_menu("token", "chat", self.db, session)
        finally:
            rt.telegram_request = original_request
        callbacks = {button["callback_data"] for row in calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row}
        self.assertEqual(callbacks, {"sync:realtime", "sync:now", "sync:status", "sync:close"})

    def test_director_goal_and_scene_commands_are_documented(self):
        commands = {command for entries in rt.HELP_CATEGORIES.values() for command, _summary in entries}
        self.assertIn("/group goal", commands)
        self.assertIn("/group goal <objective>", commands)
        self.assertIn("/scene", commands)
        self.assertIn("/scene refresh", commands)
        self.assertIn("1,200 characters", rt.command_detail("/group goal", ""))
        self.assertIn("utility model", rt.command_detail("/scene refresh", ""))

    def test_help_command_fast_path_always_renders_panel(self):
        calls = []
        original_send = rt.send_help_menu
        rt.send_help_menu = lambda *args, **kwargs: calls.append((args, kwargs))
        try:
            self.assertEqual(rt.normalize_help_command("/help@SillyTavernPunzmeBot"), "")
            self.assertEqual(rt.normalize_help_command("/help scene refresh"), "scene refresh")
            self.assertIsNone(rt.normalize_help_command("help"))
            self.assertTrue(rt.send_help_command("token", "chat", "/help scene refresh"))
            self.assertEqual(calls[-1][0], ("token", "chat", "voice_group", None, 7))
            self.assertTrue(rt.send_help_command("token", "chat", "/help unknown"))
            self.assertEqual(calls[-1][0], ("token", "chat"))
            self.assertFalse(rt.send_help_command("token", "chat", "/helper"))
        finally:
            rt.send_help_menu = original_send

    def test_help_callbacks_are_marked_for_fast_path(self):
        self.assertTrue(rt.is_help_callback("help:menu"))
        self.assertTrue(rt.is_help_callback("help:cmd:voice_group:7"))
        self.assertFalse(rt.is_help_callback("persona:menu"))

    def test_read_only_and_feature_commands_render_panels(self):
        session = rt.create_session(self.db, "chat", rt.DEFAULT_MODEL, session_id="panel-session")
        calls = []
        original_panel = rt.send_panel_message
        original_groups = rt.get_model_groups
        rt.send_panel_message = lambda *args, **kwargs: calls.append((args, kwargs))
        rt.get_model_groups = lambda: {}
        try:
            rt.send_prompt_menu("token", "chat", self.db, session, {"name": "Test"})
            self.assertIn("prompt:budget", str(calls[-1]))
            rt.send_scene_menu("token", "chat", self.db, session)
            self.assertIn("scene:refresh", str(calls[-1]))
            rt.set_director_goal(self.db, "chat", session["session_id"], "Reveal the door")
            rt.send_director_goal_menu("token", "chat", self.db, session)
            self.assertIn("goal:set", str(calls[-1]))
            rt.send_curated_memory_menu("token", "chat", self.db, session)
            self.assertIn("curated:refresh", str(calls[-1]))
        finally:
            rt.send_panel_message = original_panel
            rt.get_model_groups = original_groups

if __name__ == "__main__":
    unittest.main()

from application_test_setup import ensure_application_extensions

ensure_application_extensions()

import json
from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import bridge.callbacks as _m_callbacks
import bridge.catalog as _m_catalog
import bridge.command_routes as _m_command_routes
import bridge.director_goals as _m_director_goals
import bridge.help as _m_help
import bridge.help_details as _m_help_details
import bridge.memory_curator as _m_memory_curator
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.scene_state as _m_scene_state
import bridge.session_naming as _m_session_naming
import bridge.sync_core as _m_sync_core
class HelpDrilldownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_category_renders_command_buttons_and_detail(self):
        calls = []
        original_request = _m_panel_callback_routes.telegram_request
        _m_panel_callback_routes.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            _m_command_routes.send_help_menu("token", "chat", "basic")
            buttons = [button for row in calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row]
            self.assertIn("help:cmd:basic:0", {button["callback_data"] for button in buttons})
            self.assertIn("/start", {button["text"] for button in buttons})
            _m_command_routes.send_help_menu("token", "chat", "basic", 77, 0)
            detail = calls[-1][1]
            self.assertEqual(calls[-1][0], "editMessageText")
            self.assertIn("Help — /start", detail["text"])
            self.assertIn("opening message", detail["text"])
            detail_callbacks = {button["callback_data"] for row in detail["reply_markup"]["inline_keyboard"] for button in row}
            self.assertEqual(detail_callbacks, {"help:basic", "help:close"})
        finally:
            _m_panel_callback_routes.telegram_request = original_request

    def test_help_copy_explains_topics_and_actions(self):
        calls = []
        original_request = _m_panel_callback_routes.telegram_request
        _m_panel_callback_routes.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            _m_command_routes.send_help_menu("token", "chat")
            root = calls[-1][1]
            self.assertIn("Choose a topic below", root["text"])
            labels = {button["text"] for row in root["reply_markup"]["inline_keyboard"] for button in row}
            self.assertIn("💬 Start & Sessions", labels)
            _m_command_routes.send_help_menu("token", "chat", "basic")
            category = calls[-1][1]
            self.assertIn("Start a conversation", category["text"])
            self.assertIn("Tap a command below", category["text"])
            _m_command_routes.send_help_menu("token", "chat", "basic", 77, 0)
            self.assertIn("What it does:", calls[-1][1]["text"])
        finally:
            _m_panel_callback_routes.telegram_request = original_request

    def test_command_detail_callback_rejects_stale_index(self):
        answers = []
        original_answer = _m_callbacks.answer_callback
        _m_callbacks.answer_callback = lambda _token, _callback_id, text: answers.append(text)
        try:
            handled = _m_panel_callback_routes.handle_help_callback(self.db, "token", {"id": "cb"}, _m_callbacks.answer_callback, "help:cmd:basic:99", "chat", {"message_id": 77}, {}, "default", None)
        finally:
            _m_callbacks.answer_callback = original_answer
        self.assertTrue(handled)
        self.assertEqual(answers, ["Help choice expired"])

    def test_category_command_list_is_paginated_at_eight(self):
        calls = []
        original_request = _m_panel_callback_routes.telegram_request
        _m_panel_callback_routes.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            _m_command_routes.send_help_menu("token", "chat", "generation")
        finally:
            _m_panel_callback_routes.telegram_request = original_request
        buttons = [button for row in calls[0][1]["reply_markup"]["inline_keyboard"] for button in row]
        command_buttons = [button for button in buttons if button["callback_data"].startswith("help:cmd:")]
        self.assertEqual(len(command_buttons), 8)
        self.assertIn("help:cmdpage:generation:1", {button["callback_data"] for row in calls[0][1]["reply_markup"]["inline_keyboard"] for button in row})

    def test_standalone_export_import_are_not_public_help_commands(self):
        public_commands = {command for entries in _m_help.HELP_CATEGORIES.values() for command, _summary in entries}
        self.assertNotIn("/export", public_commands)
        self.assertNotIn("/import", public_commands)
        self.assertIn("/sync", public_commands)

    def test_sync_help_exposes_only_live_api_sync(self):
        summary = dict(_m_help.HELP_CATEGORIES["basic"])["/sync"]
        detail = _m_help_details.command_detail("/sync", summary)
        self.assertIn("Live API Sync", summary)
        self.assertIn("Live API Sync controls", summary)
        self.assertNotIn("file sync", summary.lower())
        self.assertNotIn("Phase ", summary)
        self.assertIn("Sync now runs a one-shot API reconciliation", detail)
        self.assertNotIn("file sync", detail.lower())
        self.assertIn("Refresh status only redraws state", detail)
        calls = []
        original_request = _m_panel_callback_routes.telegram_request
        _m_panel_callback_routes.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            _m_help.set_bot_commands("token")
        finally:
            _m_panel_callback_routes.telegram_request = original_request
        commands = {item["command"]: item["description"] for item in calls[-1][1]["commands"]}
        self.assertEqual(commands["sync"], "Open Live API Sync controls")

    def test_sync_binding_is_stable_and_panel_is_scoped(self):
        session = _m_session_naming.create_session(self.db, "chat", "provider/model", session_id="sync-session")
        first = _m_sync_core.ensure_sync_binding(self.db, "chat", session["session_id"])
        second = _m_sync_core.ensure_sync_binding(self.db, "chat", session["session_id"])
        self.assertEqual(first["sync_id"], second["sync_id"])
        calls = []
        original_request = _m_panel_callback_routes.telegram_request
        _m_panel_callback_routes.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            _m_panel_callback_routes.send_sync_menu("token", "chat", self.db, session)
        finally:
            _m_panel_callback_routes.telegram_request = original_request
        callbacks = {button["callback_data"] for row in calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row}
        self.assertEqual(callbacks, {"sync:realtime", "sync:now", "sync:status", "sync:close"})

    def test_director_goal_and_scene_commands_are_documented(self):
        commands = {command for entries in _m_help.HELP_CATEGORIES.values() for command, _summary in entries}
        self.assertIn("/group goal", commands)
        self.assertIn("/group goal <objective>", commands)
        self.assertIn("/scene", commands)
        self.assertIn("/scene refresh", commands)
        self.assertIn("1,200 characters", _m_help_details.command_detail("/group goal", ""))
        self.assertIn("utility model", _m_help_details.command_detail("/scene refresh", ""))

    def test_help_command_fast_path_always_renders_panel(self):
        calls = []
        original_send = _m_command_routes.send_help_menu
        _m_command_routes.send_help_menu = lambda *args, **kwargs: calls.append((args, kwargs))
        try:
            self.assertEqual(_m_help_details.normalize_help_command("/help@SillyTavernPunzmeBot"), "")
            self.assertEqual(_m_help_details.normalize_help_command("/help scene refresh"), "scene refresh")
            self.assertIsNone(_m_help_details.normalize_help_command("help"))
            self.assertTrue(_m_command_routes.send_help_command("token", "chat", "/help scene refresh"))
            self.assertEqual(calls[-1][0], ("token", "chat", "voice_group", None, 7))
            self.assertTrue(_m_command_routes.send_help_command("token", "chat", "/help unknown"))
            self.assertEqual(calls[-1][0], ("token", "chat"))
            self.assertFalse(_m_command_routes.send_help_command("token", "chat", "/helper"))
        finally:
            _m_command_routes.send_help_menu = original_send

    def test_help_callbacks_are_marked_for_fast_path(self):
        self.assertTrue(_m_help_details.is_help_callback("help:menu"))
        self.assertTrue(_m_help_details.is_help_callback("help:cmd:voice_group:7"))
        self.assertFalse(_m_help_details.is_help_callback("persona:menu"))

    def test_read_only_and_feature_commands_render_panels(self):
        session = _m_session_naming.create_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL, session_id="panel-session")
        calls = []
        original_panel = _m_panel_callback_routes.send_panel_message
        original_groups = _m_catalog.get_model_groups
        _m_panel_callback_routes.send_panel_message = lambda *args, **kwargs: calls.append((args, kwargs))
        _m_catalog.get_model_groups = lambda: {}
        try:
            _m_command_routes.send_prompt_menu("token", "chat", self.db, session, {"name": "Test"})
            self.assertIn("prompt:budget", str(calls[-1]))
            _m_scene_state.send_scene_menu("token", "chat", self.db, session)
            self.assertIn("scene:refresh", str(calls[-1]))
            _m_director_goals.set_director_goal(self.db, "chat", session["session_id"], "Reveal the door")
            _m_director_goals.send_director_goal_menu("token", "chat", self.db, session)
            self.assertIn("goal:set", str(calls[-1]))
            _m_memory_curator.send_curated_memory_menu("token", "chat", self.db, session)
            self.assertIn("curated:refresh", str(calls[-1]))
        finally:
            _m_panel_callback_routes.send_panel_message = original_panel
            _m_catalog.get_model_groups = original_groups

if __name__ == "__main__":
    unittest.main()

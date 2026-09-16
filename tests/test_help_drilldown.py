import json
from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class HelpDrilldownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
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

    def test_sync_help_covers_all_three_phases_and_command_menu(self):
        summary = dict(rt.HELP_CATEGORIES["basic"])["/sync"]
        detail = rt.command_detail("/sync", summary)
        self.assertIn("Phase 1", summary)
        self.assertIn("Phase 2", summary)
        self.assertIn("Phase 3", summary)
        self.assertIn("Sync now uses Phase 3", detail)
        self.assertIn("Refresh status only redraws state", detail)
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.set_bot_commands("token")
        finally:
            rt.telegram_request = original_request
        commands = {item["command"]: item["description"] for item in calls[-1][1]["commands"]}
        self.assertEqual(commands["sync"], "Open manual, file, and realtime API sync")

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
        self.assertEqual(callbacks, {"sync:export", "sync:import", "sync:auto", "sync:realtime", "sync:now", "sync:status", "sync:close"})

    def test_export_contains_sync_identity_and_metadata(self):
        session = rt.create_session(self.db, "chat", "provider/model", session_id="export-session")
        self.db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)", ("chat", "export-session", "user", "Hello", 1.0))
        self.db.commit()
        export_dir = Path(self.tmp.name) / "exports"
        original_dir, original_send = rt.EXPORT_DIR, rt.send_document
        captured = []
        rt.EXPORT_DIR = export_dir
        rt.send_document = lambda _token, _chat, path, _caption: captured.append(json.loads(path.read_text(encoding="utf-8").splitlines()[0])) or True
        try:
            rt.export_session("token", self.db, session, {"name": "Test"}, "chat")
        finally:
            rt.EXPORT_DIR, rt.send_document = original_dir, original_send
        metadata = captured[0]["chat_metadata"]
        self.assertRegex(metadata["bridge_sync"]["sync_id"], r"^stb-[a-f0-9]{32}$")
        self.assertEqual(metadata["bridge_sync"]["version"], 1)
        self.assertEqual(metadata["character_file"], session["character_file"])

    def test_import_creates_new_session_and_records_direction(self):
        sync_id = "stb-" + "a" * 32
        raw = (json.dumps({"chat_metadata": {"name": "Imported", "bridge_sync": {"version": 1, "sync_id": sync_id}}}) + "\n" + json.dumps({"is_user": True, "is_system": False, "mes": "From ST"}) + "\n").encode()
        imported = rt.import_chat_session(self.db, "chat", raw, "provider/model")
        self.assertNotEqual(imported["session_id"], "default")
        binding = self.db.execute("SELECT sync_id,last_direction FROM sync_bindings WHERE chat_id=? AND session_id=?", ("chat", imported["session_id"])).fetchone()
        self.assertEqual(binding, (sync_id, "sillytavern_to_bridge"))
        count = self.db.execute("SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?", ("chat", imported["session_id"])).fetchone()[0]
        self.assertEqual(count, 1)

    def test_sync_import_callback_shows_safe_instructions(self):
        answers, calls = [], []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            handled = rt.handle_sync_callback(self.db, "token", {"id": "cb"}, lambda _t, _i, text: answers.append(text), "sync:import", "chat", {"message_id": 77}, {}, "sync-session", None)
        finally:
            rt.telegram_request = original_request
        self.assertTrue(handled)
        self.assertEqual(answers, ["Send JSONL"])
        self.assertEqual(calls[-1][0], "editMessageText")
        self.assertIn("creates a separate session", calls[-1][1]["text"])


if __name__ == "__main__":
    unittest.main()

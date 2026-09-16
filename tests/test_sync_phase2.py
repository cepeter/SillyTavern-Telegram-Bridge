import json
from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class Phase2SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = rt.DB_FILE
        self.old_chat_dir = rt.PHASE2_SYNC_CHAT_DIR
        self.old_group_dir = rt.PHASE2_SYNC_GROUP_DIR
        self.old_backup_dir = rt.PHASE2_SYNC_BACKUP_DIR
        self.old_card_fields = rt.card_fields_from_file
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        rt.PHASE2_SYNC_CHAT_DIR = Path(self.tmp.name) / "st-chats"
        rt.PHASE2_SYNC_GROUP_DIR = Path(self.tmp.name) / "st-groups"
        rt.PHASE2_SYNC_BACKUP_DIR = Path(self.tmp.name) / "sync-backups"
        rt.card_fields_from_file = lambda _filename: {"name": "Test Character", "first_mes": "", "description": "", "personality": "", "scenario": ""}
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.session = rt.create_session(self.db, "chat", "provider/model", session_id="phase2-session")
        self.session["character_file"] = "Test.png"

    def tearDown(self):
        rt.DB_FILE = self.old_db
        rt.PHASE2_SYNC_CHAT_DIR = self.old_chat_dir
        rt.PHASE2_SYNC_GROUP_DIR = self.old_group_dir
        rt.PHASE2_SYNC_BACKUP_DIR = self.old_backup_dir
        rt.card_fields_from_file = self.old_card_fields
        self.db.close()
        self.tmp.cleanup()

    def _add_message(self, role, content, created_at=None):
        self.db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)", ("chat", "phase2-session", role, content, created_at or 1.0))
        self.db.commit()

    def _path(self):
        binding = rt._phase2_binding(self.db, "chat", "phase2-session")
        return Path(binding["external_path"]) if binding["external_path"] else rt._phase2_path(self.db, "chat", self.session, binding)

    def test_initial_sync_creates_bounded_external_file_and_checkpoint(self):
        self._add_message("user", "Hello")
        result = rt.phase2_sync_now(self.db, "chat", "phase2-session")
        self.assertEqual(result, "created external chat")
        path = self._path()
        self.assertTrue(path.is_file())
        data = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        metadata = data[0]["chat_metadata"]
        self.assertEqual(metadata["bridge_sync"]["version"], 2)
        self.assertRegex(metadata["bridge_sync"]["sync_id"], r"^stb-[a-f0-9]{32}$")
        binding = rt._phase2_binding(self.db, "chat", "phase2-session")
        self.assertEqual(binding["last_direction"], "bridge_to_sillytavern")
        self.assertEqual(binding["conflict"], "")
        self.assertTrue(binding["last_hash"])

    def test_external_edit_is_imported_without_changing_session_identity(self):
        self._add_message("user", "Original")
        rt.phase2_sync_now(self.db, "chat", "phase2-session")
        path = self._path()
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        records[1]["mes"] = "Edited in SillyTavern"
        path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
        result = rt.phase2_sync_now(self.db, "chat", "phase2-session")
        self.assertEqual(result, "imported SillyTavern changes")
        stored = self.db.execute("SELECT session_id,content FROM messages WHERE chat_id=? AND session_id=?", ("chat", "phase2-session")).fetchall()
        self.assertEqual(stored, [("phase2-session", "Edited in SillyTavern")])

    def test_local_edit_is_exported_when_external_file_is_unchanged(self):
        self._add_message("user", "Original")
        rt.phase2_sync_now(self.db, "chat", "phase2-session")
        self.db.execute("UPDATE messages SET content=? WHERE chat_id=? AND session_id=?", ("Edited in Telegram", "chat", "phase2-session"))
        self.db.commit()
        self.assertEqual(rt.phase2_sync_now(self.db, "chat", "phase2-session"), "exported bridge changes")
        records = [json.loads(line) for line in self._path().read_text(encoding="utf-8").splitlines()]
        self.assertEqual(records[1]["mes"], "Edited in Telegram")

    def test_external_deletion_is_imported(self):
        self._add_message("user", "Keep this")
        self._add_message("assistant", "Remove this", 1.001)
        rt.phase2_sync_now(self.db, "chat", "phase2-session")
        path = self._path()
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        records.pop()
        path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
        self.assertEqual(rt.phase2_sync_now(self.db, "chat", "phase2-session"), "imported SillyTavern changes")
        count = self.db.execute("SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?", ("chat", "phase2-session")).fetchone()[0]
        self.assertEqual(count, 1)

    def test_both_sides_changed_stops_with_conflict(self):
        self._add_message("user", "Original")
        rt.phase2_sync_now(self.db, "chat", "phase2-session")
        self.db.execute("UPDATE messages SET content=? WHERE chat_id=? AND session_id=?", ("Telegram edit", "chat", "phase2-session"))
        self.db.commit()
        path = self._path()
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        records[1]["mes"] = "SillyTavern edit"
        path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
        self.assertEqual(rt.phase2_sync_now(self.db, "chat", "phase2-session"), "conflict detected; sync stopped")
        binding = rt._phase2_binding(self.db, "chat", "phase2-session")
        self.assertEqual(binding["conflict"], "conflict")
        self.assertEqual(binding["auto_enabled"], 0)

    def test_sync_panel_exposes_phase2_controls(self):
        calls = []
        original_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_sync_menu("token", "chat", self.db, self.session)
        finally:
            rt.telegram_request = original_request
        callbacks = {button["callback_data"] for row in calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row}
        self.assertIn("sync:auto", callbacks)
        self.assertIn("sync:now", callbacks)

    def test_swipes_are_exported_and_imported(self):
        self._add_message("user", "Question")
        self._add_message("assistant", "Answer two", 1.001)
        user_rowid = self.db.execute("SELECT rowid FROM messages WHERE role='user' AND session_id='phase2-session'").fetchone()[0]
        rt.save_response_variant(self.db, "chat", "phase2-session", "Question", "Answer one", user_rowid=user_rowid)
        rt.save_response_variant(self.db, "chat", "phase2-session", "Question", "Answer two", user_rowid=user_rowid)
        rt.phase2_sync_now(self.db, "chat", "phase2-session")
        assistant = json.loads(self._path().read_text(encoding="utf-8").splitlines()[2])
        self.assertEqual(assistant["swipes"], ["Answer one", "Answer two"])
        self.assertEqual(assistant["swipe_id"], 1)

    def test_auto_toggle_initializes_file_and_poll_imports_changes(self):
        self._add_message("user", "Original")
        self.assertIn("auto sync enabled", rt.phase2_toggle_auto(self.db, "chat", "phase2-session"))
        binding = rt._phase2_binding(self.db, "chat", "phase2-session")
        self.assertEqual(binding["auto_enabled"], 1)
        path = self._path()
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        records[1]["mes"] = "Changed by ST"
        path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
        self.db.execute("UPDATE sync_bindings SET last_checked_at=0 WHERE chat_id='chat' AND session_id='phase2-session'")
        self.db.commit()
        rt.phase2_sync_poll(self.db)
        self.assertEqual(self.db.execute("SELECT content FROM messages WHERE role='user' AND session_id='phase2-session'").fetchone()[0], "Changed by ST")
        self.assertEqual(rt.phase2_toggle_auto(self.db, "chat", "phase2-session"), "auto sync disabled")


if __name__ == "__main__":
    unittest.main()

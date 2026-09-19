from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class ItemPanelLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = rt.DB_FILE
        self.old_schema = rt._DB_SCHEMA_READY
        self.old_request = rt.telegram_request
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.calls = []
        rt.telegram_request = lambda _token, method, payload: self.calls.append((method, payload)) or {"message_id": 1}

    def tearDown(self):
        rt.telegram_request = self.old_request
        self.db.close()
        rt.DB_FILE = self.old_db
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = self.old_schema
        self.tmp.cleanup()

    @staticmethod
    def _callbacks(payload):
        return [button["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]

    def test_persona_rows_have_delete_callbacks(self):
        old_loader = rt.load_personas
        rt.load_personas = lambda: {"p1": {"name": "Punto"}, "p2": {"name": "Alt"}}
        try:
            rt.send_persona_menu("bot", "chat", "p1")
        finally:
            rt.load_personas = old_loader
        callbacks = self._callbacks(self.calls[-1][1])
        self.assertEqual(sum(value.startswith("persona:delete:") for value in callbacks), 2)
        self.assertNotIn("persona:delete", callbacks)

    def test_preset_rows_have_delete_callbacks(self):
        rt.save_generation_preset(self.db, "chat", "fast", {"temperature": 0.7})
        rt.send_preset_menu("bot", "chat", self.db)
        callbacks = self._callbacks(self.calls[-1][1])
        self.assertIn("enum:preset:save", callbacks)
        self.assertTrue(any(value.startswith("enum:presetdel:") for value in callbacks))
        self.assertNotIn("enum:presetdelete", callbacks)

    def test_databank_rows_have_versions_and_remove_callbacks(self):
        old_docs = rt.data_bank_documents
        old_coverage = rt.rag_embedding_coverage
        rt.data_bank_documents = lambda _db, _chat: [("doc-1", "lore.json", 1, 2)]
        rt.rag_embedding_coverage = lambda _db, _chat: (2, 2)
        try:
            rt.send_databank_menu("bot", "chat", self.db)
        finally:
            rt.data_bank_documents = old_docs
            rt.rag_embedding_coverage = old_coverage
        callbacks = self._callbacks(self.calls[-1][1])
        self.assertTrue(any(value.startswith("enum:ragversions:") for value in callbacks))
        self.assertTrue(any(value.startswith("enum:ragremove:") for value in callbacks))

    def test_character_upload_reports_duplicate_and_new_version(self):
        root = Path(self.tmp.name)
        old_character_dir = rt.CHARACTER_DIR
        old_backup_dir = rt.CHARACTER_BACKUP_DIR
        old_parse = rt.parse_png_chara_bytes
        old_fields = rt.card_fields
        old_send = rt.send_text
        sent = []
        rt.CHARACTER_DIR = root / "characters"
        rt.CHARACTER_BACKUP_DIR = root / "backups"
        rt.parse_png_chara_bytes = lambda _raw: {"name": "Test Character"}
        rt.card_fields = lambda _card: {"name": "Test Character"}
        rt.send_text = lambda _token, _chat, text: sent.append(text) or []
        try:
            rt.import_character_card(self.db, "bot", "chat", "one.png", b"one")
            rt.import_character_card(self.db, "bot", "chat", "one.png", b"one")
            rt.import_character_card(self.db, "bot", "chat", "one.png", b"two")
        finally:
            rt.CHARACTER_DIR = old_character_dir
            rt.CHARACTER_BACKUP_DIR = old_backup_dir
            rt.parse_png_chara_bytes = old_parse
            rt.card_fields = old_fields
            rt.send_text = old_send
        self.assertIn("Duplicate character card:", sent[1])
        self.assertIn("New character-card version installed:", sent[2])
        self.assertIn("Previous version retained as Test_Character.png", sent[2])

    def test_group_rows_remove_members_without_card_delete_callback(self):
        session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        old_fields = rt.card_fields_from_file
        rt.card_fields_from_file = lambda _filename: {"name": "Member"}
        rt.save_group_state(self.db, "chat", session["session_id"], {
            "title": "Group", "enabled": True, "turn_index": 0,
            "mode": "round_robin", "forced_speaker": "",
            "members": ["member.png"], "turn_user_id": "", "turn_users": [],
        })
        try:
            rt.send_group_menu(self.db, "bot", "chat", session)
        finally:
            rt.card_fields_from_file = old_fields
        callbacks = self._callbacks(self.calls[-1][1])
        self.assertTrue(any(value.startswith("groupremove:") for value in callbacks))
        self.assertFalse(any(value.startswith("characterdelete") for value in callbacks))


if __name__ == "__main__":
    unittest.main()

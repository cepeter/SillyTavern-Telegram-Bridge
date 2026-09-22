from application_test_setup import ensure_application_extensions, make_test_persona_service, make_test_request_context

ensure_application_extensions()

from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import bridge.callbacks as _m_callbacks
import bridge.cards as _m_cards
import bridge.character_identity as _m_character_identity
import bridge.command_routes as _m_command_routes
import bridge.groups as _m_groups
import bridge.help as _m_help
import bridge.input_flows as _m_input_flows
import bridge.main as _m_main
import bridge.memory_curator as _m_memory_curator
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.persona_sync as _m_persona_sync
import bridge.status_panels as _m_status_panels
import bridge.sync_api as _m_sync_api
import bridge.telegram as _m_telegram
class ItemPanelLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        self.old_request = _m_cards.send_panel_request
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.calls = []
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: self.calls.append((method, payload)) or {"message_id": 1}

    def tearDown(self):
        _m_cards.send_panel_request = self.old_request
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    @staticmethod
    def _callbacks(payload):
        return [button["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]

    def test_persona_rows_have_delete_callbacks(self):
        persona_service = make_test_persona_service(personas={"p1": {"name": "Punto"}, "p2": {"name": "Alt"}})
        _m_command_routes.send_persona_menu("bot", "chat", "p1", persona_service=persona_service, request_context=make_test_request_context(self.db))
        callbacks = self._callbacks(self.calls[-1][1])
        self.assertEqual(sum(value.startswith("persona:delete:") for value in callbacks), 2)
        self.assertNotIn("persona:delete", callbacks)

    def test_preset_rows_have_delete_callbacks(self):
        _m_input_flows.save_generation_preset(self.db, "chat", "fast", {"temperature": 0.7})
        _m_command_routes.send_preset_menu("bot", "chat", self.db, request_context=make_test_request_context(self.db))
        callbacks = self._callbacks(self.calls[-1][1])
        self.assertIn("enum:preset:save", callbacks)
        self.assertTrue(any(value.startswith("enum:presetdel:") for value in callbacks))
        self.assertNotIn("enum:presetdelete", callbacks)

    def test_databank_rows_have_versions_and_remove_callbacks(self):
        old_docs = _m_help.data_bank_documents
        old_coverage = _m_help.rag_embedding_coverage
        _m_help.data_bank_documents = lambda _db, _chat: [("doc-1", "lore.json", 1, 2)]
        _m_help.rag_embedding_coverage = lambda _db, _chat: (2, 2)
        try:
            _m_command_routes.send_databank_menu("bot", "chat", self.db, request_context=make_test_request_context(self.db))
        finally:
            _m_help.data_bank_documents = old_docs
            _m_help.rag_embedding_coverage = old_coverage
        callbacks = self._callbacks(self.calls[-1][1])
        self.assertTrue(any(value.startswith("enum:ragversions:") for value in callbacks))
        self.assertTrue(any(value.startswith("enum:ragremove:") for value in callbacks))

    def test_character_upload_reports_duplicate_and_new_version(self):
        root = Path(self.tmp.name)
        old_character_dir = _m_telegram.CHARACTER_DIR
        old_backup_dir = _m_telegram.CHARACTER_BACKUP_DIR
        old_parse = _m_telegram.parse_png_chara_bytes
        old_fields = _m_telegram.card_fields
        old_send = _m_telegram.send_text
        sent = []
        _m_telegram.CHARACTER_DIR = root / "characters"
        _m_telegram.CHARACTER_BACKUP_DIR = root / "backups"
        _m_telegram.parse_png_chara_bytes = lambda _raw: {"name": "Test Character"}
        _m_telegram.card_fields = lambda _card: {"name": "Test Character"}
        _m_telegram.send_text = lambda _token, _chat, text: sent.append(text) or []
        try:
            _m_telegram.import_character_card(self.db, "bot", "chat", "one.png", b"one")
            _m_telegram.import_character_card(self.db, "bot", "chat", "one.png", b"one")
            _m_telegram.import_character_card(self.db, "bot", "chat", "one.png", b"two")
        finally:
            _m_telegram.CHARACTER_DIR = old_character_dir
            _m_telegram.CHARACTER_BACKUP_DIR = old_backup_dir
            _m_telegram.parse_png_chara_bytes = old_parse
            _m_telegram.card_fields = old_fields
            _m_telegram.send_text = old_send
        self.assertIn("Duplicate character card:", sent[1])
        self.assertIn("New character-card version installed:", sent[2])
        self.assertIn("Previous version retained as Test_Character.png", sent[2])

    def test_group_rows_remove_members_without_card_delete_callback(self):
        session = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)
        old_fields = _m_groups.card_fields_from_file
        _m_groups.card_fields_from_file = lambda _filename: {"name": "Member"}
        _m_groups.save_group_state(self.db, "chat", session["session_id"], {
            "title": "Group", "enabled": True, "turn_index": 0,
            "mode": "round_robin", "forced_speaker": "",
            "members": ["member.png"], "turn_user_id": "", "turn_users": [],
        })
        try:
            _m_panel_callback_routes.send_group_menu(self.db, "bot", "chat", session, request_context=make_test_request_context(self.db, session["session_id"]))
        finally:
            _m_groups.card_fields_from_file = old_fields
        callbacks = self._callbacks(self.calls[-1][1])
        self.assertTrue(any(value.startswith("groupremove:") for value in callbacks))
        self.assertFalse(any(value.startswith("characterdelete") for value in callbacks))


if __name__ == "__main__":
    unittest.main()

from application_test_setup import (
    ensure_application_extensions,
    make_test_group_service,
    make_test_persona_service,
    make_test_request_context,
)
from settings_test_support import SettingsTestCase

import bridge.command_panels as _command_panels

ensure_application_extensions()

import tempfile
import unittest
from pathlib import Path

import bridge.cards as _m_cards
import bridge.command_routes as _m_command_routes
import bridge.group_core as _m_group_core
import bridge.groups as _m_groups
import bridge.help as _m_help
import bridge.input_flows as _m_input_flows
import bridge.memory_curator as _m_memory_curator
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.telegram as _m_telegram


class ItemPanelLayoutTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.old_request = _m_cards.send_panel_request
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.calls = []
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: (
            self.calls.append((method, payload)) or {"message_id": 1}
        )

    def tearDown(self):
        _m_cards.send_panel_request = self.old_request
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    @staticmethod
    def _callbacks(payload):
        return [button["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]

    def test_persona_rows_have_delete_callbacks(self):
        persona_service = make_test_persona_service(personas={"p1": {"name": "Punto"}, "p2": {"name": "Alt"}})
        _m_command_routes.send_persona_menu(
            "bot",
            "chat",
            "p1",
            persona_service=persona_service,
            request_context=make_test_request_context(self.db, app_settings=self.app_settings_builder.build()),
        )
        callbacks = self._callbacks(self.calls[-1][1])
        self.assertEqual(sum(value.startswith("persona:delete:") for value in callbacks), 2)
        self.assertNotIn("persona:delete", callbacks)

    def test_preset_rows_have_delete_callbacks(self):
        _m_input_flows.save_generation_preset(self.db, "chat", "fast", {"temperature": 0.7})
        _command_panels.send_preset_menu(
            "bot",
            "chat",
            self.db,
            request_context=make_test_request_context(self.db, app_settings=self.app_settings_builder.build()),
        )
        callbacks = self._callbacks(self.calls[-1][1])
        self.assertIn("enum:preset:save", callbacks)
        self.assertTrue(any(value.startswith("enum:presetdel:") for value in callbacks))
        self.assertNotIn("enum:presetdelete", callbacks)

    def test_databank_rows_have_versions_and_remove_callbacks(self):
        old_docs = _m_help.data_bank_documents
        old_coverage = _m_help.rag_embedding_coverage
        _m_help.data_bank_documents = lambda _db, _chat: [("doc-1", "lore.json", 1, 2)]
        _m_help.rag_embedding_coverage = lambda _db, _chat, *, app_settings=None: (2, 2)
        try:
            _command_panels.send_databank_menu(
                "bot",
                "chat",
                self.db,
                request_context=make_test_request_context(self.db, app_settings=self.app_settings_builder.build()),
            )
        finally:
            _m_help.data_bank_documents = old_docs
            _m_help.rag_embedding_coverage = old_coverage
        callbacks = self._callbacks(self.calls[-1][1])
        self.assertTrue(any(value.startswith("enum:ragversions:") for value in callbacks))
        self.assertTrue(any(value.startswith("enum:ragremove:") for value in callbacks))

    def test_character_upload_reports_duplicate_and_new_version(self):
        root = Path(self.tmp.name)
        old_character_dir = self.app_settings_builder.character_dir
        old_backup_dir = self.app_settings_builder.character_backup_dir
        old_parse = _m_telegram.parse_png_chara_bytes
        old_fields = _m_telegram.card_fields
        old_send = _m_telegram.send_text
        sent = []
        self.app_settings_builder.character_dir = root / "characters"
        self.app_settings_builder.character_backup_dir = root / "backups"
        _m_telegram.parse_png_chara_bytes = lambda _raw: {"name": "Test Character"}
        _m_telegram.card_fields = lambda _card, *, app_settings=None: {"name": "Test Character"}
        _m_telegram.send_text = lambda _token, _chat, text: sent.append(text) or []
        try:
            _m_telegram.import_character_card(
                self.db, "bot", "chat", "one.png", b"one", app_settings=self.app_settings_builder.build()
            )
            _m_telegram.import_character_card(
                self.db, "bot", "chat", "one.png", b"one", app_settings=self.app_settings_builder.build()
            )
            _m_telegram.import_character_card(
                self.db, "bot", "chat", "one.png", b"two", app_settings=self.app_settings_builder.build()
            )
        finally:
            self.app_settings_builder.character_dir = old_character_dir
            self.app_settings_builder.character_backup_dir = old_backup_dir
            _m_telegram.parse_png_chara_bytes = old_parse
            _m_telegram.card_fields = old_fields
            _m_telegram.send_text = old_send
        self.assertIn("Duplicate character card:", sent[1])
        self.assertIn("New character-card version installed:", sent[2])
        self.assertIn("Previous version retained as Test_Character.png", sent[2])

    def test_group_rows_remove_members_without_card_delete_callback(self):
        session = _m_telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        old_fields = _m_groups.card_fields_from_file
        _m_groups.card_fields_from_file = lambda _filename, *, app_settings=None: {"name": "Member"}
        _m_group_core.save_group_state(
            self.db,
            "chat",
            session["session_id"],
            {
                "title": "Group",
                "enabled": True,
                "turn_index": 0,
                "mode": "round_robin",
                "forced_speaker": "",
                "members": ["member.png"],
                "turn_user_id": "",
                "turn_users": [],
            },
        )
        try:
            _m_panel_callback_routes.send_group_menu(
                self.db,
                "bot",
                "chat",
                session,
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                request_context=make_test_request_context(
                    self.db, session["session_id"], app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            _m_groups.card_fields_from_file = old_fields
        callbacks = self._callbacks(self.calls[-1][1])
        self.assertTrue(any(value.startswith("groupremove:") for value in callbacks))
        self.assertFalse(any(value.startswith("characterdelete") for value in callbacks))


if __name__ == "__main__":
    unittest.main()

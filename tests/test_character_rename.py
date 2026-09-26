import application_test_setup as application_setup
from application_test_setup import ensure_application_extensions, make_test_group_service, make_test_request_context
from settings_test_support import SettingsTestCase

import bridge.character_callbacks as _owner_character_callbacks

ensure_application_extensions()

import base64
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import bridge.cards as _m_cards
import bridge.character_identity as _m_character_identity
import bridge.memory_curator as _m_memory_curator
import bridge.session_naming as _m_session_naming


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _card_png(name: str, pixel=(1, 2, 3, 255)) -> bytes:
    card = {"spec": "chara_card_v2", "data": {"name": name, "description": "", "first_mes": ""}}
    metadata = b"chara\x00" + base64.b64encode(json.dumps(card).encode("utf-8"))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    image = zlib.compress(bytes([0, *pixel]))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"tEXt", metadata)
        + _chunk(b"IDAT", image)
        + _chunk(b"IEND", b"")
    )


class CharacterRenameTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_dir = self.app_settings_builder.character_dir
        self.old_config_dir = self.app_settings_builder.character_dir
        self.old_backup = self.app_settings_builder.character_backup_dir
        self.old_card = self.app_settings_builder.card_file
        self.old_config_card = self.app_settings_builder.card_file
        self.old_db = self.app_settings_builder.db_file
        character_dir = root / "characters"
        card_file = character_dir / "Default.png"
        self.app_settings_builder.character_dir = character_dir
        self.app_settings_builder.character_dir = character_dir
        self.app_settings_builder.character_backup_dir = root / "backups"
        self.app_settings_builder.card_file = card_file
        self.app_settings_builder.card_file = card_file
        self.app_settings_builder.db_file = root / "bridge.sqlite3"
        self.app_settings_builder.character_dir.mkdir()
        self.app_settings_builder.character_backup_dir.mkdir()
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _m_session_naming.create_session(
            self.db, "chat", "provider/model", session_id="active", app_settings=self.app_settings_builder.build()
        )
        _m_session_naming.update_session(self.db, "chat", "active", character_file="Old.png")

    def tearDown(self):
        self.db.close()
        self.app_settings_builder.character_dir = self.old_dir
        self.app_settings_builder.character_dir = self.old_config_dir
        self.app_settings_builder.character_backup_dir = self.old_backup
        self.app_settings_builder.card_file = self.old_card
        self.app_settings_builder.card_file = self.old_config_card
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def test_unique_visual_fingerprint_rebinds_renamed_card(self):
        (self.app_settings_builder.character_backup_dir / "Old.png").write_bytes(_card_png("Old"))
        (self.app_settings_builder.character_dir / "Renamed.png").write_bytes(_card_png("New Display Name"))
        session = _m_memory_curator.load_session(
            self.db, "chat", "active", "provider/model", app_settings=self.app_settings_builder.build()
        )
        repaired = _m_character_identity.reconcile_session_character(
            self.db, "chat", session, app_settings=self.app_settings_builder.build()
        )
        self.assertEqual(repaired["character_file"], "Renamed.png")
        stored = _m_memory_curator.load_session(
            self.db, "chat", "active", "provider/model", app_settings=self.app_settings_builder.build()
        )
        self.assertEqual(stored["character_file"], "Renamed.png")

    def test_ambiguous_visual_match_does_not_rebind(self):
        (self.app_settings_builder.character_backup_dir / "Old.png").write_bytes(_card_png("Old"))
        (self.app_settings_builder.character_dir / "One.png").write_bytes(_card_png("One"))
        (self.app_settings_builder.character_dir / "Two.png").write_bytes(_card_png("Two"))
        self.assertEqual(
            _m_character_identity.resolve_renamed_character("Old.png", app_settings=self.app_settings_builder.build()),
            "",
        )
        session = _m_memory_curator.load_session(
            self.db, "chat", "active", "provider/model", app_settings=self.app_settings_builder.build()
        )
        self.assertEqual(
            _m_character_identity.reconcile_session_character(
                self.db, "chat", session, app_settings=self.app_settings_builder.build()
            )["character_file"],
            "Old.png",
        )

    def test_unique_embedded_name_is_safe_fallback(self):
        (self.app_settings_builder.character_dir / "different-file.png").write_bytes(
            _card_png("Old", pixel=(9, 8, 7, 255))
        )
        self.assertEqual(
            _m_character_identity.resolve_renamed_character("Old.png", app_settings=self.app_settings_builder.build()),
            "different-file.png",
        )

    def test_character_panel_rereads_embedded_name_and_has_refresh(self):
        (self.app_settings_builder.character_dir / "Renamed.png").write_bytes(_card_png("Fresh Name"))
        calls = []
        original = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _m_session_naming.send_character_menu(
                "token",
                "chat",
                "Renamed.png",
                request_context=make_test_request_context(
                    self.db, "active", app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            _m_cards.send_panel_request = original
        payload = calls[-1][1]
        labels = [button["text"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]
        callbacks = [button["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]
        self.assertTrue(any("Fresh Name" in label for label in labels))
        self.assertIn("character:menu", callbacks)
        self.assertIn("Current character: Fresh Name", payload["text"])

    def test_character_refresh_treats_unchanged_edit_as_success(self):
        (self.app_settings_builder.character_dir / "Old.png").write_bytes(_card_png("Old"))
        callback = {
            "id": "callback",
            "data": "character:menu",
            "message": {"message_id": 10, "chat": {"id": "chat"}},
        }
        answers = []
        original = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Telegram editMessageText failed: Bad Request: message is not modified")
        )
        try:
            handled = _owner_character_callbacks.handle_character_callback(
                self.db,
                "token",
                callback,
                lambda _token, _callback_id, text: answers.append(text),
                "character:menu",
                "chat",
                callback["message"],
                _m_memory_curator.load_session(
                    self.db, "chat", "active", "provider/model", app_settings=self.app_settings_builder.build()
                ),
                "active",
                None,
                group_service=make_test_group_service(app_settings=self.app_settings_builder.build()),
                request_context=make_test_request_context(
                    self.db, "active", app_settings=self.app_settings_builder.build()
                ),
                provider_port=application_setup.make_test_provider_port(),
            )
        finally:
            _m_cards.send_panel_request = original
        self.assertTrue(handled)
        self.assertEqual(answers, ["Refreshed"])


if __name__ == "__main__":
    unittest.main()

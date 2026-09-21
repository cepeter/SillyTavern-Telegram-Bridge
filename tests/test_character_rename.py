import base64
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

import bridge.config as config
from dependency_patch import dependency_module

_m_character_identity = dependency_module("bridge.character_identity")
_m_main = dependency_module("bridge.main")
_m_memory_curator = dependency_module("bridge.memory_curator")
_m_panel_callback_routes = dependency_module("bridge.panel_callback_routes")
_m_session_naming = dependency_module("bridge.session_naming")


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _card_png(name: str, pixel=(1, 2, 3, 255)) -> bytes:
    card = {"spec": "chara_card_v2", "data": {"name": name, "description": "", "first_mes": ""}}
    metadata = b"chara\x00" + base64.b64encode(json.dumps(card).encode("utf-8"))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    image = zlib.compress(bytes([0, *pixel]))
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"tEXt", metadata) + _chunk(b"IDAT", image) + _chunk(b"IEND", b"")


class CharacterRenameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_dir = _m_main.CHARACTER_DIR
        self.old_config_dir = config.CHARACTER_DIR
        self.old_backup = _m_character_identity.CHARACTER_BACKUP_DIR
        self.old_card = _m_panel_callback_routes.CARD_FILE
        self.old_config_card = config.CARD_FILE
        self.old_db = config.DB_FILE
        character_dir = root / "characters"
        card_file = character_dir / "Default.png"
        _m_main.CHARACTER_DIR = character_dir
        config.CHARACTER_DIR = character_dir
        _m_character_identity.CHARACTER_BACKUP_DIR = root / "backups"
        _m_panel_callback_routes.CARD_FILE = card_file
        config.CARD_FILE = card_file
        config.DB_FILE = root / "bridge.sqlite3"
        _m_main.CHARACTER_DIR.mkdir()
        _m_character_identity.CHARACTER_BACKUP_DIR.mkdir()
        self.db = _m_memory_curator.db_connect()
        self.session = _m_session_naming.create_session(self.db, "chat", "provider/model", session_id="active")
        _m_session_naming.update_session(self.db, "chat", "active", character_file="Old.png")

    def tearDown(self):
        self.db.close()
        _m_main.CHARACTER_DIR = self.old_dir
        config.CHARACTER_DIR = self.old_config_dir
        _m_character_identity.CHARACTER_BACKUP_DIR = self.old_backup
        _m_panel_callback_routes.CARD_FILE = self.old_card
        config.CARD_FILE = self.old_config_card
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def test_unique_visual_fingerprint_rebinds_renamed_card(self):
        (_m_character_identity.CHARACTER_BACKUP_DIR / "Old.png").write_bytes(_card_png("Old"))
        (_m_main.CHARACTER_DIR / "Renamed.png").write_bytes(_card_png("New Display Name"))
        session = _m_memory_curator.load_session(self.db, "chat", "active", "provider/model")
        repaired = _m_character_identity.reconcile_session_character(self.db, "chat", session)
        self.assertEqual(repaired["character_file"], "Renamed.png")
        stored = _m_memory_curator.load_session(self.db, "chat", "active", "provider/model")
        self.assertEqual(stored["character_file"], "Renamed.png")

    def test_ambiguous_visual_match_does_not_rebind(self):
        (_m_character_identity.CHARACTER_BACKUP_DIR / "Old.png").write_bytes(_card_png("Old"))
        (_m_main.CHARACTER_DIR / "One.png").write_bytes(_card_png("One"))
        (_m_main.CHARACTER_DIR / "Two.png").write_bytes(_card_png("Two"))
        self.assertEqual(_m_character_identity.resolve_renamed_character("Old.png"), "")
        session = _m_memory_curator.load_session(self.db, "chat", "active", "provider/model")
        self.assertEqual(_m_character_identity.reconcile_session_character(self.db, "chat", session)["character_file"], "Old.png")

    def test_unique_embedded_name_is_safe_fallback(self):
        (_m_main.CHARACTER_DIR / "different-file.png").write_bytes(_card_png("Old", pixel=(9, 8, 7, 255)))
        self.assertEqual(_m_character_identity.resolve_renamed_character("Old.png"), "different-file.png")

    def test_character_panel_rereads_embedded_name_and_has_refresh(self):
        (_m_main.CHARACTER_DIR / "Renamed.png").write_bytes(_card_png("Fresh Name"))
        calls = []
        original = _m_panel_callback_routes.telegram_request
        _m_panel_callback_routes.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            _m_session_naming.send_character_menu("token", "chat", "Renamed.png")
        finally:
            _m_panel_callback_routes.telegram_request = original
        payload = calls[-1][1]
        labels = [button["text"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]
        callbacks = [button["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]
        self.assertTrue(any("Fresh Name" in label for label in labels))
        self.assertIn("character:menu", callbacks)
        self.assertIn("Current character: Fresh Name", payload["text"])

    def test_character_refresh_treats_unchanged_edit_as_success(self):
        (_m_main.CHARACTER_DIR / "Old.png").write_bytes(_card_png("Old"))
        callback = {
            "id": "callback",
            "data": "character:menu",
            "message": {"message_id": 10, "chat": {"id": "chat"}},
        }
        answers = []
        original = _m_panel_callback_routes.telegram_request
        _m_panel_callback_routes.telegram_request = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Telegram editMessageText failed: Bad Request: message is not modified")
        )
        try:
            handled = _m_panel_callback_routes.handle_character_callback(
                self.db,
                "token",
                callback,
                lambda _token, _callback_id, text: answers.append(text),
                "character:menu",
                "chat",
                callback["message"],
                _m_memory_curator.load_session(self.db, "chat", "active", "provider/model"),
                "active",
                None,
            )
        finally:
            _m_panel_callback_routes.telegram_request = original
        self.assertTrue(handled)
        self.assertEqual(answers, ["Refreshed"])


if __name__ == "__main__":
    unittest.main()

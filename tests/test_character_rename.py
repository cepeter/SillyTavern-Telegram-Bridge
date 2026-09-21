import base64
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

import bridge.config as config
from runtime_test_facade import runtime as rt


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
        self.old_dir = rt.CHARACTER_DIR
        self.old_config_dir = config.CHARACTER_DIR
        self.old_backup = rt.CHARACTER_BACKUP_DIR
        self.old_card = rt.CARD_FILE
        self.old_config_card = config.CARD_FILE
        self.old_db = config.DB_FILE
        character_dir = root / "characters"
        card_file = character_dir / "Default.png"
        rt.CHARACTER_DIR = character_dir
        config.CHARACTER_DIR = character_dir
        rt.CHARACTER_BACKUP_DIR = root / "backups"
        rt.CARD_FILE = card_file
        config.CARD_FILE = card_file
        config.DB_FILE = root / "bridge.sqlite3"
        rt.CHARACTER_DIR.mkdir()
        rt.CHARACTER_BACKUP_DIR.mkdir()
        self.db = rt.db_connect()
        self.session = rt.create_session(self.db, "chat", "provider/model", session_id="active")
        rt.update_session(self.db, "chat", "active", character_file="Old.png")

    def tearDown(self):
        self.db.close()
        rt.CHARACTER_DIR = self.old_dir
        config.CHARACTER_DIR = self.old_config_dir
        rt.CHARACTER_BACKUP_DIR = self.old_backup
        rt.CARD_FILE = self.old_card
        config.CARD_FILE = self.old_config_card
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def test_unique_visual_fingerprint_rebinds_renamed_card(self):
        (rt.CHARACTER_BACKUP_DIR / "Old.png").write_bytes(_card_png("Old"))
        (rt.CHARACTER_DIR / "Renamed.png").write_bytes(_card_png("New Display Name"))
        session = rt.load_session(self.db, "chat", "active", "provider/model")
        repaired = rt.reconcile_session_character(self.db, "chat", session)
        self.assertEqual(repaired["character_file"], "Renamed.png")
        stored = rt.load_session(self.db, "chat", "active", "provider/model")
        self.assertEqual(stored["character_file"], "Renamed.png")

    def test_ambiguous_visual_match_does_not_rebind(self):
        (rt.CHARACTER_BACKUP_DIR / "Old.png").write_bytes(_card_png("Old"))
        (rt.CHARACTER_DIR / "One.png").write_bytes(_card_png("One"))
        (rt.CHARACTER_DIR / "Two.png").write_bytes(_card_png("Two"))
        self.assertEqual(rt.resolve_renamed_character("Old.png"), "")
        session = rt.load_session(self.db, "chat", "active", "provider/model")
        self.assertEqual(rt.reconcile_session_character(self.db, "chat", session)["character_file"], "Old.png")

    def test_unique_embedded_name_is_safe_fallback(self):
        (rt.CHARACTER_DIR / "different-file.png").write_bytes(_card_png("Old", pixel=(9, 8, 7, 255)))
        self.assertEqual(rt.resolve_renamed_character("Old.png"), "different-file.png")

    def test_character_panel_rereads_embedded_name_and_has_refresh(self):
        (rt.CHARACTER_DIR / "Renamed.png").write_bytes(_card_png("Fresh Name"))
        calls = []
        original = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_character_menu("token", "chat", "Renamed.png")
        finally:
            rt.telegram_request = original
        payload = calls[-1][1]
        labels = [button["text"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]
        callbacks = [button["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]
        self.assertTrue(any("Fresh Name" in label for label in labels))
        self.assertIn("character:menu", callbacks)
        self.assertIn("Current character: Fresh Name", payload["text"])

    def test_character_refresh_treats_unchanged_edit_as_success(self):
        (rt.CHARACTER_DIR / "Old.png").write_bytes(_card_png("Old"))
        callback = {
            "id": "callback",
            "data": "character:menu",
            "message": {"message_id": 10, "chat": {"id": "chat"}},
        }
        answers = []
        original = rt.telegram_request
        rt.telegram_request = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Telegram editMessageText failed: Bad Request: message is not modified")
        )
        try:
            handled = rt.handle_character_callback(
                self.db,
                "token",
                callback,
                lambda _token, _callback_id, text: answers.append(text),
                "character:menu",
                "chat",
                callback["message"],
                rt.load_session(self.db, "chat", "active", "provider/model"),
                "active",
                None,
            )
        finally:
            rt.telegram_request = original
        self.assertTrue(handled)
        self.assertEqual(answers, ["Refreshed"])


if __name__ == "__main__":
    unittest.main()

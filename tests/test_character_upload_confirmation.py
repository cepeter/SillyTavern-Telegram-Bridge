from application_test_setup import ensure_application_extensions

ensure_application_extensions()

import base64
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from settings_test_support import SettingsTestCase

import bridge.native_imports as native_imports
from bridge.memory_curator import db_connect
from bridge.request_types import RequestContext


def _card_png(name: str, description: str = "") -> bytes:
    card = {
        "name": name,
        "description": description,
        "personality": "",
        "scenario": "",
        "first_mes": "",
        "mes_example": "",
    }
    encoded = base64.b64encode(json.dumps(card).encode("utf-8"))
    chunk_data = b"chara\x00" + encoded
    chunk = (
        struct.pack(">I", len(chunk_data))
        + b"tEXt"
        + chunk_data
        + struct.pack(">I", zlib.crc32(b"tEXt" + chunk_data) & 0xFFFFFFFF)
    )
    return b"\x89PNG\r\n\x1a\n" + chunk


class CharacterUploadConfirmationTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.app_settings_builder.db_file = base / "bridge.sqlite3"
        self.app_settings_builder.character_dir = base / "characters"
        self.app_settings_builder.character_backup_dir = base / "backups"
        self.app_settings = self.app_settings_builder.build()
        self.db = db_connect(app_settings=self.app_settings)
        self.character_dir = self.app_settings.character_dir
        self.character_dir.mkdir(parents=True, exist_ok=True)
        self.app_settings.character_backup_dir.mkdir(parents=True, exist_ok=True)
        self.context = RequestContext(self.db, "s", "u", app_settings=self.app_settings)
        self.panels = []
        self._panel_patcher = mock.patch.object(
            native_imports,
            "send_panel_request",
            side_effect=lambda _t, method, payload, **kwargs: self.panels.append(payload),
        )
        self._panel_patcher.start()
        self.sent_text = []
        self._text_patcher = mock.patch.object(
            native_imports, "send_text", side_effect=lambda _t, _c, message: self.sent_text.append(message)
        )
        self._text_patcher.start()

    def tearDown(self):
        self._text_patcher.stop()
        self._panel_patcher.stop()
        self.db.close()
        self.tmp.cleanup()

    def _install(self, name: str, description: str, request_context=None) -> bytes:
        raw = _card_png(name, description)
        native_imports.import_character_card(
            self.db,
            "token",
            "chat",
            f"{name}.png",
            raw,
            app_settings=self.app_settings,
            request_context=request_context or self.context,
        )
        return raw

    def test_reupload_same_name_stages_pending_instead_of_overwriting(self):
        original = self._install("Alice", "original")
        updated = _card_png("Alice", "updated")
        native_imports.import_character_card(
            self.db, "token", "chat", "Alice.png", updated, app_settings=self.app_settings, request_context=self.context
        )
        self.assertEqual((self.character_dir / "Alice.png").read_bytes(), original)
        self.assertTrue(list((self.app_settings.character_backup_dir / ".pending").glob("*.bin")))
        self.assertFalse((self.character_dir / ".pending_chat.png").exists())
        self.assertTrue(any("already exists" in p["text"] for p in self.panels))

    def test_reupload_with_context_sends_confirmation_panel(self):
        self._install("Alice", "original")
        updated = _card_png("Alice", "updated")
        ctx = RequestContext(self.db, "s", "u", app_settings=self.app_settings)
        panels = []
        with mock.patch.object(
            native_imports,
            "send_panel_request",
            side_effect=lambda _t, method, payload, *, request_context: panels.append((method, payload)),
        ):
            native_imports.import_character_card(
                self.db, "token", "chat", "Alice.png", updated, app_settings=self.app_settings, request_context=ctx
            )
        self.assertEqual(panels[0][0], "sendMessage")
        self.assertIn("already exists", panels[0][1]["text"])
        self.assertIn("characterupload:overwrite", json.dumps(panels[0][1]["reply_markup"]))

    def test_apply_overwrite_replaces_existing(self):
        original = self._install("Alice", "original")
        updated = _card_png("Alice", "updated")
        native_imports.import_character_card(
            self.db, "token", "chat", "Alice.png", updated, app_settings=self.app_settings, request_context=self.context
        )
        message = self._apply("overwrite")
        self.assertIn("overwritten", message)
        self.assertNotEqual((self.character_dir / "Alice.png").read_bytes(), original)

    def test_apply_newversion_keeps_original(self):
        original = self._install("Alice", "original")
        updated = _card_png("Alice", "updated")
        native_imports.import_character_card(
            self.db, "token", "chat", "Alice.png", updated, app_settings=self.app_settings, request_context=self.context
        )
        message = self._apply("newversion")
        self.assertIn("installed", message)
        self.assertEqual((self.character_dir / "Alice.png").read_bytes(), original)
        versions = [p.name for p in self.character_dir.glob("Alice-*.png")]
        self.assertEqual(len(versions), 1)

    def test_apply_keep_discards_pending(self):
        original = self._install("Alice", "original")
        updated = _card_png("Alice", "updated")
        native_imports.import_character_card(
            self.db, "token", "chat", "Alice.png", updated, app_settings=self.app_settings, request_context=self.context
        )
        message = self._apply("keep")
        self.assertIn("Kept", message)
        self.assertEqual((self.character_dir / "Alice.png").read_bytes(), original)
        self.assertFalse((self.character_dir / ".pending_chat.png").exists())

    def _apply(self, action):
        nonce = self.panels[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].rsplit(":", 1)[1]
        message, _filename = native_imports.apply_character_proposal(
            self.db, "chat", nonce, action, request_context=self.context
        )
        return message

    def test_apply_without_pending_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "expired or already used"):
            native_imports.apply_character_proposal(
                self.db, "chat", "a" * 24, "overwrite", request_context=self.context
            )


if __name__ == "__main__":
    unittest.main()

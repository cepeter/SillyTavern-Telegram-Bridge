import json
from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class NativePersonaSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_settings = rt.NATIVE_PERSONA_SETTINGS_FILE
        self.old_avatars = rt.NATIVE_PERSONA_AVATAR_DIR
        self.old_backups = rt.NATIVE_PERSONA_BACKUP_DIR
        self.old_cache = rt._NATIVE_PERSONA_CACHE
        self.old_cache_time = rt._NATIVE_PERSONA_CACHE_LAST_REFRESH
        self.old_phase3 = rt.phase3_api_configured
        rt.NATIVE_PERSONA_SETTINGS_FILE = root / "settings.json"
        rt.NATIVE_PERSONA_AVATAR_DIR = root / "avatars"
        rt.NATIVE_PERSONA_BACKUP_DIR = root / "backups"
        rt.NATIVE_PERSONA_AVATAR_DIR.mkdir()
        (rt.NATIVE_PERSONA_AVATAR_DIR / "user-default.png").write_bytes(b"avatar")
        self.settings = {"power_user": {"personas": {"native.png": "Native"}, "persona_descriptions": {"native.png": {"description": "Native desc", "title": "Keep"}}}, "default_persona": "native.png", "unrelated": {"keep": True}}
        rt.NATIVE_PERSONA_SETTINGS_FILE.write_text(json.dumps(self.settings), encoding="utf-8")
        rt._NATIVE_PERSONA_CACHE = {}
        rt._NATIVE_PERSONA_CACHE_LAST_REFRESH = 0
        rt.phase3_api_configured = lambda: False
        self.calls = []
        self.old_request = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: self.calls.append((method, payload)) or {}

    def tearDown(self):
        rt.telegram_request = self.old_request
        rt.phase3_api_configured = self.old_phase3
        rt._NATIVE_PERSONA_CACHE = self.old_cache
        rt._NATIVE_PERSONA_CACHE_LAST_REFRESH = self.old_cache_time
        rt.NATIVE_PERSONA_SETTINGS_FILE = self.old_settings
        rt.NATIVE_PERSONA_AVATAR_DIR = self.old_avatars
        rt.NATIVE_PERSONA_BACKUP_DIR = self.old_backups
        self.tmp.cleanup()

    def _read(self):
        return json.loads(rt.NATIVE_PERSONA_SETTINGS_FILE.read_text(encoding="utf-8"))

    def test_loader_reads_native_settings_without_bridge_json(self):
        personas = rt.load_personas()
        self.assertEqual(personas["native.png"]["name"], "Native")
        self.assertEqual(personas["native.png"]["description"], "Native desc")
        self.assertEqual(personas["native.png"]["sillytavern_avatar"], "native.png")

    def test_upsert_preserves_unrelated_native_settings_and_descriptor_fields(self):
        avatar = rt.upsert_native_persona("native.png", "Updated", "Updated desc")
        data = self._read()
        self.assertEqual(avatar, "native.png")
        self.assertEqual(data["power_user"]["personas"][avatar], "Updated")
        self.assertEqual(data["power_user"]["persona_descriptions"][avatar]["description"], "Updated desc")
        self.assertEqual(data["power_user"]["persona_descriptions"][avatar]["title"], "Keep")
        self.assertEqual(data["default_persona"], "native.png")
        self.assertEqual(data["unrelated"], {"keep": True})

    def test_upsert_allocates_native_avatar_for_new_persona(self):
        avatar = rt.upsert_native_persona("writer", "Writer", "Writer description")
        self.assertEqual(avatar, "bridge-writer.png")
        self.assertTrue((rt.NATIVE_PERSONA_AVATAR_DIR / avatar).is_file())
        self.assertEqual(self._read()["power_user"]["personas"][avatar], "Writer")

    def test_persona_panel_has_no_bridge_import_export_actions(self):
        rt.send_persona_menu("token", "chat", "native.png")
        callbacks = {button["callback_data"] for row in self.calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row}
        self.assertNotIn("persona:native_import", callbacks)
        self.assertNotIn("persona:native_export", callbacks)


if __name__ == "__main__":
    unittest.main()

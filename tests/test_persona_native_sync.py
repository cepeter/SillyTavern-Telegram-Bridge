import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import logging
from dependency_patch import dependency_module

_m_command_routes = dependency_module("bridge.command_routes")
_m_panel_callback_routes = dependency_module("bridge.panel_callback_routes")
_m_persona_sync = dependency_module("bridge.persona_sync")
_m_sync_core = dependency_module("bridge.sync_core")


class NativePersonaSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_settings = _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE
        self.old_avatars = _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR
        self.old_backups = _m_persona_sync.NATIVE_PERSONA_BACKUP_DIR
        self.old_cache = _m_persona_sync._NATIVE_PERSONA_CACHE
        self.old_cache_time = _m_persona_sync._NATIVE_PERSONA_CACHE_LAST_REFRESH
        self.old_phase3 = _m_persona_sync.phase3_api_configured
        _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE = root / "settings.json"
        _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR = root / "avatars"
        _m_persona_sync.NATIVE_PERSONA_BACKUP_DIR = root / "backups"
        _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR.mkdir()
        (_m_persona_sync.NATIVE_PERSONA_AVATAR_DIR / "user-default.png").write_bytes(b"avatar")
        self.settings = {"power_user": {"personas": {"native.png": "Native"}, "persona_descriptions": {"native.png": {"description": "Native desc", "title": "Keep"}}}, "default_persona": "native.png", "unrelated": {"keep": True}}
        _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE.write_text(json.dumps(self.settings), encoding="utf-8")
        _m_persona_sync._NATIVE_PERSONA_CACHE = {}
        _m_persona_sync._NATIVE_PERSONA_CACHE_LAST_REFRESH = 0
        _m_persona_sync.phase3_api_configured = lambda: False
        self.calls = []
        self.old_request = _m_panel_callback_routes.telegram_request
        _m_panel_callback_routes.telegram_request = lambda _token, method, payload: self.calls.append((method, payload)) or {}

    def tearDown(self):
        _m_panel_callback_routes.telegram_request = self.old_request
        _m_persona_sync.phase3_api_configured = self.old_phase3
        _m_persona_sync._NATIVE_PERSONA_CACHE = self.old_cache
        _m_persona_sync._NATIVE_PERSONA_CACHE_LAST_REFRESH = self.old_cache_time
        _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE = self.old_settings
        _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR = self.old_avatars
        _m_persona_sync.NATIVE_PERSONA_BACKUP_DIR = self.old_backups
        self.tmp.cleanup()

    def _read(self):
        return json.loads(_m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE.read_text(encoding="utf-8"))

    def test_loader_reads_native_settings_without_bridge_json(self):
        personas = _m_persona_sync.load_personas()
        self.assertEqual(personas["native.png"]["name"], "Native")
        self.assertEqual(personas["native.png"]["description"], "Native desc")
        self.assertEqual(personas["native.png"]["sillytavern_avatar"], "native.png")


    def test_loader_failure_returns_empty_and_logs_final_warning(self):
        with patch.object(
            _m_persona_sync,
            "load_native_personas",
            side_effect=RuntimeError("boom"),
        ), patch.object(
            logging,
            "warning",
        ) as warning:
            result = _m_persona_sync.load_personas()

        self.assertEqual(result, {})
        warning.assert_called_once_with(
            "Could not load native Persona metadata",
            exc_info=True,
        )

    def test_persona_read_helpers_use_final_runtime_loader_at_call_time(self):
        personas = {
            "patched.png": {
                "name": "Patched",
                "description": "Patched description",
                "sillytavern_avatar": "patched.png",
            }
        }

        with patch.object(
            _m_persona_sync,
            "load_personas",
            return_value=personas,
        ), patch.object(
            _m_persona_sync,
            "_native_settings",
            return_value={
                "power_user": {
                    "default_persona": "patched.png",
                }
            },
        ):
            self.assertEqual(
                _m_sync_core.get_persona("patched.png"),
                personas["patched.png"],
            )
            self.assertEqual(
                _m_persona_sync.default_persona_id(),
                "patched.png",
            )
            self.assertEqual(
                _m_sync_core.persona_name("patched.png"),
                "Patched",
            )

    def test_upsert_preserves_unrelated_native_settings_and_descriptor_fields(self):
        avatar = _m_persona_sync.upsert_native_persona("native.png", "Updated", "Updated desc")
        data = self._read()
        self.assertEqual(avatar, "native.png")
        self.assertEqual(data["power_user"]["personas"][avatar], "Updated")
        self.assertEqual(data["power_user"]["persona_descriptions"][avatar]["description"], "Updated desc")
        self.assertEqual(data["power_user"]["persona_descriptions"][avatar]["title"], "Keep")
        self.assertEqual(data["default_persona"], "native.png")
        self.assertEqual(data["unrelated"], {"keep": True})

    def test_upsert_allocates_native_avatar_for_new_persona(self):
        avatar = _m_persona_sync.upsert_native_persona("writer", "Writer", "Writer description")
        self.assertEqual(avatar, "bridge-writer.png")
        self.assertTrue((_m_persona_sync.NATIVE_PERSONA_AVATAR_DIR / avatar).is_file())
        self.assertEqual(self._read()["power_user"]["personas"][avatar], "Writer")

    def test_persona_panel_has_no_bridge_import_export_actions(self):
        _m_command_routes.send_persona_menu("token", "chat", "native.png")
        callbacks = {button["callback_data"] for row in self.calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row}
        self.assertNotIn("persona:native_import", callbacks)
        self.assertNotIn("persona:native_export", callbacks)


if __name__ == "__main__":
    unittest.main()

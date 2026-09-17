import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class _FakeSettingsClient:
    def __init__(self, settings):
        self.settings = copy.deepcopy(settings)
        self.saved = 0
        self.reads = 0
        self.mutate_on_second_read = False

    def get_settings(self):
        self.reads += 1
        if self.mutate_on_second_read and self.reads == 2:
            self.settings["unrelated"] = "changed"
        return copy.deepcopy(self.settings)

    def save_settings(self, settings):
        self.saved += 1
        self.settings = copy.deepcopy(settings)


class NativePersonaSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_persona = rt.PERSONA_FILE
        self.old_settings = rt.NATIVE_PERSONA_SETTINGS_FILE
        self.old_avatars = rt.NATIVE_PERSONA_AVATAR_DIR
        self.old_backups = rt.NATIVE_PERSONA_BACKUP_DIR
        rt.PERSONA_FILE = root / "bridge-personas.json"
        rt.NATIVE_PERSONA_SETTINGS_FILE = root / "settings.json"
        rt.NATIVE_PERSONA_AVATAR_DIR = root / "User Avatars"
        rt.NATIVE_PERSONA_BACKUP_DIR = root / "backups"
        rt.NATIVE_PERSONA_AVATAR_DIR.mkdir()
        (rt.NATIVE_PERSONA_AVATAR_DIR / "user-default.png").write_bytes(b"valid-avatar-bytes")
        self.bridge = {
            "punto": {"name": "Punto", "description": "Bridge description", "tags": ["default"]},
        }
        rt.PERSONA_FILE.write_text(json.dumps(self.bridge), encoding="utf-8")
        self.native = {
            "user_avatar": "user-default.png",
            "power_user": {
                "personas": {},
                "default_persona": None,
                "persona_descriptions": {},
            },
            "unrelated": {"preserve": True},
        }
        rt.NATIVE_PERSONA_SETTINGS_FILE.write_text(json.dumps(self.native), encoding="utf-8")

    def tearDown(self):
        rt.PERSONA_FILE = self.old_persona
        rt.NATIVE_PERSONA_SETTINGS_FILE = self.old_settings
        rt.NATIVE_PERSONA_AVATAR_DIR = self.old_avatars
        rt.NATIVE_PERSONA_BACKUP_DIR = self.old_backups
        self.tmp.cleanup()

    def test_export_adds_native_metadata_preserves_settings_and_records_mapping(self):
        client = _FakeSettingsClient(self.native)
        result = rt.export_persona_to_native("punto", client)
        self.assertIn("bridge-punto.png", result)
        power = client.settings["power_user"]
        self.assertEqual(power["personas"]["bridge-punto.png"], "Punto")
        self.assertEqual(power["persona_descriptions"]["bridge-punto.png"]["description"], "Bridge description")
        self.assertEqual(client.settings["unrelated"], {"preserve": True})
        self.assertIsNone(power["default_persona"])
        bridge = json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(bridge["punto"]["sillytavern_avatar"], "bridge-punto.png")
        self.assertEqual(os.stat(rt.NATIVE_PERSONA_AVATAR_DIR / "bridge-punto.png").st_mode & 0o777, 0o644)
        backups = list(rt.NATIVE_PERSONA_BACKUP_DIR.glob("settings-persona-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(os.stat(backups[0]).st_mode & 0o777, 0o600)

    def test_export_never_deletes_native_personas_and_clones_avatar_when_needed(self):
        self.native["power_user"]["personas"] = {"user-default.png": "Native Existing"}
        self.native["power_user"]["persona_descriptions"] = {
            "user-default.png": {"description": "Keep me", "connections": ["character"]},
        }
        rt.NATIVE_PERSONA_SETTINGS_FILE.write_text(json.dumps(self.native), encoding="utf-8")
        client = _FakeSettingsClient(self.native)
        rt.export_persona_to_native("punto", client)
        power = client.settings["power_user"]
        self.assertEqual(power["personas"]["user-default.png"], "Native Existing")
        self.assertEqual(power["persona_descriptions"]["user-default.png"]["connections"], ["character"])
        self.assertEqual(power["personas"]["bridge-punto.png"], "Punto")
        self.assertTrue((rt.NATIVE_PERSONA_AVATAR_DIR / "bridge-punto.png").is_file())

    def test_import_updates_mapped_persona_and_adds_unmapped_native_persona(self):
        self.bridge["punto"]["sillytavern_avatar"] = "user-default.png"
        rt.PERSONA_FILE.write_text(json.dumps(self.bridge), encoding="utf-8")
        self.native["power_user"]["personas"] = {"user-default.png": "Native Punto", "writer.png": "Writer"}
        self.native["power_user"]["persona_descriptions"] = {
            "user-default.png": {"description": "Native description"},
            "writer.png": {"description": "Writes concise notes"},
        }
        client = _FakeSettingsClient(self.native)
        result = rt.import_native_personas(client)
        self.assertIn("1 added", result)
        self.assertIn("1 updated", result)
        bridge = json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(bridge["punto"]["name"], "Native Punto")
        self.assertEqual(bridge["punto"]["tags"], ["default"])
        self.assertEqual(bridge["writer"]["sillytavern_avatar"], "writer.png")

    def test_import_skips_invalid_or_empty_native_descriptions(self):
        self.native["power_user"]["personas"] = {"empty.png": "Empty", "../bad.png": "Bad"}
        self.native["power_user"]["persona_descriptions"] = {
            "empty.png": {"description": ""},
            "../bad.png": {"description": "Unsafe"},
        }
        result = rt.import_native_personas(_FakeSettingsClient(self.native))
        self.assertIn("2 skipped", result)
        self.assertEqual(json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8")), self.bridge)

    def test_export_aborts_when_settings_change_between_read_and_write(self):
        client = _FakeSettingsClient(self.native)
        client.mutate_on_second_read = True
        with self.assertRaisesRegex(ValueError, "changed during export"):
            rt.export_persona_to_native("punto", client)
        self.assertEqual(client.saved, 0)
        self.assertEqual(list(rt.NATIVE_PERSONA_BACKUP_DIR.glob("*")) if rt.NATIVE_PERSONA_BACKUP_DIR.exists() else [], [])

    def test_export_requires_a_matching_on_disk_backup_source(self):
        rt.NATIVE_PERSONA_SETTINGS_FILE.unlink()
        with self.assertRaisesRegex(OSError, "unavailable for backup"):
            rt.export_persona_to_native("punto", _FakeSettingsClient(self.native))
        self.assertFalse((rt.NATIVE_PERSONA_AVATAR_DIR / "bridge-punto.png").exists())

    def test_export_cleans_created_avatar_when_settings_save_fails(self):
        class RefusingClient(_FakeSettingsClient):
            def save_settings(self, settings):
                raise rt.SillyTavernApiError("save refused")

        with self.assertRaisesRegex(rt.SillyTavernApiError, "save refused"):
            rt.export_persona_to_native("punto", RefusingClient(self.native))
        self.assertFalse((rt.NATIVE_PERSONA_AVATAR_DIR / "bridge-punto.png").exists())

    def test_export_accepts_verified_target_when_unrelated_settings_change_after_save(self):
        class ConcurrentClient(_FakeSettingsClient):
            def get_settings(self):
                settings = super().get_settings()
                if self.saved and self.reads >= 3:
                    settings["unrelated"] = {"preserve": True, "changed": True}
                    self.settings = copy.deepcopy(settings)
                return settings

        client = ConcurrentClient(self.native)
        result = rt.export_persona_to_native("punto", client)
        self.assertIn("target persona verified", result)
        self.assertTrue((rt.NATIVE_PERSONA_AVATAR_DIR / "bridge-punto.png").is_file())
        self.assertEqual(json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))["punto"]["sillytavern_avatar"], "bridge-punto.png")

    def test_export_accepts_lost_save_response_when_target_readback_matches(self):
        class ResponseLostClient(_FakeSettingsClient):
            def save_settings(self, settings):
                super().save_settings(settings)
                raise rt.SillyTavernApiError("response lost", transient=True)

        result = rt.export_persona_to_native("punto", ResponseLostClient(self.native))
        self.assertIn("save response failed", result)
        self.assertTrue((rt.NATIVE_PERSONA_AVATAR_DIR / "bridge-punto.png").is_file())

    def test_export_releases_persona_lock_during_network_io(self):
        class InspectingClient(_FakeSettingsClient):
            def __init__(self, settings):
                super().__init__(settings)
                self.lock_was_free = []

            def get_settings(self):
                acquired = rt.PERSONA_EDIT_LOCK.acquire(blocking=False)
                self.lock_was_free.append(acquired)
                if acquired:
                    rt.PERSONA_EDIT_LOCK.release()
                return super().get_settings()

        client = InspectingClient(self.native)
        rt.export_persona_to_native("punto", client)
        self.assertTrue(client.lock_was_free)
        self.assertTrue(all(client.lock_was_free))

    def test_persona_menu_exposes_explicit_import_and_export(self):
        calls = []
        original = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload))
        try:
            rt.send_persona_menu("token", "chat", "punto")
        finally:
            rt.telegram_request = original
        callbacks = {button["callback_data"] for row in calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row}
        self.assertIn("persona:native_import", callbacks)
        self.assertIn("persona:native_export", callbacks)


if __name__ == "__main__":
    unittest.main()

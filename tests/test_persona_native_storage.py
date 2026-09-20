import json
from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class NativePersonaStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_settings = rt.NATIVE_PERSONA_SETTINGS_FILE
        self.old_avatars = rt.NATIVE_PERSONA_AVATAR_DIR
        self.old_backups = rt.NATIVE_PERSONA_BACKUP_DIR
        self.old_cache = rt._NATIVE_PERSONA_CACHE
        self.old_cache_time = rt._NATIVE_PERSONA_CACHE_LAST_REFRESH
        self.old_phase3 = rt.phase3_api_configured

        rt.NATIVE_PERSONA_SETTINGS_FILE = self.root / "settings.json"
        rt.NATIVE_PERSONA_AVATAR_DIR = self.root / "avatars"
        rt.NATIVE_PERSONA_BACKUP_DIR = self.root / "backups"
        rt.NATIVE_PERSONA_AVATAR_DIR.mkdir()
        (rt.NATIVE_PERSONA_AVATAR_DIR / "source.webp").write_bytes(
            b"webp-source-bytes"
        )
        self.settings = {
            "user_avatar": "source.webp",
            "power_user": {
                "personas": {
                    "native.png": "Native",
                },
                "persona_descriptions": {
                    "native.png": {
                        "description": "Native description",
                    },
                },
            },
            "unrelated": {"keep": True},
        }
        rt.NATIVE_PERSONA_SETTINGS_FILE.write_text(
            json.dumps(self.settings),
            encoding="utf-8",
        )
        rt._NATIVE_PERSONA_CACHE = {}
        rt._NATIVE_PERSONA_CACHE_LAST_REFRESH = 0
        rt.phase3_api_configured = lambda: False

    def tearDown(self):
        rt.phase3_api_configured = self.old_phase3
        rt._NATIVE_PERSONA_CACHE = self.old_cache
        rt._NATIVE_PERSONA_CACHE_LAST_REFRESH = self.old_cache_time
        rt.NATIVE_PERSONA_SETTINGS_FILE = self.old_settings
        rt.NATIVE_PERSONA_AVATAR_DIR = self.old_avatars
        rt.NATIVE_PERSONA_BACKUP_DIR = self.old_backups
        self.tmp.cleanup()

    def _settings(self):
        return json.loads(
            rt.NATIVE_PERSONA_SETTINGS_FILE.read_text(
                encoding="utf-8"
            )
        )

    def test_explicit_persona_store_preserves_source_extension_and_bytes(self):
        avatar = rt._PERSONA_STORE.upsert(
            "writer",
            "Writer",
            "Writer description",
        )

        self.assertEqual(avatar, "bridge-writer.webp")
        self.assertEqual(
            (rt.NATIVE_PERSONA_AVATAR_DIR / avatar).read_bytes(),
            b"webp-source-bytes",
        )
        settings = self._settings()
        self.assertEqual(
            settings["power_user"]["personas"][avatar],
            "Writer",
        )
        self.assertEqual(
            settings["unrelated"],
            {"keep": True},
        )

    def test_explicit_persona_store_rejects_duplicate_bridge_stem(self):
        first = rt._PERSONA_STORE.upsert(
            "writer",
            "Writer",
            "Writer description",
        )
        self.assertEqual(first, "bridge-writer.webp")

        with self.assertRaisesRegex(
            ValueError,
            "Persona ID already exists",
        ):
            rt._PERSONA_STORE.upsert(
                "writer",
                "Writer 2",
                "Another description",
            )

    def test_delete_preserves_avatar_media(self):
        avatar = rt._PERSONA_STORE.upsert(
            "writer",
            "Writer",
            "Writer description",
        )
        path = rt.NATIVE_PERSONA_AVATAR_DIR / avatar

        self.assertTrue(
            rt._PERSONA_STORE.delete(avatar)
        )

        self.assertTrue(path.is_file())
        self.assertNotIn(
            avatar,
            self._settings()["power_user"]["personas"],
        )

    def test_persona_service_lock_can_nest_into_integrity_store(self):
        service = rt.compatibility_persona_service()
        db = rt.db_connect(
            self.root / "persona-service.sqlite3"
        )
        try:
            session = rt.create_session(
                db,
                "chat",
                rt.DEFAULT_MODEL,
            )
            avatar = service.create_and_select(
                db,
                "chat",
                session["session_id"],
                "nested",
                "Nested",
                "Nested lock test",
            )
        finally:
            db.close()

        self.assertTrue(
            Path(avatar).stem.startswith("bridge-nested")
        )


class NativePersonaSourceBoundaryTests(unittest.TestCase):
    def test_persona_sync_owns_avatar_allocator_and_explicit_store(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "persona_sync.py"
        ).read_text(encoding="utf-8")

        self.assertIn("def _choose_native_avatar(", source)
        self.assertIn(
            "_PERSONA_STORE = _IntegrityCheckedPersonaStore(",
            source,
        )
        self.assertIn(
            "def _upsert_native_persona_storage(",
            source,
        )
        self.assertIn(
            "def _delete_native_persona_storage(",
            source,
        )


if __name__ == "__main__":
    unittest.main()

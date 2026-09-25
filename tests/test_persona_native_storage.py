from application_test_setup import ensure_application_extensions, make_native_test_persona_service

ensure_application_extensions()

import json
import tempfile
import unittest
from pathlib import Path

import bridge.memory_curator as _m_memory_curator
import bridge.persona_sync as _m_persona_sync
import bridge.session_naming as _m_session_naming
import bridge.sillytavern_api as _m_sillytavern_api


class NativePersonaStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_settings = _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE
        self.old_avatars = _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR
        self.old_backups = _m_persona_sync.NATIVE_PERSONA_BACKUP_DIR
        self.old_cache = _m_persona_sync._NATIVE_PERSONA_CACHE
        self.old_cache_time = _m_persona_sync._NATIVE_PERSONA_CACHE_LAST_REFRESH
        self.old_phase3 = _m_sillytavern_api.live_sync_api_configured

        _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE = self.root / "settings.json"
        _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR = self.root / "avatars"
        _m_persona_sync.NATIVE_PERSONA_BACKUP_DIR = self.root / "backups"
        _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR.mkdir()
        (_m_persona_sync.NATIVE_PERSONA_AVATAR_DIR / "source.webp").write_bytes(b"webp-source-bytes")
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
        _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE.write_text(
            json.dumps(self.settings),
            encoding="utf-8",
        )
        _m_persona_sync._NATIVE_PERSONA_CACHE = {}
        _m_persona_sync._NATIVE_PERSONA_CACHE_LAST_REFRESH = 0
        _m_sillytavern_api.live_sync_api_configured = lambda: False

    def tearDown(self):
        _m_sillytavern_api.live_sync_api_configured = self.old_phase3
        _m_persona_sync._NATIVE_PERSONA_CACHE = self.old_cache
        _m_persona_sync._NATIVE_PERSONA_CACHE_LAST_REFRESH = self.old_cache_time
        _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE = self.old_settings
        _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR = self.old_avatars
        _m_persona_sync.NATIVE_PERSONA_BACKUP_DIR = self.old_backups
        self.tmp.cleanup()

    def _settings(self):
        return json.loads(_m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE.read_text(encoding="utf-8"))

    def test_explicit_persona_store_preserves_source_extension_and_bytes(self):
        avatar = _m_persona_sync._PERSONA_STORE.upsert(
            "writer",
            "Writer",
            "Writer description",
        )

        self.assertEqual(avatar, "bridge-writer.webp")
        self.assertEqual(
            (_m_persona_sync.NATIVE_PERSONA_AVATAR_DIR / avatar).read_bytes(),
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
        first = _m_persona_sync._PERSONA_STORE.upsert(
            "writer",
            "Writer",
            "Writer description",
        )
        self.assertEqual(first, "bridge-writer.webp")

        with self.assertRaisesRegex(
            ValueError,
            "Persona ID already exists",
        ):
            _m_persona_sync._PERSONA_STORE.upsert(
                "writer",
                "Writer 2",
                "Another description",
            )

    def test_delete_preserves_avatar_media(self):
        avatar = _m_persona_sync._PERSONA_STORE.upsert(
            "writer",
            "Writer",
            "Writer description",
        )
        path = _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR / avatar

        self.assertTrue(_m_persona_sync._PERSONA_STORE.delete(avatar))

        self.assertTrue(path.is_file())
        self.assertNotIn(
            avatar,
            self._settings()["power_user"]["personas"],
        )

    def test_public_persona_functions_use_explicit_store(self):
        avatar = _m_persona_sync.upsert_native_persona(
            "public",
            "Public",
            "Public description",
        )
        self.assertTrue(Path(avatar).stem.startswith("bridge-public"))

        self.assertTrue(_m_persona_sync.delete_native_persona(avatar))
        self.assertTrue((_m_persona_sync.NATIVE_PERSONA_AVATAR_DIR / avatar).is_file())

    def test_persona_service_lock_can_nest_into_integrity_store(self):
        service = make_native_test_persona_service()
        db = _m_memory_curator.db_connect(self.root / "persona-service.sqlite3")
        try:
            session = _m_session_naming.create_session(
                db,
                "chat",
                _m_memory_curator.DEFAULT_MODEL,
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

        self.assertTrue(Path(avatar).stem.startswith("bridge-nested"))


class NativePersonaSourceBoundaryTests(unittest.TestCase):
    def test_persona_sync_owns_avatar_allocator_and_explicit_store(self):
        source = (Path(__file__).parents[1] / "bridge" / "persona_sync.py").read_text(encoding="utf-8")

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

    def test_persona_sync_is_the_only_runtime_load_personas_definition(self):
        root = Path(__file__).parents[1] / "bridge"
        cards = (root / "cards.py").read_text(encoding="utf-8")
        persona_sync = (root / "persona_sync.py").read_text(encoding="utf-8")

        self.assertNotIn(
            "def load_personas(",
            cards,
        )
        self.assertIn(
            "def load_personas(",
            persona_sync,
        )
        self.assertIn(
            "Could not load native Persona metadata",
            persona_sync,
        )

    def test_state_integrity_no_longer_owns_persona_storage(self):
        path = Path(__file__).parents[1] / "bridge" / "state_integrity.py"
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()

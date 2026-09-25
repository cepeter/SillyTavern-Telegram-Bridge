from application_test_setup import ensure_application_extensions, make_native_test_persona_service
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import json
import tempfile
import unittest
from pathlib import Path

import bridge.memory_curator as _m_memory_curator
import bridge.persona_sync as _m_persona_sync
import bridge.session_naming as _m_session_naming
import bridge.sillytavern_api as _m_sillytavern_api


class NativePersonaStorageTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_settings = self.app_settings_builder.native_persona_settings_file
        self.old_avatars = self.app_settings_builder.native_persona_avatar_dir
        self.old_backups = self.app_settings_builder.native_persona_backup_dir
        self.old_phase3 = _m_sillytavern_api.live_sync_api_configured

        self.app_settings_builder.native_persona_settings_file = self.root / "settings.json"
        self.app_settings_builder.native_persona_avatar_dir = self.root / "avatars"
        self.app_settings_builder.native_persona_backup_dir = self.root / "backups"
        self.app_settings_builder.native_persona_avatar_dir.mkdir()
        (self.app_settings_builder.native_persona_avatar_dir / "source.webp").write_bytes(b"webp-source-bytes")
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
        self.app_settings_builder.native_persona_settings_file.write_text(
            json.dumps(self.settings),
            encoding="utf-8",
        )
        _m_sillytavern_api.live_sync_api_configured = lambda *, app_settings=None: False

    def tearDown(self):
        _m_sillytavern_api.live_sync_api_configured = self.old_phase3
        self.app_settings_builder.native_persona_settings_file = self.old_settings
        self.app_settings_builder.native_persona_avatar_dir = self.old_avatars
        self.app_settings_builder.native_persona_backup_dir = self.old_backups
        self.tmp.cleanup()

    def _settings(self):
        return json.loads(self.app_settings_builder.native_persona_settings_file.read_text(encoding="utf-8"))

    def test_explicit_persona_store_preserves_source_extension_and_bytes(self):
        avatar = _m_persona_sync._persona_store(app_settings=self.app_settings_builder.build()).upsert(
            "writer",
            "Writer",
            "Writer description",
        )

        self.assertEqual(avatar, "bridge-writer.webp")
        self.assertEqual(
            (self.app_settings_builder.native_persona_avatar_dir / avatar).read_bytes(),
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
        first = _m_persona_sync._persona_store(app_settings=self.app_settings_builder.build()).upsert(
            "writer",
            "Writer",
            "Writer description",
        )
        self.assertEqual(first, "bridge-writer.webp")

        with self.assertRaisesRegex(
            ValueError,
            "Persona ID already exists",
        ):
            _m_persona_sync._persona_store(app_settings=self.app_settings_builder.build()).upsert(
                "writer",
                "Writer 2",
                "Another description",
            )

    def test_delete_preserves_avatar_media(self):
        avatar = _m_persona_sync._persona_store(app_settings=self.app_settings_builder.build()).upsert(
            "writer",
            "Writer",
            "Writer description",
        )
        path = self.app_settings_builder.native_persona_avatar_dir / avatar

        self.assertTrue(_m_persona_sync._persona_store(app_settings=self.app_settings_builder.build()).delete(avatar))

        self.assertTrue(path.is_file())
        self.assertNotIn(
            avatar,
            self._settings()["power_user"]["personas"],
        )

    def test_public_persona_functions_use_explicit_store(self):
        avatar = _m_persona_sync.upsert_native_persona(
            "public", "Public", "Public description", app_settings=self.app_settings_builder.build()
        )
        self.assertTrue(Path(avatar).stem.startswith("bridge-public"))

        self.assertTrue(_m_persona_sync.delete_native_persona(avatar, app_settings=self.app_settings_builder.build()))
        self.assertTrue((self.app_settings_builder.native_persona_avatar_dir / avatar).is_file())

    def test_persona_service_lock_can_nest_into_integrity_store(self):
        service = make_native_test_persona_service(app_settings=self.app_settings_builder.build())
        db = _m_memory_curator.db_connect(
            self.root / "persona-service.sqlite3", app_settings=self.app_settings_builder.build()
        )
        try:
            session = _m_session_naming.create_session(
                db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
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


class NativePersonaSourceBoundaryTests(SettingsTestCase):
    def test_persona_sync_owns_avatar_allocator_and_explicit_store(self):
        source = (Path(__file__).parents[1] / "bridge" / "persona_sync.py").read_text(encoding="utf-8")

        self.assertIn("def _choose_native_avatar(", source)
        self.assertIn(
            "return _IntegrityCheckedPersonaStore(",
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


if __name__ == "__main__":
    unittest.main()

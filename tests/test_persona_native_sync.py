from application_test_setup import (
    ensure_application_extensions,
    make_native_test_persona_service,
    make_test_request_context,
)
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import json
import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge.cards as _m_cards
import bridge.command_routes as _m_command_routes
import bridge.persona_sync as _m_persona_sync
import bridge.sillytavern_api as _m_sillytavern_api
import bridge.sync_core as _m_sync_core


class NativePersonaSyncTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_settings = self.app_settings_builder.native_persona_settings_file
        self.old_avatars = self.app_settings_builder.native_persona_avatar_dir
        self.old_backups = self.app_settings_builder.native_persona_backup_dir
        self.old_phase3 = _m_sillytavern_api.live_sync_api_configured
        self.app_settings_builder.native_persona_settings_file = root / "settings.json"
        self.app_settings_builder.native_persona_avatar_dir = root / "avatars"
        self.app_settings_builder.native_persona_backup_dir = root / "backups"
        self.app_settings_builder.native_persona_avatar_dir.mkdir()
        (self.app_settings_builder.native_persona_avatar_dir / "user-default.png").write_bytes(b"avatar")
        self.settings = {
            "power_user": {
                "personas": {"native.png": "Native"},
                "persona_descriptions": {"native.png": {"description": "Native desc", "title": "Keep"}},
            },
            "default_persona": "native.png",
            "unrelated": {"keep": True},
        }
        self.app_settings_builder.native_persona_settings_file.write_text(json.dumps(self.settings), encoding="utf-8")
        _m_sillytavern_api.live_sync_api_configured = lambda *, app_settings=None: False
        self.calls = []
        self.old_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: (
            self.calls.append((method, payload)) or {}
        )

    def tearDown(self):
        _m_cards.send_panel_request = self.old_request
        _m_sillytavern_api.live_sync_api_configured = self.old_phase3
        self.app_settings_builder.native_persona_settings_file = self.old_settings
        self.app_settings_builder.native_persona_avatar_dir = self.old_avatars
        self.app_settings_builder.native_persona_backup_dir = self.old_backups
        self.tmp.cleanup()

    def _read(self):
        return json.loads(self.app_settings_builder.native_persona_settings_file.read_text(encoding="utf-8"))

    def test_loader_reads_native_settings_without_bridge_json(self):
        personas = _m_persona_sync.load_personas(app_settings=self.app_settings_builder.build())
        self.assertEqual(personas["native.png"]["name"], "Native")
        self.assertEqual(personas["native.png"]["description"], "Native desc")
        self.assertEqual(personas["native.png"]["sillytavern_avatar"], "native.png")

    def test_loader_failure_returns_empty_and_logs_final_warning(self):
        with (
            patch.object(
                _m_persona_sync,
                "load_native_personas",
                side_effect=RuntimeError("boom"),
            ),
            patch.object(
                logging,
                "warning",
            ) as warning,
        ):
            result = _m_persona_sync.load_personas(app_settings=self.app_settings_builder.build())

        self.assertEqual(result, {})
        warning.assert_called_once_with(
            "Could not load native Persona metadata",
            exc_info=True,
        )

    def test_persona_read_helpers_use_current_loader_at_call_time(self):
        personas = {
            "patched.png": {
                "name": "Patched",
                "description": "Patched description",
                "sillytavern_avatar": "patched.png",
            }
        }

        with (
            patch.object(
                _m_persona_sync,
                "load_personas",
                return_value=personas,
            ),
            patch.object(
                _m_persona_sync,
                "_native_settings",
                return_value={
                    "power_user": {
                        "default_persona": "patched.png",
                    }
                },
            ),
        ):
            self.assertEqual(
                _m_sync_core.get_persona("patched.png", app_settings=self.app_settings_builder.build()),
                personas["patched.png"],
            )
            self.assertEqual(
                _m_persona_sync.default_persona_id(app_settings=self.app_settings_builder.build()),
                "patched.png",
            )
            self.assertEqual(
                _m_sync_core.persona_name("patched.png", app_settings=self.app_settings_builder.build()),
                "Patched",
            )

    def test_upsert_preserves_unrelated_native_settings_and_descriptor_fields(self):
        avatar = _m_persona_sync.upsert_native_persona(
            "native.png", "Updated", "Updated desc", app_settings=self.app_settings_builder.build()
        )
        data = self._read()
        self.assertEqual(avatar, "native.png")
        self.assertEqual(data["power_user"]["personas"][avatar], "Updated")
        self.assertEqual(data["power_user"]["persona_descriptions"][avatar]["description"], "Updated desc")
        self.assertEqual(data["power_user"]["persona_descriptions"][avatar]["title"], "Keep")
        self.assertEqual(data["default_persona"], "native.png")
        self.assertEqual(data["unrelated"], {"keep": True})

    def test_upsert_allocates_native_avatar_for_new_persona(self):
        avatar = _m_persona_sync.upsert_native_persona(
            "writer", "Writer", "Writer description", app_settings=self.app_settings_builder.build()
        )
        self.assertEqual(avatar, "bridge-writer.png")
        self.assertTrue((self.app_settings_builder.native_persona_avatar_dir / avatar).is_file())
        self.assertEqual(self._read()["power_user"]["personas"][avatar], "Writer")

    def test_persona_panel_has_no_bridge_import_export_actions(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute(
            "CREATE TABLE callback_tokens(token TEXT PRIMARY KEY, kind TEXT, value TEXT, chat_id TEXT, expires_at REAL)"
        )
        _m_command_routes.send_persona_menu(
            "token",
            "chat",
            "native.png",
            persona_service=make_native_test_persona_service(app_settings=self.app_settings_builder.build()),
            request_context=make_test_request_context(db, app_settings=self.app_settings_builder.build()),
        )
        callbacks = {
            button["callback_data"] for row in self.calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row
        }
        self.assertNotIn("persona:native_import", callbacks)
        self.assertNotIn("persona:native_export", callbacks)


if __name__ == "__main__":
    unittest.main()

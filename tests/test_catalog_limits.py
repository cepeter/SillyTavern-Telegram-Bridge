import application_test_setup as application_setup
from application_test_setup import (
    ensure_application_extensions,
    make_native_test_persona_service,
    make_test_request_context,
)
from settings_test_support import SettingsTestCase

import bridge.card_content as _owner_card_content
import bridge.persona_input as _owner_persona_input
import bridge.session_core as _owner_session_core

ensure_application_extensions()

import json
import tempfile
import time
import unittest
from pathlib import Path

import bridge.cards as _m_cards
import bridge.character_identity as _m_character_identity
import bridge.command_routes as _m_command_routes
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.native_imports as _m_telegram
import bridge.sillytavern_api as _m_sillytavern_api


class CatalogLimitTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_character = self.app_settings_builder.character_dir
        self.old_config_character = self.app_settings_builder.character_dir
        self.old_telegram_character = self.app_settings_builder.character_dir
        self.old_config_world = self.app_settings_builder.world_dir
        self.old_prompts = self.app_settings_builder.system_prompts_dir
        self.old_config_prompts = self.app_settings_builder.system_prompts_dir
        self.old_native_settings = self.app_settings_builder.native_persona_settings_file
        self.old_native_avatars = self.app_settings_builder.native_persona_avatar_dir
        self.old_phase3 = _m_sillytavern_api.live_sync_api_configured
        self.old_db = self.app_settings_builder.db_file
        self.app_settings_builder.character_dir = root / "characters"
        self.app_settings_builder.character_dir = self.app_settings_builder.character_dir
        self.app_settings_builder.character_dir = self.app_settings_builder.character_dir
        self.app_settings_builder.world_dir = root / "worlds"
        self.app_settings_builder.system_prompts_dir = root / "prompts"
        self.app_settings_builder.system_prompts_dir = self.app_settings_builder.system_prompts_dir
        self.app_settings_builder.native_persona_settings_file = root / "settings.json"
        self.app_settings_builder.native_persona_avatar_dir = root / "avatars"
        self.app_settings_builder.native_persona_avatar_dir.mkdir()
        (self.app_settings_builder.native_persona_avatar_dir / "user-default.png").write_bytes(b"avatar")
        self.app_settings_builder.native_persona_settings_file.write_text(
            json.dumps({"power_user": {"personas": {}, "persona_descriptions": {}}}), encoding="utf-8"
        )
        _m_sillytavern_api.live_sync_api_configured = lambda *, app_settings=None: False
        self.app_settings_builder.db_file = root / "bridge.sqlite3"
        for directory in (
            self.app_settings_builder.character_dir,
            self.app_settings_builder.world_dir,
            self.app_settings_builder.system_prompts_dir,
        ):
            directory.mkdir()
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )

    def tearDown(self):
        self.db.close()
        self.app_settings_builder.character_dir = self.old_character
        self.app_settings_builder.character_dir = self.old_config_character
        self.app_settings_builder.character_dir = self.old_telegram_character
        self.app_settings_builder.world_dir = self.old_config_world
        self.app_settings_builder.system_prompts_dir = self.old_prompts
        self.app_settings_builder.system_prompts_dir = self.old_config_prompts
        self.app_settings_builder.native_persona_settings_file = self.old_native_settings
        self.app_settings_builder.native_persona_avatar_dir = self.old_native_avatars
        _m_sillytavern_api.live_sync_api_configured = self.old_phase3
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def test_system_prompt_catalog_uses_directory_only(self):
        directory_prompt = self.app_settings_builder.system_prompts_dir / "Only.txt"
        directory_prompt.write_text("directory prompt", encoding="utf-8")

        prompts = _m_cards.load_system_prompts(app_settings=self.app_settings_builder.build())

        self.assertEqual(
            prompts,
            {
                "Only": {
                    "name": "Only",
                    "prompt": "directory prompt",
                }
            },
        )

    def test_file_catalogs_are_deterministically_capped_at_40(self):
        for index in range(41):
            (self.app_settings_builder.character_dir / f"{index:02}.png").write_bytes(b"x")
            (self.app_settings_builder.world_dir / f"{index:02}.json").write_text("{}", encoding="utf-8")
            (self.app_settings_builder.system_prompts_dir / f"{index:02}.txt").write_text(
                f"prompt {index}", encoding="utf-8"
            )
        self.assertEqual(
            len(_m_character_identity.character_card_paths(app_settings=self.app_settings_builder.build())), 40
        )
        self.assertEqual(len(_owner_card_content.world_file_paths(app_settings=self.app_settings_builder.build())), 40)
        self.assertEqual(len(_m_cards.load_system_prompts(app_settings=self.app_settings_builder.build())), 40)
        self.assertEqual(
            _m_character_identity.character_card_paths(app_settings=self.app_settings_builder.build())[-1].name,
            "39.png",
        )

    def test_native_system_prompt_json_uses_content_and_name(self):
        native = self.app_settings_builder.system_prompts_dir / "Native Prompt.json"
        native.write_text(
            json.dumps({"name": "Native Prompt", "content": "native content", "post_history": "ignored by bridge"}),
            encoding="utf-8",
        )
        prompts = _m_cards.load_system_prompts(app_settings=self.app_settings_builder.build())
        self.assertEqual(prompts["Native Prompt"]["name"], "Native Prompt")
        self.assertEqual(prompts["Native Prompt"]["prompt"], "native content")
        self.assertEqual(
            _owner_card_content.system_prompt_label("native content", app_settings=self.app_settings_builder.build()),
            "Native Prompt",
        )
        self.assertEqual(
            _owner_card_content.system_prompt_label("", app_settings=self.app_settings_builder.build()), "off"
        )
        self.assertEqual(
            _owner_card_content.system_prompt_label(
                "unrecognized prompt", app_settings=self.app_settings_builder.build()
            ),
            "custom",
        )
        self.assertNotIn("name", prompts)
        self.assertNotIn("content", prompts)
        self.assertNotIn("post_history", prompts)

    def test_persona_panel_shows_at_most_40(self):
        personas = {f"p{index:02}.png": f"Persona {index}" for index in range(41)}
        settings = {
            "power_user": {
                "personas": personas,
                "persona_descriptions": {key: {"description": "d"} for key in personas},
            }
        }
        self.app_settings_builder.native_persona_settings_file.write_text(json.dumps(settings), encoding="utf-8")
        calls = []
        original = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _m_command_routes.send_persona_menu(
                "token",
                "chat",
                "",
                persona_service=make_native_test_persona_service(app_settings=self.app_settings_builder.build()),
                request_context=make_test_request_context(self.db, app_settings=self.app_settings_builder.build()),
            )
        finally:
            _m_cards.send_panel_request = original
        callbacks = [
            button["callback_data"] for row in calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row
        ]
        self.assertEqual(sum(value.startswith("persona:t") for value in callbacks), 8)
        self.assertIn("page 1/5", calls[-1][1]["text"])

    def test_persona_create_rejects_item_41(self):
        personas = {f"p{index:02}.png": f"Persona {index}" for index in range(40)}
        self.app_settings_builder.native_persona_settings_file.write_text(
            json.dumps(
                {
                    "power_user": {
                        "personas": personas,
                        "persona_descriptions": {key: {"description": "d"} for key in personas},
                    }
                }
            ),
            encoding="utf-8",
        )
        sent = []
        original = _m_message_commands.send_text
        _m_message_commands.send_text = lambda _token, _chat, text: sent.append(text) or []
        try:
            state = {
                "session_id": self.session["session_id"],
                "mode": "create",
                "persona_id": "",
                "expires_at": time.time() + 60,
            }
            self.assertTrue(
                _owner_persona_input._handle_persona_input(
                    self.db,
                    "token",
                    "chat",
                    self.session,
                    "p40 | Persona 40 | description",
                    state,
                    None,
                    persona_service=make_native_test_persona_service(app_settings=self.app_settings_builder.build()),
                    request_context=make_test_request_context(
                        self.db, self.session["session_id"], app_settings=self.app_settings_builder.build()
                    ),
                )
            )
        finally:
            _m_message_commands.send_text = original
        self.assertTrue(any("40 maximum" in text for text in sent))

    def test_character_upload_rejects_item_41_without_deleting_existing(self):
        for index in range(40):
            (self.app_settings_builder.character_dir / f"{index:02}.png").write_bytes(b"existing")
        sent = []
        old_parse, old_fields, old_send = (
            _m_telegram.parse_png_chara_bytes,
            _m_telegram.card_fields,
            _m_telegram.send_text,
        )
        _m_telegram.parse_png_chara_bytes = lambda _raw: {"name": "Forty One"}
        _m_telegram.card_fields = lambda _card, *, app_settings=None: {"name": "Forty One"}
        _m_telegram.send_text = lambda _token, _chat, text: sent.append(text) or []
        try:
            _m_telegram.import_character_card(
                self.db,
                "token",
                "chat",
                "new.png",
                b"new",
                app_settings=self.app_settings_builder.build(),
                request_context=application_setup.make_test_request_context(
                    self.db, app_settings=self.app_settings_builder.build()
                ),
            )
        finally:
            _m_telegram.parse_png_chara_bytes, _m_telegram.card_fields, _m_telegram.send_text = (
                old_parse,
                old_fields,
                old_send,
            )
        self.assertEqual(len(list(self.app_settings_builder.character_dir.glob("*.png"))), 40)
        self.assertTrue(any("40 maximum" in text for text in sent))


if __name__ == "__main__":
    unittest.main()

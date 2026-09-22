from application_test_setup import ensure_application_extensions

ensure_application_extensions()

import json
from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import time
import bridge.callbacks as _m_callbacks
import bridge.cards as _m_cards
import bridge.catalog as _m_catalog
import bridge.character_identity as _m_character_identity
import bridge.command_routes as _m_command_routes
import bridge.common as _m_common
import bridge.input_flows as _m_input_flows
import bridge.main as _m_main
import bridge.memory_curator as _m_memory_curator
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.persona_sync as _m_persona_sync
import bridge.status_panels as _m_status_panels
import bridge.telegram as _m_telegram
class CatalogLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_character = _m_main.CHARACTER_DIR
        self.old_config_character = config.CHARACTER_DIR
        self.old_world = _m_catalog.WORLD_DIR
        self.old_config_world = config.WORLD_DIR
        self.old_prompts = _m_common.SYSTEM_PROMPTS_DIR
        self.old_config_prompts = config.SYSTEM_PROMPTS_DIR
        self.old_prompt_file = _m_common.SYSTEM_PROMPTS_FILE
        self.old_config_prompt_file = config.SYSTEM_PROMPTS_FILE
        self.old_native_settings = _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE
        self.old_native_avatars = _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR
        self.old_native_cache = _m_persona_sync._NATIVE_PERSONA_CACHE
        self.old_native_cache_time = _m_persona_sync._NATIVE_PERSONA_CACHE_LAST_REFRESH
        self.old_phase3 = _m_persona_sync.phase3_api_configured
        self.old_db = config.DB_FILE
        _m_main.CHARACTER_DIR = root / "characters"
        config.CHARACTER_DIR = _m_main.CHARACTER_DIR
        _m_catalog.WORLD_DIR = root / "worlds"
        config.WORLD_DIR = _m_catalog.WORLD_DIR
        _m_common.SYSTEM_PROMPTS_DIR = root / "prompts"
        config.SYSTEM_PROMPTS_DIR = _m_common.SYSTEM_PROMPTS_DIR
        _m_common.SYSTEM_PROMPTS_FILE = ""
        config.SYSTEM_PROMPTS_FILE = ""
        _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE = root / "settings.json"
        _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR = root / "avatars"
        _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR.mkdir()
        (_m_persona_sync.NATIVE_PERSONA_AVATAR_DIR / "user-default.png").write_bytes(b"avatar")
        _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE.write_text(json.dumps({"power_user": {"personas": {}, "persona_descriptions": {}}}), encoding="utf-8")
        _m_persona_sync._NATIVE_PERSONA_CACHE = {}
        _m_persona_sync._NATIVE_PERSONA_CACHE_LAST_REFRESH = 0
        _m_persona_sync.phase3_api_configured = lambda: False
        config.DB_FILE = root / "bridge.sqlite3"
        for directory in (_m_main.CHARACTER_DIR, _m_catalog.WORLD_DIR, _m_common.SYSTEM_PROMPTS_DIR):
            directory.mkdir()
        self.db = _m_memory_curator.db_connect()
        self.session = _m_callbacks.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)

    def tearDown(self):
        self.db.close()
        _m_main.CHARACTER_DIR = self.old_character
        config.CHARACTER_DIR = self.old_config_character
        _m_catalog.WORLD_DIR = self.old_world
        config.WORLD_DIR = self.old_config_world
        _m_common.SYSTEM_PROMPTS_DIR = self.old_prompts
        config.SYSTEM_PROMPTS_DIR = self.old_config_prompts
        _m_common.SYSTEM_PROMPTS_FILE = self.old_prompt_file
        config.SYSTEM_PROMPTS_FILE = self.old_config_prompt_file
        _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE = self.old_native_settings
        _m_persona_sync.NATIVE_PERSONA_AVATAR_DIR = self.old_native_avatars
        _m_persona_sync._NATIVE_PERSONA_CACHE = self.old_native_cache
        _m_persona_sync._NATIVE_PERSONA_CACHE_LAST_REFRESH = self.old_native_cache_time
        _m_persona_sync.phase3_api_configured = self.old_phase3
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def test_file_catalogs_are_deterministically_capped_at_40(self):
        for index in range(41):
            (_m_main.CHARACTER_DIR / f"{index:02}.png").write_bytes(b"x")
            (_m_catalog.WORLD_DIR / f"{index:02}.json").write_text("{}", encoding="utf-8")
            (_m_common.SYSTEM_PROMPTS_DIR / f"{index:02}.txt").write_text(f"prompt {index}", encoding="utf-8")
        self.assertEqual(len(_m_character_identity.character_card_paths()), 40)
        self.assertEqual(len(_m_catalog.world_file_paths()), 40)
        self.assertEqual(len(_m_cards.load_system_prompts()), 40)
        self.assertEqual(_m_character_identity.character_card_paths()[-1].name, "39.png")

    def test_native_system_prompt_json_uses_content_and_name(self):
        native = _m_common.SYSTEM_PROMPTS_DIR / "Native Prompt.json"
        native.write_text(json.dumps({"name": "Native Prompt", "content": "native content", "post_history": "ignored by bridge"}), encoding="utf-8")
        prompts = _m_cards.load_system_prompts()
        self.assertEqual(prompts["Native Prompt"]["name"], "Native Prompt")
        self.assertEqual(prompts["Native Prompt"]["prompt"], "native content")
        self.assertEqual(_m_status_panels.system_prompt_label("native content"), "Native Prompt")
        self.assertEqual(_m_status_panels.system_prompt_label(""), "off")
        self.assertEqual(_m_status_panels.system_prompt_label("unrecognized prompt"), "custom")
        self.assertNotIn("name", prompts)
        self.assertNotIn("content", prompts)
        self.assertNotIn("post_history", prompts)

    def test_persona_panel_shows_at_most_40(self):
        personas = {f"p{index:02}.png": f"Persona {index}" for index in range(41)}
        settings = {"power_user": {"personas": personas, "persona_descriptions": {key: {"description": "d"} for key in personas}}}
        _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE.write_text(json.dumps(settings), encoding="utf-8")
        calls = []
        original = _m_panel_callback_routes.telegram_request
        _m_panel_callback_routes.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            _m_command_routes.send_persona_menu("token", "chat", "")
        finally:
            _m_panel_callback_routes.telegram_request = original
        callbacks = [button["callback_data"] for row in calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row]
        self.assertEqual(sum(value.startswith("persona:t") for value in callbacks), 8)
        self.assertIn("page 1/5", calls[-1][1]["text"])

    def test_persona_create_rejects_item_41(self):
        personas = {f"p{index:02}.png": f"Persona {index}" for index in range(40)}
        _m_persona_sync.NATIVE_PERSONA_SETTINGS_FILE.write_text(json.dumps({"power_user": {"personas": personas, "persona_descriptions": {key: {"description": "d"} for key in personas}}}), encoding="utf-8")
        sent = []
        original = _m_memory_curator.send_text
        _m_memory_curator.send_text = lambda _token, _chat, text: sent.append(text) or []
        try:
            state = {"session_id": self.session["session_id"], "mode": "create", "persona_id": "", "expires_at": time.time() + 60}
            self.assertTrue(_m_input_flows._handle_persona_input(self.db, "token", "chat", self.session, "p40 | Persona 40 | description", state, None))
        finally:
            _m_memory_curator.send_text = original
        self.assertTrue(any("40 maximum" in text for text in sent))

    def test_character_upload_rejects_item_41_without_deleting_existing(self):
        for index in range(40):
            (_m_main.CHARACTER_DIR / f"{index:02}.png").write_bytes(b"existing")
        sent = []
        old_parse, old_fields, old_send = _m_telegram.parse_png_chara_bytes, _m_main.card_fields, _m_memory_curator.send_text
        _m_telegram.parse_png_chara_bytes = lambda _raw: {"name": "Forty One"}
        _m_main.card_fields = lambda _card: {"name": "Forty One"}
        _m_memory_curator.send_text = lambda _token, _chat, text: sent.append(text) or []
        try:
            _m_telegram.import_character_card(self.db, "token", "chat", "new.png", b"new")
        finally:
            _m_telegram.parse_png_chara_bytes, _m_main.card_fields, _m_memory_curator.send_text = old_parse, old_fields, old_send
        self.assertEqual(len(list(_m_main.CHARACTER_DIR.glob("*.png"))), 40)
        self.assertTrue(any("40 maximum" in text for text in sent))


if __name__ == "__main__":
    unittest.main()

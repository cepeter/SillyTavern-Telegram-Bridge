import json
from pathlib import Path
import tempfile
import unittest

import bridge.config as config
from runtime_test_facade import runtime as rt


class CatalogLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_character = rt.CHARACTER_DIR
        self.old_config_character = config.CHARACTER_DIR
        self.old_world = rt.WORLD_DIR
        self.old_config_world = config.WORLD_DIR
        self.old_prompts = rt.SYSTEM_PROMPTS_DIR
        self.old_config_prompts = config.SYSTEM_PROMPTS_DIR
        self.old_prompt_file = rt.SYSTEM_PROMPTS_FILE
        self.old_config_prompt_file = config.SYSTEM_PROMPTS_FILE
        self.old_native_settings = rt.NATIVE_PERSONA_SETTINGS_FILE
        self.old_native_avatars = rt.NATIVE_PERSONA_AVATAR_DIR
        self.old_native_cache = rt._NATIVE_PERSONA_CACHE
        self.old_native_cache_time = rt._NATIVE_PERSONA_CACHE_LAST_REFRESH
        self.old_phase3 = rt.phase3_api_configured
        self.old_db = config.DB_FILE
        rt.CHARACTER_DIR = root / "characters"
        config.CHARACTER_DIR = rt.CHARACTER_DIR
        rt.WORLD_DIR = root / "worlds"
        config.WORLD_DIR = rt.WORLD_DIR
        rt.SYSTEM_PROMPTS_DIR = root / "prompts"
        config.SYSTEM_PROMPTS_DIR = rt.SYSTEM_PROMPTS_DIR
        rt.SYSTEM_PROMPTS_FILE = ""
        config.SYSTEM_PROMPTS_FILE = ""
        rt.NATIVE_PERSONA_SETTINGS_FILE = root / "settings.json"
        rt.NATIVE_PERSONA_AVATAR_DIR = root / "avatars"
        rt.NATIVE_PERSONA_AVATAR_DIR.mkdir()
        (rt.NATIVE_PERSONA_AVATAR_DIR / "user-default.png").write_bytes(b"avatar")
        rt.NATIVE_PERSONA_SETTINGS_FILE.write_text(json.dumps({"power_user": {"personas": {}, "persona_descriptions": {}}}), encoding="utf-8")
        rt._NATIVE_PERSONA_CACHE = {}
        rt._NATIVE_PERSONA_CACHE_LAST_REFRESH = 0
        rt.phase3_api_configured = lambda: False
        config.DB_FILE = root / "bridge.sqlite3"
        for directory in (rt.CHARACTER_DIR, rt.WORLD_DIR, rt.SYSTEM_PROMPTS_DIR):
            directory.mkdir()
        self.db = rt.db_connect()
        self.session = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)

    def tearDown(self):
        self.db.close()
        rt.CHARACTER_DIR = self.old_character
        config.CHARACTER_DIR = self.old_config_character
        rt.WORLD_DIR = self.old_world
        config.WORLD_DIR = self.old_config_world
        rt.SYSTEM_PROMPTS_DIR = self.old_prompts
        config.SYSTEM_PROMPTS_DIR = self.old_config_prompts
        rt.SYSTEM_PROMPTS_FILE = self.old_prompt_file
        config.SYSTEM_PROMPTS_FILE = self.old_config_prompt_file
        rt.NATIVE_PERSONA_SETTINGS_FILE = self.old_native_settings
        rt.NATIVE_PERSONA_AVATAR_DIR = self.old_native_avatars
        rt._NATIVE_PERSONA_CACHE = self.old_native_cache
        rt._NATIVE_PERSONA_CACHE_LAST_REFRESH = self.old_native_cache_time
        rt.phase3_api_configured = self.old_phase3
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def test_file_catalogs_are_deterministically_capped_at_40(self):
        for index in range(41):
            (rt.CHARACTER_DIR / f"{index:02}.png").write_bytes(b"x")
            (rt.WORLD_DIR / f"{index:02}.json").write_text("{}", encoding="utf-8")
            (rt.SYSTEM_PROMPTS_DIR / f"{index:02}.txt").write_text(f"prompt {index}", encoding="utf-8")
        self.assertEqual(len(rt.character_card_paths()), 40)
        self.assertEqual(len(rt.world_file_paths()), 40)
        self.assertEqual(len(rt.load_system_prompts()), 40)
        self.assertEqual(rt.character_card_paths()[-1].name, "39.png")

    def test_native_system_prompt_json_uses_content_and_name(self):
        native = rt.SYSTEM_PROMPTS_DIR / "Native Prompt.json"
        native.write_text(json.dumps({"name": "Native Prompt", "content": "native content", "post_history": "ignored by bridge"}), encoding="utf-8")
        prompts = rt.load_system_prompts()
        self.assertEqual(prompts["Native Prompt"]["name"], "Native Prompt")
        self.assertEqual(prompts["Native Prompt"]["prompt"], "native content")
        self.assertEqual(rt.system_prompt_label("native content"), "Native Prompt")
        self.assertEqual(rt.system_prompt_label(""), "off")
        self.assertEqual(rt.system_prompt_label("unrecognized prompt"), "custom")
        self.assertNotIn("name", prompts)
        self.assertNotIn("content", prompts)
        self.assertNotIn("post_history", prompts)

    def test_persona_panel_shows_at_most_40(self):
        personas = {f"p{index:02}.png": f"Persona {index}" for index in range(41)}
        settings = {"power_user": {"personas": personas, "persona_descriptions": {key: {"description": "d"} for key in personas}}}
        rt.NATIVE_PERSONA_SETTINGS_FILE.write_text(json.dumps(settings), encoding="utf-8")
        calls = []
        original = rt.telegram_request
        rt.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {}
        try:
            rt.send_persona_menu("token", "chat", "")
        finally:
            rt.telegram_request = original
        callbacks = [button["callback_data"] for row in calls[-1][1]["reply_markup"]["inline_keyboard"] for button in row]
        self.assertEqual(sum(value.startswith("persona:t") for value in callbacks), 8)
        self.assertIn("page 1/5", calls[-1][1]["text"])

    def test_persona_create_rejects_item_41(self):
        personas = {f"p{index:02}.png": f"Persona {index}" for index in range(40)}
        rt.NATIVE_PERSONA_SETTINGS_FILE.write_text(json.dumps({"power_user": {"personas": personas, "persona_descriptions": {key: {"description": "d"} for key in personas}}}), encoding="utf-8")
        sent = []
        original = rt.send_text
        rt.send_text = lambda _token, _chat, text: sent.append(text) or []
        try:
            state = {"session_id": self.session["session_id"], "mode": "create", "persona_id": "", "expires_at": rt.time.time() + 60}
            self.assertTrue(rt._handle_persona_input(self.db, "token", "chat", self.session, "p40 | Persona 40 | description", state, None))
        finally:
            rt.send_text = original
        self.assertTrue(any("40 maximum" in text for text in sent))

    def test_character_upload_rejects_item_41_without_deleting_existing(self):
        for index in range(40):
            (rt.CHARACTER_DIR / f"{index:02}.png").write_bytes(b"existing")
        sent = []
        old_parse, old_fields, old_send = rt.parse_png_chara_bytes, rt.card_fields, rt.send_text
        rt.parse_png_chara_bytes = lambda _raw: {"name": "Forty One"}
        rt.card_fields = lambda _card: {"name": "Forty One"}
        rt.send_text = lambda _token, _chat, text: sent.append(text) or []
        try:
            rt.import_character_card(self.db, "token", "chat", "new.png", b"new")
        finally:
            rt.parse_png_chara_bytes, rt.card_fields, rt.send_text = old_parse, old_fields, old_send
        self.assertEqual(len(list(rt.CHARACTER_DIR.glob("*.png"))), 40)
        self.assertTrue(any("40 maximum" in text for text in sent))


if __name__ == "__main__":
    unittest.main()

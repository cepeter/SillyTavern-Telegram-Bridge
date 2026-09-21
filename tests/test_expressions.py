import sqlite3
import tempfile
import unittest
from pathlib import Path

from dependency_patch import dependency_module

_m_expressions = dependency_module("bridge.expressions")
_m_main = dependency_module("bridge.main")
_m_media = dependency_module("bridge.media")
_m_panel_callback_routes = dependency_module("bridge.panel_callback_routes")
_m_persona_sync = dependency_module("bridge.persona_sync")
_m_session_naming = dependency_module("bridge.session_naming")


class ExpressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_character_dir = _m_main.CHARACTER_DIR
        self.old_card_file = _m_panel_callback_routes.CARD_FILE
        self.old_st_dir = _m_persona_sync.SILLYTAVERN_DIR
        _m_main.CHARACTER_DIR = self.root / "characters"
        _m_main.CHARACTER_DIR.mkdir()
        _m_panel_callback_routes.CARD_FILE = _m_main.CHARACTER_DIR / "Alisha.png"
        _m_panel_callback_routes.CARD_FILE.write_bytes(b"fixed")
        _m_persona_sync.SILLYTAVERN_DIR = self.root / "st"
        (_m_persona_sync.SILLYTAVERN_DIR / "public/img/default-expressions").mkdir(parents=True)

    def tearDown(self):
        _m_main.CHARACTER_DIR = self.old_character_dir
        _m_panel_callback_routes.CARD_FILE = self.old_card_file
        _m_persona_sync.SILLYTAVERN_DIR = self.old_st_dir
        self.temp.cleanup()

    def test_native_discovery_strips_sillytavern_suffixes(self):
        sprite_dir = _m_main.CHARACTER_DIR / "Alisha"
        sprite_dir.mkdir()
        for name in ("joy.png", "joy-1.png", "sadness.expressive.webp"):
            (sprite_dir / name).write_bytes(b"image")
        assets = _m_panel_callback_routes.discover_expression_assets("Alisha.png")
        self.assertEqual(set(assets), {"joy", "sadness"})
        self.assertEqual(assets["joy"].name, "joy.png")

    def test_classifier_uses_available_assets_and_neutral_fallback(self):
        assets = {"joy": Path("joy.png"), "neutral": Path("neutral.png")}
        self.assertEqual(_m_expressions.classify_expression("I am so happy!", assets), "joy")
        self.assertEqual(_m_expressions.classify_expression("an unrelated sentence", assets), "neutral")

    def test_menu_exposes_auto_off_and_native_labels(self):
        sprite_dir = _m_main.CHARACTER_DIR / "Alisha"
        sprite_dir.mkdir()
        (sprite_dir / "joy.png").write_bytes(b"image")
        for index in range(13):
            (sprite_dir / f"asset{index}.png").write_bytes(b"image")
        markup = _m_expressions.expression_menu_markup("Alisha.png", "auto")
        callbacks = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
        self.assertIn("expression:auto", callbacks)
        self.assertIn("expression:off", callbacks)
        self.assertIn("expression:page:1", callbacks)
        page_two = _m_expressions.expression_menu_markup("Alisha.png", "auto", 1)
        page_callbacks = [button["callback_data"] for row in page_two["inline_keyboard"] for button in row]
        self.assertIn("expression:joy", page_callbacks)
        self.assertIn("expression:asset9", page_callbacks)

    def test_delivery_deduplicates_same_effective_asset(self):
        sprite_dir = _m_main.CHARACTER_DIR / "Alisha"
        sprite_dir.mkdir()
        (sprite_dir / "joy.png").write_bytes(b"image")
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE sessions(chat_id TEXT, session_id TEXT, character_file TEXT)")
        db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO sessions VALUES('1', 's1', 'Alisha.png')")
        sent = []
        old_upload = _m_expressions._send_expression_photo
        _m_expressions._send_expression_photo = lambda token, chat, path: sent.append(path.name) or True
        try:
            _m_session_naming.set_meta(db, _m_panel_callback_routes.expression_mode_key("1", "s1"), "auto")
            _m_media.deliver_expression("token", "1", "I am happy", db, "s1")
            _m_media.deliver_expression("token", "1", "I am happy", db, "s1")
        finally:
            _m_expressions._send_expression_photo = old_upload
        self.assertEqual(sent, ["joy.png"])


if __name__ == "__main__":
    unittest.main()

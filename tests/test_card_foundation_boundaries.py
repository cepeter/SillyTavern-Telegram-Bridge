"""Card-foundation import-boundary regression tests."""

import ast
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from settings_test_support import SettingsTestCase

REPO_ROOT = Path(__file__).parents[1]


PANEL_UTIL_EXPORTS = (
    "panel_label",
    "panel_page",
    "panel_navigation",
)

CARD_CONTENT_EXPORTS = (
    "read_png_chara",
    "parse_png_chara_bytes",
    "card_fields",
    "character_card_paths",
    "safe_character_path",
    "card_fields_from_file",
    "world_file_paths",
    "safe_world_path",
    "active_world_files",
    "encode_world_files",
    "build_world_info",
    "load_system_prompts",
    "get_system_prompt_choice",
    "system_prompt_label",
    "system_prompt_callback_token",
    "system_prompt_choices",
    "replace_macros",
    "build_system_prompt",
    "character_display_name",
)

CALLBACK_TOKEN_EXPORTS = (
    "dynamic_callback_token",
    "resolve_dynamic_callback_token",
)


class CardFoundationBoundaryTests(SettingsTestCase):
    def _run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_config_exposes_card_foundation_defaults(self):
        import bridge.config as config

        self.assertIsInstance(self.app_settings_builder.sillytavern_dir, Path)
        self.assertIsInstance(self.app_settings_builder.character_dir, Path)
        self.assertIsInstance(self.app_settings_builder.card_file, Path)
        self.assertIsInstance(self.app_settings_builder.world_dir, Path)
        self.assertIsInstance(self.app_settings_builder.system_prompts_dir, Path)
        self.assertIsInstance(self.app_settings_builder.default_character_file, str)
        self.assertFalse(hasattr(config, "SYSTEM_PROMPTS_FILE"))
        self.assertIsInstance(self.app_settings_builder.default_user_name, str)
        self.assertEqual(config.CATALOG_MAX_ITEMS, 40)
        self.assertEqual(config.CARD_FIELD_MAX_CHARS, 20000)
        self.assertEqual(config.CARD_TOTAL_MAX_CHARS, 60000)

    def test_card_file_is_fixed_in_each_explicit_settings_snapshot(self):
        first = self.app_settings_builder.build()
        original_card = first.card_file
        self.app_settings_builder.default_character_file = "temporary.png"
        second = self.app_settings_builder.build()
        self.assertEqual(first.card_file, original_card)
        self.assertEqual(second.card_file, second.character_dir / "temporary.png")
        self.assertIsNot(first, second)

    def test_common_no_longer_owns_extracted_context_state(self):
        source = (REPO_ROOT / "bridge" / "common.py").read_text(encoding="utf-8")

        self.assertNotIn(
            "_PANEL_SESSION_CONTEXT = threading.local()",
            source,
        )
        self.assertNotIn(
            "_DB_CONNECTION_CONTEXT = threading.local()",
            source,
        )
        for name in (
            "set_panel_session_context",
            "panel_session_context",
            "set_panel_actor_context",
            "panel_actor_context",
            "set_db_connection_context",
            "db_connection_context",
        ):
            self.assertNotIn(f"def {name}(", source)

    def test_common_no_longer_defines_extracted_card_config(self):
        source = (REPO_ROOT / "bridge" / "common.py").read_text(encoding="utf-8")

        for prefix in (
            "SILLYTAVERN_DIR = ",
            "CHARACTER_DIR = ",
            "DEFAULT_CHARACTER_FILE = ",
            "CARD_FILE = ",
            "WORLD_DIR = ",
            "SYSTEM_PROMPTS_DIR = ",
            "SYSTEM_PROMPTS_FILE = ",
            "DEFAULT_USER_NAME = ",
            "CATALOG_MAX_ITEMS = ",
            "CARD_FIELD_MAX_CHARS = ",
            "CARD_TOTAL_MAX_CHARS = ",
        ):
            with self.subTest(prefix=prefix):
                self.assertNotIn("\n" + prefix, source)

    def test_panel_utils_imports_without_runtime_or_common(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.panel_utils as panel_utils\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
            "assert panel_utils.panel_label('abcdef', 4) == 'abc…'\n"
            "assert panel_utils.panel_page(list(range(9)), 0)[2] == 2\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_card_content_imports_without_legacy_or_persistence_modules(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.card_content\n"
            "for name in ("
            "'bridge.runtime', 'bridge.common', 'bridge.database', "
            "'bridge.telegram', 'bridge.persona_sync'"
            "):\n"
            "    assert name not in sys.modules, name\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_cards_orchestration_does_not_redefine_content_or_panel_functions(self):
        source = (REPO_ROOT / "bridge" / "cards.py").read_text(encoding="utf-8")

        for name in (*PANEL_UTIL_EXPORTS, *CARD_CONTENT_EXPORTS):
            with self.subTest(name=name):
                self.assertNotIn(f"def {name}(", source)

    def test_character_identity_imports_character_display_name(self):
        source = (REPO_ROOT / "bridge" / "character_identity.py").read_text(encoding="utf-8")

        imports = {
            (node.module, alias.name)
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        self.assertIn(("bridge.card_content", "character_display_name"), imports)
        self.assertNotIn(
            "def character_display_name(",
            source,
        )

    def test_card_fields_default_name_follows_canonical_config(self):
        import bridge.card_content as card_content

        with patch.object(
            self.app_settings_builder,
            "default_character_file",
            "Seraphina.png",
        ):
            fields = card_content.card_fields({"data": {"name": ""}}, app_settings=self.app_settings_builder.build())
        self.assertEqual(fields["name"], "Seraphina")

    def test_character_and_world_paths_follow_canonical_config(self):
        import bridge.card_content as card_content

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            characters = root / "characters"
            worlds = root / "worlds"
            characters.mkdir()
            worlds.mkdir()
            (characters / "one.png").write_bytes(b"not parsed here")
            (worlds / "lore.json").write_text(
                "{}",
                encoding="utf-8",
            )

            with (
                patch.object(self.app_settings_builder, "character_dir", characters),
                patch.object(self.app_settings_builder, "world_dir", worlds),
            ):
                self.assertEqual(
                    [
                        path.name
                        for path in card_content.character_card_paths(app_settings=self.app_settings_builder.build())
                    ],
                    ["one.png"],
                )
                self.assertEqual(
                    [
                        path.name
                        for path in card_content.world_file_paths(app_settings=self.app_settings_builder.build())
                    ],
                    ["lore.json"],
                )
                self.assertEqual(
                    card_content.safe_character_path("one.png", app_settings=self.app_settings_builder.build()),
                    characters / "one.png",
                )
                self.assertEqual(
                    card_content.safe_world_path("lore.json", app_settings=self.app_settings_builder.build()),
                    worlds / "lore.json",
                )

    def test_deterministic_system_prompt_uses_canonical_text_cache(self):
        import bridge.card_content as card_content

        fields = {
            "system_prompt": "Hello {{char}}",
            "description": "",
            "personality": "",
            "scenario": "",
            "mes_example": "",
            "name": "Mira",
        }
        calls = []

        def fake_cached_text(key, builder):
            calls.append(key)
            return builder()

        with patch.object(
            card_content,
            "cached_text",
            side_effect=fake_cached_text,
        ):
            result = card_content.build_system_prompt(fields, "User", app_settings=self.app_settings_builder.build())

        self.assertEqual(result, "Hello Mira")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("system-prompt:"))

    def test_dynamic_system_prompt_macros_bypass_text_cache(self):
        import bridge.card_content as card_content

        fields = {
            "system_prompt": "{{time}}",
            "description": "",
            "personality": "",
            "scenario": "",
            "mes_example": "",
            "name": "Mira",
        }
        with patch.object(
            card_content,
            "cached_text",
            side_effect=AssertionError("dynamic prompt used cache"),
        ):
            result = card_content.build_system_prompt(fields, "User", app_settings=self.app_settings_builder.build())

        self.assertRegex(result, r"^\d{2}:\d{2}$")

    def test_callback_tokens_imports_without_runtime_or_common(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.callback_tokens as callback_tokens\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
            "assert callback_tokens._CALLBACK_TOKEN_TTL_SECONDS == 900\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_callback_token_opaque_format_fits_telegram(self):
        import bridge.callback_tokens as callback_tokens

        db = sqlite3.connect(":memory:")
        try:
            db.execute(
                (
                    "CREATE TABLE callback_tokens(token TEXT PRIMARY KEY, kind TEXT, value "
                    "TEXT, chat_id TEXT, expires_at REAL)"
                )
            )
            first = callback_tokens.dynamic_callback_token("character", "mira.png", "chat", db=db)
            second = callback_tokens.dynamic_callback_token("character", "mira.png", "chat", db=db)
            self.assertRegex(first, r"^t[A-Za-z0-9_-]{22}$")
            self.assertNotEqual(first, second)
            self.assertLessEqual(len("characterdeleteconfirm:" + first), 64)
        finally:
            db.close()

    def test_callback_token_scope_mismatch_preserves_valid_token(self):
        import bridge.callback_tokens as callback_tokens

        db = sqlite3.connect(":memory:")
        try:
            db.execute(
                (
                    "CREATE TABLE callback_tokens(token TEXT PRIMARY KEY, kind TEXT, value "
                    "TEXT, chat_id TEXT, expires_at REAL)"
                )
            )
            token = callback_tokens.dynamic_callback_token("world", "lore.json", "chat-a", db=db)
            self.assertIsNone(callback_tokens.resolve_dynamic_callback_token(token, "world", "chat-b", db=db))
            self.assertIsNotNone(db.execute("SELECT 1 FROM callback_tokens WHERE token=?", (token,)).fetchone())
            self.assertEqual(
                callback_tokens.resolve_dynamic_callback_token(token, "world", "chat-a", db=db), "lore.json"
            )
        finally:
            db.close()

    def test_callback_token_survives_database_reopen(self):
        import bridge.callback_tokens as callback_tokens

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.sqlite3"
            db = sqlite3.connect(path)
            db.execute(
                (
                    "CREATE TABLE callback_tokens(token TEXT PRIMARY KEY, kind TEXT, value "
                    "TEXT, chat_id TEXT, expires_at REAL)"
                )
            )
            token = callback_tokens.dynamic_callback_token("persona", "p1", "chat", db=db)
            db.close()
            reopened = sqlite3.connect(path)
            try:
                self.assertEqual(
                    callback_tokens.resolve_dynamic_callback_token(token, "persona", "chat", db=reopened), "p1"
                )
            finally:
                reopened.close()

    def test_callback_token_expiry_does_not_mutate_database(self):
        import bridge.callback_tokens as callback_tokens

        db = sqlite3.connect(":memory:")
        try:
            db.execute(
                (
                    "CREATE TABLE callback_tokens(token TEXT PRIMARY KEY, kind TEXT, value "
                    "TEXT, chat_id TEXT, expires_at REAL)"
                )
            )
            db.execute("INSERT INTO callback_tokens VALUES ('expired','world','lore.json','chat',0.0)")
            db.commit()
            statements = []
            db.set_trace_callback(statements.append)
            self.assertIsNone(callback_tokens.resolve_dynamic_callback_token("expired", "world", "chat", db=db))
            self.assertTrue(all(statement.lstrip().upper().startswith("SELECT ") for statement in statements))
            self.assertIsNotNone(db.execute("SELECT 1 FROM callback_tokens WHERE token='expired'").fetchone())
        finally:
            db.close()

    def test_cards_shell_does_not_define_callback_token_state_or_functions(self):
        source = (REPO_ROOT / "bridge" / "cards.py").read_text(encoding="utf-8")

        self.assertNotIn("_CALLBACK_TOKEN_VALUES", source)
        self.assertNotIn("_CALLBACK_TOKEN_TTL_SECONDS", source)
        self.assertNotIn("def dynamic_callback_token(", source)
        self.assertNotIn(
            "def resolve_dynamic_callback_token(",
            source,
        )

    def test_callback_tokens_have_no_process_local_cache(self):
        import bridge.callback_tokens as callback_tokens

        self.assertFalse(hasattr(callback_tokens, "_CALLBACK_TOKEN_VALUES"))
        self.assertEqual(callback_tokens.dynamic_callback_token.__module__, "bridge.callback_tokens")
        self.assertEqual(callback_tokens.resolve_dynamic_callback_token.__module__, "bridge.callback_tokens")

    def test_card_foundation_modules_do_not_import_runtime_or_common(self):
        for filename in (
            "panel_utils.py",
            "card_content.py",
            "callback_tokens.py",
        ):
            with self.subTest(filename=filename):
                source = (REPO_ROOT / "bridge" / filename).read_text(encoding="utf-8")
                self.assertNotIn("import bridge.runtime", source)
                self.assertNotIn(
                    "from bridge.runtime import",
                    source,
                )
                self.assertNotIn("import bridge.common", source)
                self.assertNotIn(
                    "from bridge.common import",
                    source,
                )

    def test_card_content_has_no_database_telegram_or_persona_dependency(self):
        source = (REPO_ROOT / "bridge" / "card_content.py").read_text(encoding="utf-8")

        for forbidden in (
            "bridge.database",
            "bridge.telegram",
            "bridge.persona_sync",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_cards_shell_declares_telegram_and_persona_collaborators_explicitly(self):
        source = (REPO_ROOT / "bridge" / "cards.py").read_text(encoding="utf-8")

        self.assertIn("from bridge.telegram import send_panel_request", source)
        self.assertIn("from bridge.persona_service import PersonaService", source)
        self.assertIn("panel_message_request", source)
        self.assertNotIn("from bridge.persona_sync import", source)
        self.assertNotIn("load_personas", source)
        self.assertNotIn("_native_settings", source)

        self.assertNotIn("ordinary_dependencies", source)
        self.assertNotIn("_bind_module_dependencies", source)

    def test_card_foundation_exports_are_canonical_without_facade(self):
        import bridge.callback_tokens as callback_tokens
        import bridge.card_content as card_content
        import bridge.cards as cards
        import bridge.media as media
        import bridge.panel_callback_routes as panel_callback_routes
        import bridge.panel_utils as panel_utils
        import bridge.session_naming as session_naming
        import bridge.telegram as telegram

        for name in PANEL_UTIL_EXPORTS:
            with self.subTest(group="panel_utils", name=name):
                self.assertTrue(hasattr(panel_utils, name))
        for name in CARD_CONTENT_EXPORTS:
            with self.subTest(group="card_content", name=name):
                self.assertTrue(hasattr(card_content, name))
        for name in CALLBACK_TOKEN_EXPORTS:
            with self.subTest(group="callback_tokens", name=name):
                self.assertTrue(hasattr(callback_tokens, name))

        self.assertFalse(hasattr(media, "set_db_connection_context"))
        self.assertFalse(hasattr(session_naming, "set_panel_session_context"))
        self.assertFalse(hasattr(telegram, "panel_session_context"))
        self.assertFalse(hasattr(telegram, "panel_actor_context"))
        self.assertFalse(hasattr(telegram, "db_connection_context"))
        self.assertIs(
            panel_callback_routes.dynamic_callback_token,
            callback_tokens.dynamic_callback_token,
        )
        self.assertTrue(callable(cards.send_character_menu))
        self.assertTrue(callable(cards.send_session_menu))


if __name__ == "__main__":
    unittest.main()

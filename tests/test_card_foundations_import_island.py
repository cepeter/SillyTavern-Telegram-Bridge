"""Phase 7B2 card-foundation ordinary-import boundary tests."""

from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).parents[1]


RUNTIME_CONTEXT_EXPORTS = (
    "set_panel_session_context",
    "panel_session_context",
    "set_panel_actor_context",
    "panel_actor_context",
    "set_db_connection_context",
    "db_connection_context",
)

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



class CardFoundationsImportIslandTests(unittest.TestCase):
    def _run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_runtime_context_imports_without_runtime_or_common(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.runtime_context as context\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
            "assert context.panel_session_context() == ''\n"
            "assert context.panel_actor_context() == ''\n"
            "assert context.db_connection_context() is None\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_config_exposes_card_foundation_defaults(self):
        import bridge.config as config

        self.assertIsInstance(config.SILLYTAVERN_DIR, Path)
        self.assertIsInstance(config.CHARACTER_DIR, Path)
        self.assertIsInstance(config.CARD_FILE, Path)
        self.assertIsInstance(config.WORLD_DIR, Path)
        self.assertIsInstance(config.SYSTEM_PROMPTS_DIR, Path)
        self.assertIsInstance(config.DEFAULT_CHARACTER_FILE, str)
        self.assertIsInstance(config.SYSTEM_PROMPTS_FILE, str)
        self.assertIsInstance(config.DEFAULT_USER_NAME, str)
        self.assertEqual(config.CATALOG_MAX_ITEMS, 40)
        self.assertEqual(config.CARD_FIELD_MAX_CHARS, 20000)
        self.assertEqual(config.CARD_TOTAL_MAX_CHARS, 60000)

    def test_card_file_remains_startup_derived(self):
        import bridge.config as config

        original_card = config.CARD_FILE
        with patch.object(
            config,
            "DEFAULT_CHARACTER_FILE",
            "temporary.png",
        ):
            self.assertEqual(config.CARD_FILE, original_card)

    def test_runtime_context_facade_exports_canonical_functions(self):
        import bridge.runtime as rt
        import bridge.runtime_context as context

        for name in RUNTIME_CONTEXT_EXPORTS:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(rt, name),
                    getattr(context, name),
                )

    def test_db_context_is_shared_across_runtime_and_module(self):
        import bridge.runtime as rt
        import bridge.runtime_context as context

        first = sqlite3.connect(":memory:")
        second = sqlite3.connect(":memory:")
        try:
            rt.set_db_connection_context(first)
            self.assertIs(context.db_connection_context(), first)
            context.set_db_connection_context(second)
            self.assertIs(rt.db_connection_context(), second)
        finally:
            context.set_db_connection_context(None)
            first.close()
            second.close()

    def test_panel_context_is_shared_across_runtime_and_module(self):
        import bridge.runtime as rt
        import bridge.runtime_context as context

        try:
            rt.set_panel_session_context("session-a")
            self.assertEqual(
                context.panel_session_context(),
                "session-a",
            )
            context.set_panel_actor_context("actor-b")
            self.assertEqual(rt.panel_actor_context(), "actor-b")
        finally:
            context.set_panel_session_context(None)
            context.set_panel_actor_context(None)


    def test_common_no_longer_owns_extracted_context_state(self):
        source = (
            REPO_ROOT / "bridge" / "common.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn(
            "_PANEL_SESSION_CONTEXT = threading.local()",
            source,
        )
        self.assertNotIn(
            "_DB_CONNECTION_CONTEXT = threading.local()",
            source,
        )
        for name in RUNTIME_CONTEXT_EXPORTS:
            self.assertNotIn(f"def {name}(", source)

    def test_common_no_longer_defines_extracted_card_config(self):
        source = (
            REPO_ROOT / "bridge" / "common.py"
        ).read_text(encoding="utf-8")

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

    def test_runtime_facade_exports_canonical_panel_utils(self):
        import bridge.panel_utils as panel_utils
        import bridge.runtime as rt

        for name in PANEL_UTIL_EXPORTS:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(rt, name),
                    getattr(panel_utils, name),
                )

    def test_runtime_facade_exports_complete_canonical_card_content_api(self):
        import bridge.card_content as card_content
        import bridge.runtime as rt

        for name in CARD_CONTENT_EXPORTS:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(rt, name),
                    getattr(card_content, name),
                )

    def test_cards_shell_does_not_redefine_migrated_content_or_panel_functions(self):
        source = (
            REPO_ROOT / "bridge" / "cards.py"
        ).read_text(encoding="utf-8")

        for name in (*PANEL_UTIL_EXPORTS, *CARD_CONTENT_EXPORTS):
            with self.subTest(name=name):
                self.assertNotIn(f"def {name}(", source)

    def test_character_identity_imports_character_display_name(self):
        source = (
            REPO_ROOT / "bridge" / "character_identity.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "from bridge.card_content import character_display_name",
            source,
        )
        self.assertNotIn(
            "def character_display_name(",
            source,
        )

    def test_card_fields_default_name_follows_canonical_config(self):
        import bridge.card_content as card_content
        import bridge.config as config

        with patch.object(
            config,
            "DEFAULT_CHARACTER_FILE",
            "Seraphina.png",
        ):
            fields = card_content.card_fields(
                {"data": {"name": ""}}
            )
        self.assertEqual(fields["name"], "Seraphina")

    def test_character_and_world_paths_follow_canonical_config(self):
        import bridge.card_content as card_content
        import bridge.config as config

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
                patch.object(config, "CHARACTER_DIR", characters),
                patch.object(config, "WORLD_DIR", worlds),
            ):
                self.assertEqual(
                    [path.name for path in card_content.character_card_paths()],
                    ["one.png"],
                )
                self.assertEqual(
                    [path.name for path in card_content.world_file_paths()],
                    ["lore.json"],
                )
                self.assertEqual(
                    card_content.safe_character_path("one.png"),
                    characters / "one.png",
                )
                self.assertEqual(
                    card_content.safe_world_path("lore.json"),
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
            result = card_content.build_system_prompt(
                fields,
                "User",
            )

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
            result = card_content.build_system_prompt(
                fields,
                "User",
            )

        self.assertRegex(result, r"^\d{2}:\d{2}$")


if __name__ == "__main__":
    unittest.main()

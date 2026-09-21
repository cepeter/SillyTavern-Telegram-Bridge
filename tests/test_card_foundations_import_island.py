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


if __name__ == "__main__":
    unittest.main()

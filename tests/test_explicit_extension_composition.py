"""Explicit extension composition regression tests."""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

from settings_test_support import SettingsTestCase

REPO_ROOT = Path(__file__).parents[1]


class ExplicitExtensionCompositionTests(SettingsTestCase):
    def _run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_importing_main_does_not_mutate_extension_registry(self):
        completed = self._run_python(
            "import bridge.extension_registry as registry\n"
            "registry.reset_extension_registry()\n"
            "before = registry.extension_registry_snapshot()\n"
            "import bridge.main\n"
            "after = registry.extension_registry_snapshot()\n"
            "assert after == before, (before, after)\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_importing_composition_helper_does_not_mutate_registry(self):
        completed = self._run_python(
            "import bridge.extension_registry as registry\n"
            "registry.reset_extension_registry()\n"
            "before = registry.extension_registry_snapshot()\n"
            "import bridge.application_composition\n"
            "after = registry.extension_registry_snapshot()\n"
            "assert after == before, (before, after)\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_main_initializes_extensions_once_before_checks_or_runtime(self):
        tree = ast.parse((REPO_ROOT / "bridge/main.py").read_text(encoding="utf-8"))
        main_function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_main")
        calls = [
            (node.func.id, node.lineno)
            for node in ast.walk(main_function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        init_lines = [line for name, line in calls if name == "_initialize_extensions"]
        self.assertEqual(len(init_lines), 1)
        self.assertLess(next(line for name, line in calls if name == "_load_startup_config"), init_lines[0])
        for name in ("set_bot_commands", "run_check", "run_bridge_runtime"):
            self.assertLess(init_lines[0], next(line for called, line in calls if called == name))

    def test_explicit_extension_composition_is_deterministic(self):
        completed = self._run_python(
            "import bridge.extension_registry as registry\n"
            "from bridge.application_composition import initialize_extensions\n"
            "registry.reset_extension_registry()\n"
            "initialize_extensions()\n"
            "first = registry.extension_registry_snapshot()\n"
            "initialize_extensions()\n"
            "second = registry.extension_registry_snapshot()\n"
            "expected = {\n"
            "  'command_routes': ('scene_state', 'director_goals', 'memory_curator'),\n"
            "  'post_retain': ('scene_state', 'memory_curator'),\n"
            "  'summary_context': ('scene_state',),\n"
            "  'summary_clear': ('scene_state',),\n"
            "}\n"
            "assert first == expected, first\n"
            "assert second == expected, second\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )


if __name__ == "__main__":
    unittest.main()

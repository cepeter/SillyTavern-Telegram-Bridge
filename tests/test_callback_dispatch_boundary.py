from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys
import unittest

BRIDGE_DIR = Path(__file__).parents[1] / "bridge"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class CallbackDispatchBoundaryTests(unittest.TestCase):
    def test_callbacks_does_not_import_panel_callback_routes(self):
        self.assertNotIn(
            "bridge.panel_callback_routes",
            imported_modules(BRIDGE_DIR / "callbacks.py"),
        )

    def test_dispatcher_is_canonical_owner(self):
        import bridge.callback_dispatch as callback_dispatch
        import bridge.callbacks as callbacks

        self.assertTrue(callable(callback_dispatch.process_callback))
        for name in (
            "process_callback",
            "ensure_session",
            "DEFAULT_MODEL",
            "answer_callback",
            "handle_group_panel_callback",
            "panel_owner_for_message",
            "panel_session_for_message",
            "send_text",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(callbacks, name))

    def test_callbacks_import_does_not_load_route_module(self):
        code = (
            "import sys\n"
            "import bridge.callbacks\n"
            "assert 'bridge.panel_callback_routes' not in sys.modules\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )


if __name__ == "__main__":
    unittest.main()

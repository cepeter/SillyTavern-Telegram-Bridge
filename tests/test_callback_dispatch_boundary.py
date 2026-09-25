from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from application_test_setup import make_test_session_service
from settings_test_support import SettingsTestCase, make_test_settings

from bridge.session_service import SessionService

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


class CallbackDispatchBoundaryTests(SettingsTestCase):
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

    def test_queued_callback_routes_without_duplicate_ack(self):
        import bridge.callback_dispatch as callback_dispatch

        callback = {
            "id": "cb-1",
            "from": {"id": "user-1"},
            "data": "status:refresh",
            "_queued": True,
            "message": {"chat": {"id": "chat"}},
        }
        external_answer = Mock()
        routed = Mock(return_value=True)
        services = SimpleNamespace(
            config=make_test_settings(),
            group=object(),
            provider=object(),
            delivery=object(),
            memory=object(),
            persona=object(),
            sync=object(),
            session=make_test_session_service(app_settings=make_test_settings()),
        )
        with (
            patch.object(callback_dispatch, "answer_callback", external_answer),
            patch.object(
                SessionService,
                "ensure",
                return_value={"session_id": "session"},
            ),
            patch.object(
                callback_dispatch,
                "handle_primary_panel_callback",
                routed,
            ),
        ):
            callback_dispatch.process_callback(
                object(),
                "token",
                callback,
                services=services,
            )

        routed.assert_called_once()
        external_answer.assert_not_called()

    def test_callbacks_import_does_not_load_route_module(self):
        code = "import sys\nimport bridge.callbacks\nassert 'bridge.panel_callback_routes' not in sys.modules\n"
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

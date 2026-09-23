from __future__ import annotations

from application_test_setup import make_test_group_service

import ast
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import bridge.database as database
import bridge.session_naming as session_naming
import bridge.telegram as telegram

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


def imported_names(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


class PendingPromptOwnershipTests(unittest.TestCase):
    def test_common_does_not_import_telegram(self):
        self.assertNotIn(
            "bridge.telegram",
            imported_modules(BRIDGE_DIR / "common.py"),
        )

    def test_telegram_is_canonical_owner(self):
        import bridge.common as common

        self.assertTrue(callable(telegram.delete_pending_input_prompts))
        self.assertFalse(hasattr(common, "delete_pending_input_prompts"))

    def test_consumers_import_canonical_owner(self):
        for filename in ("input_flows.py", "session_naming.py"):
            path = BRIDGE_DIR / filename
            self.assertIn(
                "delete_pending_input_prompts",
                imported_names(path, "bridge.telegram"),
                filename,
            )
            self.assertNotIn(
                "delete_pending_input_prompts",
                imported_names(path, "bridge.common"),
                filename,
            )

    def test_delete_pending_input_prompts_continues_after_bad_id(self):
        calls = []

        def fake_request(_token, method, payload):
            calls.append((method, payload))
            return {}

        with patch.object(telegram, "telegram_request", side_effect=fake_request):
            telegram.delete_pending_input_prompts(
                "token",
                "chat",
                {"prompt_message_ids": [90, "bad", "91"]},
            )

        self.assertEqual(
            calls,
            [
                ("deleteMessage", {"chat_id": "chat", "message_id": 90}),
                ("deleteMessage", {"chat_id": "chat", "message_id": 91}),
            ],
        )

    def test_delete_pending_input_prompts_accepts_scalar_id(self):
        calls = []
        with patch.object(
            telegram,
            "telegram_request",
            side_effect=lambda _token, method, payload: calls.append((method, payload)) or {},
        ):
            telegram.delete_pending_input_prompts(
                "token",
                "chat",
                {"prompt_message_ids": "92"},
            )

        self.assertEqual(
            calls,
            [("deleteMessage", {"chat_id": "chat", "message_id": 92})],
        )

    def test_delete_pending_input_prompts_continues_after_request_failure(self):
        seen = []

        def fake_request(_token, _method, payload):
            message_id = payload["message_id"]
            seen.append(message_id)
            if message_id == 90:
                raise RuntimeError("message already unavailable")
            return {}

        with patch.object(telegram, "telegram_request", side_effect=fake_request):
            telegram.delete_pending_input_prompts(
                "token",
                "chat",
                {"prompt_message_ids": [90, 91]},
            )

        self.assertEqual(seen, [90, 91])

    def test_start_session_name_input_clears_conflicting_prompt_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = database.db_connect(Path(tmp) / "prompt.sqlite3")
            state = {
                "session_id": "default",
                "expires_at": time.time() + 600,
                "prompt_message_ids": [77, 78],
            }
            session_naming.set_meta(
                db, "settings_input:chat", json.dumps(state)
            )
            seen = []
            with patch.object(
                session_naming,
                "delete_pending_input_prompts",
                side_effect=lambda token, chat_id, value: seen.append(
                    (token, chat_id, value)
                ),
            ), patch.object(session_naming, "send_text", return_value=[99]):
                session_naming.start_session_name_input(
                    db,
                    "token",
                    "chat",
                    {"session_id": "default", "model_id": "model"},
                    group_service=make_test_group_service(),
                )
            self.assertEqual(seen, [("token", "chat", state)])
            self.assertEqual(
                session_naming.get_meta(db, "settings_input:chat", ""),
                "",
            )
            db.close()


if __name__ == "__main__":
    unittest.main()

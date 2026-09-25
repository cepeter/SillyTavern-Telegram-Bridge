import unittest
from unittest.mock import patch

import bridge.extension_registry as registry


class ExtensionRegistryTests(unittest.TestCase):
    def test_none_summary_hook_preserves_accumulated_summary(self):
        with patch.dict(registry._SUMMARY_CONTEXT_HOOKS, clear=True):
            registry.register_summary_context_hook(
                "none",
                lambda summary, db, chat_id, session: None,
            )
            self.assertEqual(
                registry.apply_summary_context_hooks(
                    "existing summary",
                    None,
                    "chat",
                    {"session_id": "session"},
                ),
                "existing summary",
            )

    def test_post_retain_failure_does_not_block_later_hook(self):
        calls = []

        def fail(*_args):
            calls.append("first")
            raise RuntimeError("boom")

        def succeed(*_args):
            calls.append("second")

        with patch.dict(registry._POST_RETAIN_HOOKS, clear=True):
            registry.register_post_retain_hook("first", fail)
            registry.register_post_retain_hook("second", succeed)
            with self.assertLogs(level="ERROR"):
                registry.run_post_retain_hooks(
                    None,
                    "chat",
                    {"session_id": "session"},
                    {"name": "Character"},
                    object(),
                )
        self.assertEqual(calls, ["first", "second"])

    def test_summary_clear_failure_does_not_block_later_hook(self):
        calls = []

        def fail(*_args):
            calls.append("first")
            raise RuntimeError("boom")

        def succeed(*_args):
            calls.append("second")

        with patch.dict(registry._SUMMARY_CLEAR_HOOKS, clear=True):
            registry.register_summary_clear_hook("first", fail)
            registry.register_summary_clear_hook("second", succeed)
            with self.assertLogs(level="ERROR"):
                registry.run_summary_clear_hooks(None, "chat", "session")
        self.assertEqual(calls, ["first", "second"])

    def test_command_route_failure_propagates(self):
        def fail(*_args, **_kwargs):
            raise RuntimeError("command failed")

        with patch.dict(registry._COMMAND_ROUTES, clear=True):
            registry.register_command_route("failing", fail)
            with self.assertRaisesRegex(RuntimeError, "command failed"):
                registry.dispatch_command_routes()


if __name__ == "__main__":
    unittest.main()

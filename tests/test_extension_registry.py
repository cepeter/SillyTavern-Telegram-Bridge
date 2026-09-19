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

    def test_director_customization_provider_registers_and_returns_value(self):
        with patch.object(registry, "_DIRECTOR_CUSTOMIZATION_PROVIDER", None):
            expected = registry.DirectorCustomization(
                model="utility::director",
                hidden_instructions="Hidden objective.",
                max_tokens=220,
                speaker_context="Hidden scene objective: Hidden objective.",
            )

            registry.register_director_customization_provider(
                "director_goals",
                lambda db, chat_id, session: expected,
            )

            self.assertEqual(
                registry.get_director_customization(
                    None,
                    "chat",
                    {"session_id": "session"},
                ),
                expected,
            )
            self.assertEqual(
                registry.extension_registry_snapshot()["director_customization"],
                ("director_goals",),
            )

    def test_director_customization_provider_rejects_duplicate_registration(self):
        with patch.object(registry, "_DIRECTOR_CUSTOMIZATION_PROVIDER", None):
            registry.register_director_customization_provider(
                "first",
                lambda db, chat_id, session: None,
            )
            with self.assertRaisesRegex(RuntimeError, "already registered"):
                registry.register_director_customization_provider(
                    "second",
                    lambda db, chat_id, session: None,
                )

    def test_director_customization_provider_failure_returns_none(self):
        def fail(_db, _chat_id, _session):
            raise RuntimeError("boom")

        with patch.object(registry, "_DIRECTOR_CUSTOMIZATION_PROVIDER", None):
            registry.register_director_customization_provider("broken", fail)
            with self.assertLogs(level="ERROR") as logs:
                result = registry.get_director_customization(
                    None,
                    "chat",
                    {"session_id": "session"},
                )

            self.assertIsNone(result)
            self.assertTrue(
                any(
                    "Director customization provider failed: broken" in line
                    for line in logs.output
                )
            )

    def test_reset_extension_registry_clears_director_provider(self):
        with (
            patch.dict(registry._COMMAND_ROUTES, clear=True),
            patch.dict(registry._POST_RETAIN_HOOKS, clear=True),
            patch.dict(registry._SUMMARY_CONTEXT_HOOKS, clear=True),
            patch.dict(registry._SUMMARY_CLEAR_HOOKS, clear=True),
            patch.object(registry, "_DIRECTOR_CUSTOMIZATION_PROVIDER", None),
        ):
            registry.register_director_customization_provider(
                "director_goals",
                lambda db, chat_id, session: None,
            )
            registry.reset_extension_registry()
            self.assertEqual(
                registry.extension_registry_snapshot()["director_customization"],
                (),
            )


if __name__ == "__main__":
    unittest.main()

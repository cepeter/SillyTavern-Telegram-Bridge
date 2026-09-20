from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bridge.runtime as rt
import bridge.extension_registry as extension_registry
from bridge.extension_registry import extension_registry_snapshot
from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES, RuntimeStage, load_runtime_namespace


class RuntimeLoaderTests(unittest.TestCase):
    def test_runtime_exposes_structured_load_report(self):
        stages = {entry["stage"] for entry in rt.RUNTIME_LOAD_REPORT}
        self.assertEqual(
            stages,
            {
                "core",
                "recovery_overrides",
                "sync_extensions",
                "native_adapter_overrides",
                "identity_extensions",
                "safety_overrides",
            },
        )
        self.assertTrue(
            any(
                entry["public_callable_overrides"]
                for entry in rt.RUNTIME_LOAD_REPORT
                if entry["stage"] in {
                    "recovery_overrides",
                    "native_adapter_overrides",
                    "safety_overrides",
                }
            )
        )
        by_module = {entry["module"]: entry["public_callable_overrides"] for entry in rt.RUNTIME_LOAD_REPORT}
        self.assertEqual(by_module["scene_state.py"], ())
        self.assertEqual(by_module["memory_curator.py"], ())
        self.assertEqual(by_module["director_goals.py"], ())

        extensions = extension_registry_snapshot()
        self.assertEqual(
            extensions["command_routes"],
            ("scene_state", "director_goals", "memory_curator"),
        )
        self.assertEqual(extensions["post_retain"], ("scene_state", "memory_curator"))
        self.assertEqual(extensions["summary_context"], ("scene_state",))
        self.assertEqual(extensions["summary_clear"], ("scene_state",))
        self.assertEqual(
            extensions["director_customization"],
            ("director_goals",),
        )

    def test_director_goals_not_allowlisted_for_public_callable_overrides(self):
        safety = next(stage for stage in DEFAULT_RUNTIME_STAGES if stage.name == "safety_overrides")
        self.assertEqual(safety.allowed_overrides_for("director_goals.py"), frozenset())

    def test_runtime_reload_resets_extension_registry_deterministically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.py").write_text(
                "from bridge.extension_registry import "
                "register_command_route, register_director_customization_provider\n"
                "def route_one(*args, **kwargs):\n    return False\n"
                "def director_policy(db, chat_id, session):\n    return None\n"
                'register_command_route("first", route_one)\n'
                'register_director_customization_provider("policy", director_policy)\n',
                encoding="utf-8",
            )
            (root / "two.py").write_text(
                "from bridge.extension_registry import register_command_route\n"
                "def route_two(*args, **kwargs):\n    return False\n"
                'register_command_route("second", route_two)\n',
                encoding="utf-8",
            )
            stages = (RuntimeStage("core", ("one.py", "two.py")),)
            with (
                patch.dict(extension_registry._COMMAND_ROUTES, clear=True),
                patch.dict(extension_registry._POST_RETAIN_HOOKS, clear=True),
                patch.dict(extension_registry._SUMMARY_CONTEXT_HOOKS, clear=True),
                patch.dict(extension_registry._SUMMARY_CLEAR_HOOKS, clear=True),
                patch.object(extension_registry, "_DIRECTOR_CUSTOMIZATION_PROVIDER", None),
            ):
                load_runtime_namespace({"__name__": "runtime_one"}, root, stages)
                first = extension_registry_snapshot()
                load_runtime_namespace({"__name__": "runtime_two"}, root, stages)
                second = extension_registry_snapshot()

                self.assertEqual(first, second)
                self.assertEqual(first["command_routes"], ("first", "second"))
                self.assertEqual(first["director_customization"], ("policy",))

    def test_core_stage_rejects_silent_public_callable_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.py").write_text("def action():\n    return 1\n", encoding="utf-8")
            (root / "two.py").write_text("def action():\n    return 2\n", encoding="utf-8")
            namespace = {"__name__": "test_runtime"}
            stages = (RuntimeStage("core", ("one.py", "two.py")),)
            with self.assertRaisesRegex(RuntimeError, "unexpectedly overrides"):
                load_runtime_namespace(namespace, root, stages, reset_extensions=False)

    def test_override_stage_records_declared_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.py").write_text("def action():\n    return 1\n", encoding="utf-8")
            (root / "two.py").write_text("def action():\n    return 2\n", encoding="utf-8")
            namespace = {"__name__": "test_runtime"}
            stages = (
                RuntimeStage("core", ("one.py",)),
                RuntimeStage(
                    "hardening",
                    ("two.py",),
                    (("two.py", ("action",)),),
                ),
            )
            report = load_runtime_namespace(namespace, root, stages, reset_extensions=False)
            self.assertEqual(report[-1]["public_callable_overrides"], ("action",))
            self.assertEqual(namespace["action"](), 2)

    def test_override_stage_rejects_non_allowlisted_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.py").write_text(
                "def action():\n    return 1\n\ndef extra():\n    return 1\n",
                encoding="utf-8",
            )
            (root / "two.py").write_text(
                "def action():\n    return 2\n\ndef extra():\n    return 2\n",
                encoding="utf-8",
            )
            namespace = {"__name__": "test_runtime"}
            stages = (
                RuntimeStage("core", ("one.py",)),
                RuntimeStage(
                    "hardening",
                    ("two.py",),
                    (("two.py", ("action",)),),
                ),
            )
            with self.assertRaisesRegex(RuntimeError, "extra"):
                load_runtime_namespace(namespace, root, stages, reset_extensions=False)

    def test_composition_module_is_not_a_runtime_stage(self):
        loaded_modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn("composition.py", loaded_modules)

    def test_group_director_service_is_not_a_runtime_stage(self):
        loaded_modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn("group_director_service.py", loaded_modules)

    def test_memory_service_is_not_a_runtime_stage(self):
        loaded_modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn("memory_service.py", loaded_modules)

    def test_persona_service_is_not_a_runtime_stage(self):
        loaded_modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn("persona_service.py", loaded_modules)

    def test_loader_rejects_module_outside_base_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / "outside_runtime_loader_test.py"
            outside.write_text("value = 1\n", encoding="utf-8")
            try:
                stages = (RuntimeStage("core", ("../outside_runtime_loader_test.py",)),)
                with self.assertRaisesRegex(RuntimeError, "outside runtime base directory"):
                    load_runtime_namespace(
                        {"__name__": "test_runtime"},
                        root,
                        stages,
                        reset_extensions=False,
                    )
            finally:
                outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

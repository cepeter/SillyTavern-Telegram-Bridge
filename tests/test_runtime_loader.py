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
        self.assertEqual(
            tuple(
                (entry["stage"], entry["module"], entry["public_callable_overrides"])
                for entry in rt.RUNTIME_LOAD_REPORT
            ),
            (("core", "main.py", ()),),
        )

        extensions = extension_registry_snapshot()
        self.assertEqual(
            extensions["command_routes"],
            ("scene_state", "director_goals", "memory_curator"),
        )
        self.assertEqual(
            extensions["post_retain"],
            ("scene_state", "memory_curator"),
        )
        self.assertEqual(extensions["summary_context"], ("scene_state",))
        self.assertEqual(extensions["summary_clear"], ("scene_state",))
        self.assertEqual(
            extensions["director_customization"],
            ("director_goals",),
        )


    def test_director_goals_not_allowlisted_for_public_callable_overrides(self):
        loaded = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn("director_goals.py", loaded)
        for stage in DEFAULT_RUNTIME_STAGES:
            self.assertEqual(
                stage.allowed_public_callable_overrides,
                (),
            )

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

    def test_phase_7a_import_island_is_never_exec_loaded(self):
        loaded_modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertTrue(
            {
                "performance.py",
                "native_cache.py",
                "schema.py",
                "runtime_defaults.py",
            }.isdisjoint(loaded_modules)
        )

    def test_phase_7b1_persistence_island_is_never_exec_loaded(self):
        loaded_modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertTrue(
            {"database.py", "config.py"}.isdisjoint(loaded_modules)
        )


    def test_phase_7b1_preserves_remaining_core_order(self):
        core = next(
            stage
            for stage in DEFAULT_RUNTIME_STAGES
            if stage.name == "core"
        )
        self.assertEqual(core.modules, ("main.py",))


    def test_phase_7b2_preserves_legacy_core_order_and_cards_shell(self):
        core = next(
            stage
            for stage in DEFAULT_RUNTIME_STAGES
            if stage.name == "core"
        )
        self.assertEqual(core.modules, ("main.py",))

        loaded = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertTrue(
            {
                "runtime_context.py",
                "panel_utils.py",
                "card_content.py",
                "callback_tokens.py",
                "cards.py",
            }.isdisjoint(loaded)
        )

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

    def test_sync_service_is_not_a_runtime_stage(self):
        loaded_modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn("sync_service.py", loaded_modules)

    def test_job_service_is_not_a_runtime_stage(self):
        loaded_modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn("job_service.py", loaded_modules)

    def test_scheduler_safety_is_not_a_runtime_stage(self):
        loaded_modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn("scheduler_safety.py", loaded_modules)
        for stage in DEFAULT_RUNTIME_STAGES:
            self.assertEqual(
                stage.allowed_overrides_for("scheduler_safety.py"),
                frozenset(),
            )

    def test_scheduler_owners_are_canonical_files(self):
        self.assertEqual(
            Path(rt.db_connect.__code__.co_filename).name,
            "database.py",
        )
        self.assertEqual(
            Path(rt.recover_jobs.__code__.co_filename).name,
            "database.py",
        )
        self.assertEqual(
            Path(rt.submit_durable_chat_job.__code__.co_filename).name,
            "main.py",
        )


    def test_persona_integrity_writes_are_not_state_integrity_overrides(self):
        for stage in DEFAULT_RUNTIME_STAGES:
            for _filename, names in stage.allowed_public_callable_overrides:
                self.assertNotIn("upsert_native_persona", names)
                self.assertNotIn("delete_native_persona", names)

    def test_persona_write_owners_are_persona_sync(self):
        self.assertEqual(
            Path(
                rt.upsert_native_persona.__code__.co_filename
            ).name,
            "persona_sync.py",
        )
        self.assertEqual(
            Path(
                rt.delete_native_persona.__code__.co_filename
            ).name,
            "persona_sync.py",
        )

    def test_persona_load_public_owner_is_persona_sync(self):
        self.assertEqual(
            Path(
                rt.load_personas.__code__.co_filename
            ).name,
            "persona_sync.py",
        )


    def test_persona_sync_remains_loaded_without_public_overrides(self):
        loaded = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn("persona_sync.py", loaded)
        self.assertNotIn(
            "persona_sync.py",
            {
                item["module"]
                for item in rt.RUNTIME_LOAD_REPORT
            },
        )

    def test_load_personas_has_no_runtime_override_allowlist(self):
        for stage in DEFAULT_RUNTIME_STAGES:
            for filename, names in (
                stage.allowed_public_callable_overrides
            ):
                self.assertNotIn(
                    "load_personas",
                    names,
                    msg=(
                        f"{filename} still overrides "
                        "load_personas"
                    ),
                )

    def test_no_public_override_allowlist_remains_during_phase_6h_cutover(self):
        allowlisted_modules = {
            filename
            for stage in DEFAULT_RUNTIME_STAGES
            for filename, names in (
                stage.allowed_public_callable_overrides
            )
            if names
        }
        self.assertEqual(
            allowlisted_modules,
            set(),
        )


    def test_hindsight_writes_are_not_state_integrity_overrides(self):
        for stage in DEFAULT_RUNTIME_STAGES:
            for _filename, names in stage.allowed_public_callable_overrides:
                self.assertNotIn("retain_session_memory", names)
                self.assertNotIn("purge_hindsight_session", names)

    def test_hindsight_public_owners_are_memory_module(self):
        self.assertEqual(
            Path(
                rt.retain_session_memory.__code__.co_filename
            ).name,
            "memory.py",
        )
        self.assertEqual(
            Path(
                rt.purge_hindsight_session.__code__.co_filename
            ).name,
            "memory.py",
        )

    def test_sync_snapshot_public_owner_is_sync_core(self):
        self.assertEqual(
            Path(
                rt.apply_sync_snapshot.__code__.co_filename
            ).name,
            "sync_core.py",
        )

    def test_state_integrity_is_not_a_runtime_module(self):
        loaded = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn(
            "state_integrity.py",
            loaded,
        )

    def test_runtime_report_has_no_state_integrity_entry(self):
        modules = {
            item["module"]
            for item in rt.RUNTIME_LOAD_REPORT
        }
        self.assertNotIn(
            "state_integrity.py",
            modules,
        )

    def test_apply_sync_snapshot_has_no_runtime_override_allowlist(self):
        for stage in DEFAULT_RUNTIME_STAGES:
            for filename, names in (
                stage.allowed_public_callable_overrides
            ):
                self.assertNotIn(
                    "apply_sync_snapshot",
                    names,
                    msg=f"{filename} still overrides apply_sync_snapshot",
                )

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

    def test_sync_schema_public_owner_is_schema(self):
        self.assertEqual(
            Path(
                rt.initialize_database_schema
                .__code__.co_filename
            ).name,
            "schema.py",
        )

    def test_sync_poll_public_owner_is_sync_api(self):
        self.assertEqual(
            Path(
                rt.phase3_sync_poll
                .__code__.co_filename
            ).name,
            "sync_api.py",
        )

    def test_sync_safety_is_not_a_runtime_module(self):
        modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn(
            "sync_safety.py",
            modules,
        )

    def test_runtime_report_has_no_sync_safety_entry(self):
        modules = {
            item["module"]
            for item in rt.RUNTIME_LOAD_REPORT
        }
        self.assertNotIn(
            "sync_safety.py",
            modules,
        )

    def test_sync_safety_functions_have_no_override_allowlist(self):
        for stage in DEFAULT_RUNTIME_STAGES:
            for filename, names in (
                stage.allowed_public_callable_overrides
            ):
                for name in (
                    "initialize_database_schema",
                    "phase3_sync_poll",
                ):
                    self.assertNotIn(
                        name,
                        names,
                        msg=(
                            f"{filename} still overrides "
                            f"{name}"
                        ),
                    )


    def test_recovery_stage_and_module_are_absent(self):
        self.assertNotIn(
            "recovery_overrides",
            {
                stage.name
                for stage in DEFAULT_RUNTIME_STAGES
            },
        )
        self.assertNotIn(
            "recovery.py",
            {
                module
                for stage in DEFAULT_RUNTIME_STAGES
                for module in stage.modules
            },
        )

    def test_runtime_report_has_no_recovery_entry(self):
        self.assertNotIn(
            "recovery_overrides",
            {
                entry["stage"]
                for entry in rt.RUNTIME_LOAD_REPORT
            },
        )
        self.assertNotIn(
            "recovery.py",
            {
                entry["module"]
                for entry in rt.RUNTIME_LOAD_REPORT
            },
        )


if __name__ == "__main__":
    unittest.main()

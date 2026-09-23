"""PR 57 native runtime compatibility retirement guards."""

from __future__ import annotations

from pathlib import Path
from dataclasses import MISSING
import ast
import inspect
import unittest

from bridge.composition import BridgeServices, build_bridge_services


REPO_ROOT = Path(__file__).parents[1]
BRIDGE_DIR = REPO_ROOT / "bridge"
TESTS_DIR = REPO_ROOT / "tests"


class NativeRuntimeRetirementTests(unittest.TestCase):
    def test_runtime_compatibility_module_is_deleted(self):
        self.assertFalse((BRIDGE_DIR / "runtime.py").exists())

    def test_ambient_runtime_context_module_is_deleted(self):
        self.assertFalse((BRIDGE_DIR / "runtime_context.py").exists())

    def test_no_ambient_runtime_context_in_production_source(self):
        forbidden_tokens = (
            "set_panel_session_context",
            "panel_session_context",
            "set_panel_actor_context",
            "panel_actor_context",
            "set_db_connection_context",
            "db_connection_context",
        )
        offenders = {}
        for path in sorted(BRIDGE_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            hits = [token for token in forbidden_tokens if token in source]
            tree = ast.parse(source)
            imports_runtime_context = any(
                (
                    isinstance(node, ast.Import)
                    and any(
                        alias.name == "bridge.runtime_context"
                        for alias in node.names
                    )
                )
                or (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "bridge.runtime_context"
                )
                for node in ast.walk(tree)
            )
            if imports_runtime_context:
                hits.append("import bridge.runtime_context")
            if hits:
                offenders[path.relative_to(REPO_ROOT).as_posix()] = sorted(
                    set(hits)
                )
        self.assertEqual(offenders, {})

    def test_runtime_test_facade_is_deleted(self):
        self.assertFalse((TESTS_DIR / "runtime_test_facade.py").exists())

    def test_transitional_dependency_helpers_are_deleted(self):
        self.assertFalse((TESTS_DIR / "dependency_patch.py").exists())
        self.assertFalse((BRIDGE_DIR / "ordinary_dependencies.py").exists())

    def test_test_setup_has_no_patch_propagation_magic(self):
        source = (TESTS_DIR / "application_test_setup.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "ModuleType",
            "sys.modules",
            "__class__",
            "_IdentityPropagatingModule",
            "_install_identity_propagation",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


    def test_durable_job_compatibility_module_is_deleted(self):
        self.assertFalse((BRIDGE_DIR / "job_runtime.py").exists())

    def test_all_application_services_are_required_by_composition(self):
        for name in (
            "jobs",
            "group_director",
            "memory",
            "persona",
            "sync",
        ):
            with self.subTest(name=name):
                self.assertIs(
                    BridgeServices.__dataclass_fields__[name].default,
                    MISSING,
                )
                self.assertIs(
                    inspect.signature(build_bridge_services)
                    .parameters[name]
                    .default,
                    inspect.Parameter.empty,
                )
                self.assertNotIn(
                    "None",
                    str(BridgeServices.__dataclass_fields__[name].type),
                )

    def test_legacy_durable_job_wrappers_are_deleted(self):
        source = (BRIDGE_DIR / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("def submit_durable_chat_job(", source)
        self.assertNotIn("def dispatch_recovered_jobs(", source)

    def test_no_durable_job_fallback_helpers_remain(self):
        forbidden = {
            "compatibility_job_service",
            "jobs_for_services",
            "_compatibility_job_service",
            "_jobs_for_services",
        }
        offenders = {}
        for path in sorted(BRIDGE_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            hits = sorted(name for name in forbidden if name in source)
            if hits:
                offenders[path.relative_to(REPO_ROOT).as_posix()] = hits
        self.assertEqual(offenders, {})

    def test_group_director_compatibility_wrappers_are_deleted(self):
        source = (BRIDGE_DIR / "groups.py").read_text(encoding="utf-8")
        for forbidden in (
            "def _compat_group_director_service(",
            "def group_director_plan(",
            "def group_prompt_context(",
            "def parse_group_director_decision(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_application_routes_require_service_graph(self):
        from bridge.callback_dispatch import process_callback
        from bridge.command_routes import handle_command_route
        from bridge.message_commands import process_message

        for function in (
            process_message,
            process_callback,
            handle_command_route,
        ):
            with self.subTest(function=function.__name__):
                self.assertIs(
                    inspect.signature(function).parameters["services"].default,
                    inspect.Parameter.empty,
                )

    def test_required_service_routes_do_not_use_optional_service_lookup(self):
        for filename in (
            "message_commands.py",
            "callback_dispatch.py",
            "command_routes.py",
        ):
            source = (BRIDGE_DIR / filename).read_text(encoding="utf-8")
            self.assertNotIn("services=None", source, filename)
            for forbidden in (
                'getattr(services, "memory", None)',
                'getattr(services, "persona", None)',
                'getattr(services, "sync", None)',
                'getattr(services, "group_director", None)',
            ):
                self.assertNotIn(forbidden, source, filename)

    def test_no_optional_application_service_parameters_remain(self):
        forbidden = (
            "memory_service=None",
            "persona_service=None",
            "sync_service=None",
            "group_director_service=None",
        )
        offenders = {}
        for path in sorted(BRIDGE_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            hits = [value for value in forbidden if value in source]
            if hits:
                offenders[path.relative_to(REPO_ROOT).as_posix()] = hits
        self.assertEqual(offenders, {})

    def test_startup_path_ownership_has_no_legacy_common_definitions(self):
        common_source = (BRIDGE_DIR / "common.py").read_text(
            encoding="utf-8"
        )
        config_source = (BRIDGE_DIR / "config.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "ENV_FILE =",
            "PROVIDER_CONFIG_FILE =",
            "MODEL_CACHE_FILE =",
            "CHARACTER_BACKUP_DIR =",
            "LOG_FILE =",
        ):
            self.assertNotIn(forbidden, common_source)

        self.assertNotIn("ENV_FILE =", config_source)
        for required in (
            "PROVIDER_CONFIG_FILE =",
            "MODEL_CACHE_FILE =",
            "CHARACTER_BACKUP_DIR =",
            "LOG_FILE =",
        ):
            self.assertIn(required, config_source)

        import bridge.common as common

        for retired_export in (
            "ENV_FILE",
            "PROVIDER_CONFIG_FILE",
            "MODEL_CACHE_FILE",
            "CHARACTER_BACKUP_DIR",
            "LOG_FILE",
        ):
            self.assertFalse(
                hasattr(common, retired_export),
                retired_export,
            )

    def test_single_file_system_prompt_fallback_is_deleted(self):
        offenders = {}
        for path in sorted(BRIDGE_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            if "SYSTEM_PROMPTS_FILE" in source:
                offenders[path.name] = "SYSTEM_PROMPTS_FILE"
        self.assertEqual(offenders, {})

    def test_late_environment_loader_is_deleted(self):
        common_source = (BRIDGE_DIR / "common.py").read_text(
            encoding="utf-8"
        )
        main_source = (BRIDGE_DIR / "main.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("def load_env_file(", common_source)
        self.assertNotIn("load_env_file", main_source)

    def test_common_has_no_import_time_process_resource_construction(self):
        source = (BRIDGE_DIR / "common.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        top_level_calls = []
        executor_assignments = []
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                top_level_calls.append(node.value)
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                executor_assignments.append(node.value)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Call):
                executor_assignments.append(node.value)

        def dotted_name(node):
            if isinstance(node, ast.Name):
                return node.id
            if isinstance(node, ast.Attribute):
                prefix = dotted_name(node.value)
                return f"{prefix}.{node.attr}" if prefix else node.attr
            return ""

        self.assertFalse(
            any(
                dotted_name(call.func) == "logging.basicConfig"
                or dotted_name(call.func).endswith(".mkdir")
                for call in top_level_calls
            )
        )
        self.assertFalse(
            any(
                dotted_name(call.func).endswith(".ThreadPoolExecutor")
                for call in executor_assignments
            )
        )

    def test_launcher_has_no_local_environment_parser(self):
        source = (REPO_ROOT / "sillytavern_telegram_bridge.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("def bootstrap_env(", source)
        self.assertIn(
            "from bridge.environment import bootstrap_environment",
            source,
        )

    def test_env_example_has_no_single_file_system_prompt_fallback(self):
        source = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertNotIn("SILLYTAVERN_SYSTEM_PROMPTS_FILE", source)

    def test_no_python_source_imports_runtime_compatibility(self):
        offenders = []
        for root in (BRIDGE_DIR, TESTS_DIR):
            for path in sorted(root.glob("*.py")):
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source)
                imports_runtime = any(
                    (
                        isinstance(node, ast.Import)
                        and any(alias.name == "bridge.runtime" for alias in node.names)
                    )
                    or (
                        isinstance(node, ast.ImportFrom)
                        and node.module in {"bridge.runtime", "runtime_test_facade"}
                    )
                    for node in ast.walk(tree)
                )
                if imports_runtime:
                    offenders.append(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()

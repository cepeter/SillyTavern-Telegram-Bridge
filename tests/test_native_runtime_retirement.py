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

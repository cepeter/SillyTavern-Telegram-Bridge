"""PR 57 native runtime compatibility retirement guards."""

from __future__ import annotations

from pathlib import Path
import ast
import unittest


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

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

    def test_dependency_patch_helper_rejects_runtime_facade(self):
        from dependency_patch import dependency_module

        with self.assertRaises(ValueError):
            dependency_module("bridge.runtime")

    def test_dependency_patch_updates_matching_direct_imports_only(self):
        import bridge.generation as generation
        import bridge.network_security as network_security
        from dependency_patch import dependency_module

        proxy = dependency_module("bridge.generation")
        original = generation.strict_urlopen
        marker = object()
        unrelated = object()
        generation.unrelated_patch_probe = unrelated
        try:
            proxy.strict_urlopen = marker
            self.assertIs(generation.strict_urlopen, marker)
            self.assertIs(network_security.strict_urlopen, marker)
            self.assertIs(generation.unrelated_patch_probe, unrelated)
        finally:
            proxy.strict_urlopen = original
            del generation.unrelated_patch_probe

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

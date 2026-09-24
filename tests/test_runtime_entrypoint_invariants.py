"""Runtime entrypoint and composition invariants."""

from __future__ import annotations

import ast
import builtins
from pathlib import Path
import subprocess
import symtable
import sys
import unittest

REPO_ROOT = Path(__file__).parents[1]
BRIDGE_DIR = REPO_ROOT / "bridge"


def _bound_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()

    def bind(node):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for child in node.elts:
                bind(child)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                bind(target)
    return names


def _defined_names(source: str) -> set[str]:
    tree = ast.parse(source)
    result: set[str] = set()

    def bind(node):
        if isinstance(node, ast.Name):
            result.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for child in node.elts:
                bind(child)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            result.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                bind(target)
    return result


def _referenced_globals(source: str, filename: str) -> set[str]:
    table = symtable.symtable(source, filename, "exec")
    result: set[str] = set()

    def walk(node):
        for symbol in node.get_symbols():
            if symbol.is_referenced() and symbol.is_global():
                result.add(symbol.get_name())
        for child in node.get_children():
            walk(child)

    walk(table)
    return result


def _owner_index() -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for path in sorted(BRIDGE_DIR.glob("*.py")):
        if path.name in {"main.py", "runtime.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        for name in _defined_names(source):
            owners.setdefault(name, []).append(path.name)
    return owners


class RuntimeEntrypointInvariantTests(unittest.TestCase):
    def _run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_main_import_is_ordinary_and_does_not_import_runtime_or_loader(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.main\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.runtime_loader' not in sys.modules\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_entrypoint_bootstraps_environment_before_importing_main(self):
        source = (REPO_ROOT / "sillytavern_telegram_bridge.py").read_text(
            encoding="utf-8"
        )
        bootstrap_import = source.index(
            "from bridge.environment import bootstrap_environment"
        )
        bootstrap_call = source.index("bootstrap_environment()")
        main_import = source.index("from bridge.main import main")

        self.assertLess(bootstrap_import, bootstrap_call)
        self.assertLess(bootstrap_call, main_import)
        self.assertNotIn("def bootstrap_env(", source)

    def test_entrypoint_imports_main_directly(self):
        source = (REPO_ROOT / "sillytavern_telegram_bridge.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("from bridge.main import main", source)
        self.assertNotIn("from bridge.runtime import main", source)


    def test_no_production_exec_calls_remain(self):
        offenders = []
        for path in sorted(BRIDGE_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "exec"
                ):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_main_has_no_implicit_shared_runtime_globals(self):
        path = BRIDGE_DIR / "main.py"
        source = path.read_text(encoding="utf-8")
        bound = _bound_names(source)
        unresolved = sorted(
            _referenced_globals(source, "main.py")
            - bound
            - set(dir(builtins))
            - {"__file__", "__name__", "__package__"}
        )
        owners = _owner_index()
        detail = [
            name + (
                " <- " + ",".join(owners[name])
                if owners.get(name)
                else ""
            )
            for name in unresolved
        ]
        self.assertEqual(detail, [], "\n".join(detail))

    def test_main_has_no_transitional_dependency_composition(self):
        source = (BRIDGE_DIR / "main.py").read_text(encoding="utf-8")
        self.assertFalse((BRIDGE_DIR / "ordinary_dependencies.py").exists())
        self.assertNotIn("ordinary_dependencies", source)
        self.assertNotIn("complete_application_dependencies", source)


if __name__ == "__main__":
    unittest.main()

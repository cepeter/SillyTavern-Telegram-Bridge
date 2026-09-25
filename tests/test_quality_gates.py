"""CI must check the whole source tree, not silently selected lint leaves."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_ci_lints_complete_tree():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "python -m ruff check ." in workflow
    assert "xargs python -m ruff check" not in workflow


def test_ci_checks_formatting():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "python -m ruff format --check ." in workflow


def test_bug_and_security_rules_enabled():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert {"E4", "E5", "E7", "E9", "F", "I", "B", "S", "RUF"} <= set(config["tool"]["ruff"]["lint"]["select"])


def test_type_gate_includes_security_modules_separately_from_layer_rule():
    import importlib.util

    path = ROOT / "tools/static_analysis.py"
    spec = importlib.util.spec_from_file_location("quality_policy", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert {"bridge/network_security.py", "bridge/callback_tokens.py"} <= set(module.TYPE_TARGETS)
    assert "bridge/network_security.py" not in module.STATIC_TARGETS


def test_quality_manifest_cli_and_graph_cli_work():
    import subprocess
    import sys

    command = [sys.executable, str(ROOT / "tools/static_analysis.py")]
    typed = subprocess.run([*command, "--print-type-targets"], capture_output=True, text=True)
    assert typed.returncode == 0, typed.stderr
    assert "bridge/network_security.py" in typed.stdout.splitlines()
    graph = subprocess.run(command, capture_output=True, text=True)
    assert graph.returncode == 0, graph.stderr
    assert "cyclic_modules=0" in graph.stdout

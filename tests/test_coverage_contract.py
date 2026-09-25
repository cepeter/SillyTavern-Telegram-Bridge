"""CI must measure the whole application package, not a handpicked subset."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_coverage_is_branch_enabled_and_has_no_production_omissions():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["coverage"]
    assert config["run"]["source"] == ["bridge"]
    assert config["run"]["branch"] is True
    assert "subprocess" in config["run"]["patch"]
    assert not config["run"].get("omit")
    assert config["report"]["fail_under"] > 0


def test_ci_measures_and_publishes_coverage():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "--cov" in workflow
    assert "--cov-report=json:coverage.json" in workflow
    assert "--cov-report=xml:coverage.xml" in workflow
    assert "actions/upload-artifact@" in workflow
    assert "coverage.json" in workflow and "coverage.xml" in workflow
    dependencies = (ROOT / "requirements-dev.txt").read_text()
    assert "pytest-cov==" in dependencies and "coverage==" in dependencies


def test_security_contract_cases_are_retained():
    expected = {
        "test_network_security.py": {"test_external_requires_explicit_allowlist"},
        "test_callback_token_security.py": {"test_equal_inputs_get_different_opaque_tokens"},
        "test_environment_security.py": {"test_environment_parsing_is_atomic", "test_environment_rejects_wrong_owner"},
        "test_self_update_security.py": {
            "test_unsigned_release_is_refused",
            "test_wrong_signer_refused",
            "test_verified_release_updates_exact_commit_and_schedules_restart",
            "test_failed_activation_and_restore_retains_recoverable_old_mirror",
        },
        "test_update_confirmation.py": {"test_new_release_requires_new_confirmation"},
    }
    for filename, required in expected.items():
        path = ROOT / "tests" / filename
        tree = ast.parse(path.read_text())
        tests = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        assert required <= tests, (filename, required - tests)


def test_coverage_floor_matches_measured_baseline():
    import json
    import math

    baseline = json.loads((ROOT / "tools/coverage_baseline.json").read_text())
    totals = baseline["totals"]
    measured = (
        100
        * (totals["covered_lines"] + totals["covered_branches"])
        / (totals["num_statements"] + totals["num_branches"])
    )
    floor = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["coverage"]["report"]["fail_under"]
    assert math.isclose(measured, baseline["combined_percent"])
    assert floor == baseline["minimum_combined_percent"]
    assert floor >= math.floor(measured)
    assert 0 < floor <= measured

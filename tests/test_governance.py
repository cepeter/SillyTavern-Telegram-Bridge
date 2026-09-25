"""Maintenance instructions and automation must match the executable repository."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_security_policy_has_real_disclosure_path_and_no_invented_sla():
    text = (ROOT / "SECURITY.md").read_text()
    assert "Security" in text and "private" in text.casefold()
    assert "main" in text and "preproduction" in text.casefold()
    assert "response-time" in text and "no guaranteed" in text.casefold()
    assert "security@" not in text
    assert "do not include" in text.casefold()


def test_contributor_commands_match_actual_tools():
    text = (ROOT / "CONTRIBUTING.md").read_text()
    for command in (
        "python -m ruff check .",
        "python -m ruff format --check .",
        "python tools/static_analysis.py",
        "python tools/check_dependency_lock.py",
        "python -m pytest",
        "--cov-report=json:coverage.json",
        "--require-hashes",
        "uv pip compile requirements.txt",
        "--generate-hashes",
        "requirements.lock",
        "git switch -c",
    ):
        assert command in text, command
    assert "custom requirements.lock" in text
    assert "do not assume" in text.casefold()
    assert "codeql" in text.casefold()


def test_dependabot_covers_pip_and_actions_without_unbounded_updates():
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    assert config["version"] == 2
    updates = {item["package-ecosystem"]: item for item in config["updates"]}
    assert set(updates) == {"pip", "github-actions"}
    for item in updates.values():
        assert item["directory"] == "/"
        assert item["schedule"]["interval"] == "weekly"
        assert 1 <= item["open-pull-requests-limit"] <= 3
        assert item.get("insecure-external-code-execution") != "allow"
    assert not list((ROOT / ".github/workflows").glob("*codeql*"))


def test_ci_checks_dependency_manifest_and_lock_compatibility():
    assert "python tools/check_dependency_lock.py" in (ROOT / ".github/workflows/ci.yml").read_text()
    assert "packaging==" in (ROOT / "requirements-dev.txt").read_text()


def test_public_examples_match_original_reviewed_hashes():
    baseline = json.loads((ROOT / "tools/public_examples.json").read_text())
    paths = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "config/system_prompts.example").glob("*")
        if path.is_file()
    }
    assert paths == set(baseline["sha256"])
    for name, expected in baseline["sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name

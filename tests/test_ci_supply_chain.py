"""Declared executable CI dependencies must be immutable and hash-checked."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_every_external_action_uses_a_full_commit_sha():
    for path in (ROOT / ".github/workflows").glob("*.y*ml"):
        workflow = yaml.safe_load(path.read_text())
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                if uses and not uses.startswith("./"):
                    assert re.fullmatch(r"[\w-]+/[\w./-]+@[0-9a-f]{40}", uses), uses


def test_ci_python_installs_use_complete_hash_pinned_locks():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    installs = []
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            run = step.get("run", "")
            if "pip install" in run:
                installs.append(run)
                assert "--require-hashes" in run, run
                assert "-r requirements.lock" in run or "-r requirements-dev.lock" in run, run
    assert len(installs) >= 3
    assert any("requirements-dev.lock" in command for command in installs)


def test_both_full_dependency_sets_are_audited_without_re_resolving():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    audit = "\n".join(step.get("run", "") for step in workflow["jobs"]["dependency-audit"]["steps"])
    assert "--no-deps" not in audit
    assert "pip_audit --require-hashes --disable-pip -r requirements.lock" in audit
    assert "pip_audit --require-hashes --disable-pip -r requirements-dev.lock" in audit
    assert "pip-audit==" in (ROOT / "requirements-dev.txt").read_text()


def test_uv_download_is_versioned_consistently_with_development_input():
    manifest = (ROOT / "requirements-dev.txt").read_text()
    pin = re.search(r"^uv==([0-9.]+)$", manifest, re.MULTILINE)
    assert pin, "compiler/bootstrap version needs an explicit reviewed pin"
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if step.get("uses", "").startswith("astral-sh/setup-uv@"):
                assert step["with"]["version"] == pin[1]

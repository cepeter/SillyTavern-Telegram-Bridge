"""Offline dependency guards reject stale direct pins and malformed integrity metadata."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
HASH = "0" * 64


def guard():
    path = ROOT / "tools/check_dependency_lock.py"
    assert path.is_file(), "dependency lock guard missing"
    spec = importlib.util.spec_from_file_location("dependency_guard", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_dependency_files_pass_the_offline_guard():
    result = subprocess.run(
        [sys.executable, "tools/check_dependency_lock.py"], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dependency-lock=ok" in result.stdout


def test_valid_flat_lock_and_development_pin():
    assert guard().validate("demo>=1,<2\n", f"demo==1.4 --hash=sha256:{HASH}\n", "pytest==9.1.1\n") == ()


@pytest.mark.parametrize(
    "locked, message",
    [
        ("demo==1.4", "hash"),
        ("demo>=1.4 --hash=sha256:" + HASH, "exact"),
        ("demo==2.0 --hash=sha256:" + HASH, "satisfy"),
        ("other==1.0 --hash=sha256:" + HASH, "missing"),
        ("demo==1.4 --hash=md5:" + HASH, "hash"),
        ("demo==1.4 --hash=sha256:short", "hash"),
        ("-r another.lock", "unsupported"),
        ("demo @ https://example.invalid/demo.whl --hash=sha256:" + HASH, "URL"),
        ("demo==1.4 --hash=sha256:" + HASH + "\ndemo==1.5 --hash=sha256:" + HASH, "duplicate"),
    ],
)
def test_invalid_lock_is_actionable(locked, message):
    errors = guard().validate("demo>=1,<2\n", locked + "\n", "pytest==9.1.1\n")
    assert errors and any(message.casefold() in error.casefold() for error in errors), errors


def test_development_requirements_must_be_exactly_pinned():
    errors = guard().validate("demo>=1,<2", f"demo==1.4 --hash=sha256:{HASH}", "pytest>=9")
    assert any("development" in error and "exact" in error for error in errors)


def test_inactive_marker_does_not_require_an_active_pin():
    assert guard().validate('demo>=1; python_version < "2"', "", "pytest==9.1.1") == ()


def test_backslash_hash_lines_and_comments_are_supported():
    locked = "demo==1.4 \\\n    --hash=sha256:" + HASH + "\n    # via requirements.txt\n"
    assert guard().validate("demo>=1", locked, "pytest==9.1.1") == ()


def test_error_does_not_echo_url_credentials():
    errors = guard().validate("demo @ https://user:private-secret@example.invalid/demo.whl", "", "pytest==9.1.1")
    assert errors
    assert "private-secret" not in repr(errors)


def test_guard_typechecks_with_the_installed_development_dependencies():
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--no-incremental", "tools/check_dependency_lock.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

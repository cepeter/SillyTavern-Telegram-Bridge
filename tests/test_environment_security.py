"""Secret file validation is performed on the opened file, before any mutation."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge.environment import load_environment_file


def private_env(tmp_path: Path, content: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o620, 0o601])
def test_environment_rejects_insecure_permissions(tmp_path, mode):
    path = private_env(tmp_path, "TOKEN=secret\n")
    path.chmod(mode)
    target = {"EXISTING": "kept"}
    with pytest.raises(RuntimeError, match="permission"):
        load_environment_file(path, target)
    assert target == {"EXISTING": "kept"}


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership")
def test_environment_rejects_wrong_owner(tmp_path, monkeypatch):
    path = private_env(tmp_path, "TOKEN=secret\n")
    fstat = os.fstat

    def other_owner(fd):
        info = fstat(fd)
        return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size, st_uid=os.geteuid() + 1)

    monkeypatch.setattr(os, "fstat", other_owner)
    with pytest.raises(RuntimeError, match="owner"):
        load_environment_file(path, {})


@pytest.mark.skipif(os.name != "posix", reason="O_NOFOLLOW")
def test_environment_rejects_symlinks(tmp_path):
    target = private_env(tmp_path, "TOKEN=secret\n")
    link = tmp_path / "linked.env"
    link.symlink_to(target)
    with pytest.raises(RuntimeError, match=r"regular file|symlink"):
        load_environment_file(link, {})


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX fifo")
def test_environment_rejects_fifo_without_blocking(tmp_path):
    path = tmp_path / "pipe.env"
    os.mkfifo(path, 0o600)
    with pytest.raises(RuntimeError, match="regular file"):
        load_environment_file(path, {})


def test_environment_parsing_is_atomic(tmp_path):
    path = private_env(tmp_path, "FIRST=secret\nBROKEN\n")
    target = {"EXISTING": "kept"}
    with pytest.raises(RuntimeError, match="assignment"):
        load_environment_file(path, target)
    assert target == {"EXISTING": "kept"}


def test_environment_rejects_nul_without_partial_changes(tmp_path):
    path = private_env(tmp_path, "FIRST=secret\nSECOND=a\x00b\n")
    target = {}
    with pytest.raises(RuntimeError):
        load_environment_file(path, target)
    assert target == {}


def test_environment_reads_validated_descriptor(tmp_path, monkeypatch):
    path = private_env(tmp_path, "FIRST=value\n")

    def forbidden_reopen(*_args, **_kwargs):
        raise AssertionError("must read the validated descriptor")

    monkeypatch.setattr(Path, "read_text", forbidden_reopen)
    target = {}
    load_environment_file(path, target)
    assert target == {"FIRST": "value"}


def test_environment_rejects_oversized_file(tmp_path):
    path = private_env(tmp_path, "#" + ("x" * (1024 * 1024)))
    with pytest.raises(RuntimeError, match=r"size|large|limit"):
        load_environment_file(path, {})

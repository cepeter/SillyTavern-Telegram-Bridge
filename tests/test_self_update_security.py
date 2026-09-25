"""Exercise update trust and filesystem transitions with real local Git objects."""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def engine():
    return importlib.import_module("bridge.self_update")


def executable(name):
    result = shutil.which(name)
    assert result, f"required integration-test executable missing: {name}"
    return result


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        [executable("git"), "-c", "commit.gpgSign=false", "-c", f"core.hooksPath={os.devnull}", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def release_tree(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    git(remote, "init", "-b", "main")
    git(remote, "config", "user.email", "audit@example.invalid")
    git(remote, "config", "user.name", "Audit fixture")
    (remote / "bridge").mkdir()
    (remote / "bridge" / "__init__.py").write_text("")
    (remote / "bridge" / "feature.py").write_text("VALUE = 1\n")
    (remote / "sillytavern_telegram_bridge.py").write_text('print("fixture")\n')
    (remote / "CHANGELOG.md").write_text("## [0.0.1]\n")
    (remote / "requirements.lock").write_text("# no external packages in fixture\n")
    git(remote, "add", ".")
    git(remote, "commit", "-m", "baseline")
    source = tmp_path / "source"
    subprocess.run([executable("git"), "clone", str(remote), str(source)], check=True, capture_output=True)
    source.chmod(0o700)
    old = git(source, "rev-parse", "HEAD")
    (remote / "bridge" / "feature.py").write_text("VALUE = 2\n")
    (remote / "CHANGELOG.md").write_text("## [0.0.2]\n")
    git(remote, "add", ".")
    git(remote, "commit", "-m", "release")
    new = git(remote, "rev-parse", "HEAD")
    key = tmp_path / "signing-key"
    subprocess.run([executable("ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    signers = tmp_path / "allowed_signers"
    signers.write_text("maintainer " + key.with_suffix(".pub").read_text())
    signers.chmod(0o600)
    git(remote, "-c", "gpg.format=ssh", "-c", f"user.signingkey={key}", "tag", "-s", "v0.0.2", "-m", "release")
    live = tmp_path / "state" / "live"
    live.mkdir(parents=True)
    live.parent.chmod(0o700)
    live.chmod(0o700)
    return SimpleNamespace(remote=remote, source=source, live=live, signers=signers, old=old, new=new, key=key)


def make_plan(engine, data):
    return engine.UpdatePlan(
        source=data.source,
        live=data.live,
        trusted_signers=data.signers,
        release_version="0.0.2",
        remote_url=str(data.remote),
    )


def fake_supervisor(monkeypatch, engine):
    calls = []
    original = engine._run

    def command(argv, **kwargs):
        executable_name = Path(argv[0]).name
        if executable_name == "systemctl":
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "loaded\n", "")
        if executable_name == "systemd-run":
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return original(argv, **kwargs)

    monkeypatch.setattr(engine, "_run", command)
    return calls


def test_preflight_refuses_dirty_source_without_writes(engine, release_tree):
    data = release_tree
    (data.source / "bridge" / "feature.py").write_text("private unsaved edit\n")
    result = engine.apply_update(make_plan(engine, data))
    assert result.status is engine.UpdateStatus.REFUSED
    assert git(data.source, "rev-parse", "HEAD") == data.old
    assert (data.source / "bridge" / "feature.py").read_text() == "private unsaved edit\n"
    assert not list(data.live.iterdir())


@pytest.mark.parametrize("target", ["home", "root", "source", "source-child", "ancestor"])
def test_unsafe_update_targets_refused(engine, release_tree, target):
    data = release_tree
    choices = {
        "home": Path.home(),
        "root": Path("/"),
        "source": data.source,
        "source-child": data.source / "live",
        "ancestor": data.source.parent,
    }
    plan = engine.UpdatePlan(
        source=data.source,
        live=choices[target],
        trusted_signers=data.signers,
        release_version="0.0.2",
        remote_url=str(data.remote),
    )
    result = engine.apply_update(plan)
    assert result.status is engine.UpdateStatus.REFUSED
    assert git(data.source, "rev-parse", "HEAD") == data.old


def test_unmanaged_nonempty_target_is_preserved(engine, release_tree):
    data = release_tree
    (data.live / "important.txt").write_text("keep")
    result = engine.apply_update(make_plan(engine, data))
    assert result.status is engine.UpdateStatus.REFUSED
    assert (data.live / "important.txt").read_text() == "keep"
    assert git(data.source, "rev-parse", "HEAD") == data.old


def test_symlink_target_refused(engine, release_tree):
    data = release_tree
    actual = data.live.parent / "actual"
    actual.mkdir()
    data.live.rmdir()
    data.live.symlink_to(actual, target_is_directory=True)
    result = engine.apply_update(make_plan(engine, data))
    assert result.status is engine.UpdateStatus.REFUSED
    assert not list(actual.iterdir())


def test_trust_anchor_must_be_external(engine, release_tree):
    data = release_tree
    anchor = data.source / "allowed_signers"
    shutil.copy2(data.signers, anchor)
    git(data.source, "add", "allowed_signers")
    # Validate directly: a trust file from an updated tree must not authorize its own replacement.
    plan = engine.UpdatePlan(
        source=data.source, live=data.live, trusted_signers=anchor, release_version="0.0.2", remote_url=str(data.remote)
    )
    with pytest.raises(engine.UpdateRefused):
        engine.validate_plan(plan)


def test_unsigned_release_is_refused(engine, release_tree, monkeypatch):
    data = release_tree
    git(data.remote, "tag", "-d", "v0.0.2")
    git(data.remote, "tag", "v0.0.2")
    fake_supervisor(monkeypatch, engine)
    result = engine.apply_update(make_plan(engine, data))
    assert result.status is engine.UpdateStatus.REFUSED
    assert result.code == "signature"
    assert git(data.source, "rev-parse", "HEAD") == data.old
    assert not list(data.live.iterdir())


def test_wrong_signer_refused(engine, release_tree, monkeypatch):
    data = release_tree
    other = data.signers.parent / "other-key"
    subprocess.run([executable("ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-f", str(other)], check=True)
    data.signers.write_text("other " + other.with_suffix(".pub").read_text())
    fake_supervisor(monkeypatch, engine)
    result = engine.apply_update(make_plan(engine, data))
    assert result.status is engine.UpdateStatus.REFUSED
    assert result.code == "signature"
    assert git(data.source, "rev-parse", "HEAD") == data.old


def test_verified_release_updates_exact_commit_and_schedules_restart(engine, release_tree, monkeypatch):
    data = release_tree
    calls = []
    original = engine._run

    def command(argv, **kwargs):
        executable_name = Path(argv[0]).name
        if executable_name == "systemctl":
            calls.append(list(argv))
            if "show" in argv:
                return subprocess.CompletedProcess(argv, 0, "loaded\n", "")
            pytest.fail("bridge updater must not restart its own unit from inside the service cgroup")
        if executable_name == "systemd-run":
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "", "")
        return original(argv, **kwargs)

    monkeypatch.setattr(engine, "_run", command)
    result = engine.apply_update(make_plan(engine, data))
    assert result.status is engine.UpdateStatus.RESTART_SCHEDULED
    assert result.commit == data.new
    assert git(data.source, "rev-parse", "HEAD") == data.new
    assert (data.live / "bridge" / "feature.py").read_text() == "VALUE = 2\n"
    assert (data.live / ".bridge-deployment.json").is_file()
    scheduled = [call for call in calls if Path(call[0]).name == "systemd-run"]
    assert len(scheduled) == 1
    command = scheduled[0]
    assert "--user" in command
    assert "--collect" in command
    assert "--no-block" in command
    assert "--on-active=5s" in command
    restart_index = command.index("restart")
    assert Path(command[restart_index - 2]).name == "systemctl"
    assert command[restart_index - 1] == "--user"
    assert command[restart_index + 1] == make_plan(engine, data).unit


def test_prepare_failure_preserves_source_and_live(engine, release_tree, monkeypatch):
    data = release_tree
    fake_supervisor(monkeypatch, engine)

    def failed_copy(*_args, **_kwargs):
        raise OSError("private-path-or-secret-should-not-leak")

    monkeypatch.setattr(engine, "_prepare_payload", failed_copy)
    result = engine.apply_update(make_plan(engine, data))
    assert result.status is engine.UpdateStatus.FAILED
    assert "private-path-or-secret" not in repr(result)
    assert git(data.source, "rev-parse", "HEAD") == data.old
    assert not list(data.live.iterdir())


def test_runtime_dependency_change_requires_manual_install(engine, release_tree, monkeypatch):
    data = release_tree
    git(data.remote, "tag", "-d", "v0.0.2")
    (data.remote / "requirements.lock").write_text("changed dependency lock\n")
    git(data.remote, "add", ".")
    git(data.remote, "commit", "-m", "dependency change")
    git(
        data.remote, "-c", "gpg.format=ssh", "-c", f"user.signingkey={data.key}", "tag", "-s", "v0.0.2", "-m", "release"
    )
    fake_supervisor(monkeypatch, engine)
    result = engine.apply_update(make_plan(engine, data))
    assert result.status is engine.UpdateStatus.REFUSED
    assert result.code == "dependencies"
    assert git(data.source, "rev-parse", "HEAD") == data.old


def test_restart_failure_is_explicit_not_false_success(engine, release_tree, monkeypatch):
    data = release_tree
    original = engine._run

    def command(argv, **kwargs):
        executable_name = Path(argv[0]).name
        if executable_name == "systemctl":
            if "restart" in argv:
                pytest.fail("restart must not run directly inside the bridge service cgroup")
            return subprocess.CompletedProcess(argv, 0, "loaded\n", "")
        if executable_name == "systemd-run":
            raise subprocess.CalledProcessError(1, argv, stderr="private service internals")
        return original(argv, **kwargs)

    monkeypatch.setattr(engine, "_run", command)
    result = engine.apply_update(make_plan(engine, data))
    assert result.status is engine.UpdateStatus.RESTART_REQUIRED
    assert result.commit == data.new
    assert "private service" not in repr(result)
    assert git(data.source, "rev-parse", "HEAD") == data.new


def managed_mirror(engine, data):
    import json

    (data.live / engine.MARKER).write_text(json.dumps({"application": engine.APPLICATION, "format": 1}))
    (data.live / "bridge").mkdir()
    (data.live / "bridge" / "obsolete.py").write_text("old deployment must survive failed activation\n")


def test_failed_activation_and_restore_retains_recoverable_old_mirror(engine, release_tree, monkeypatch):
    data = release_tree
    managed_mirror(engine, data)
    fake_supervisor(monkeypatch, engine)
    original = Path.rename

    def rename(path, target):
        if Path(target) == data.live:
            raise OSError("simulated activation and rollback failures")
        return original(path, target)

    monkeypatch.setattr(Path, "rename", rename)
    outcome = engine.apply_update(make_plan(engine, data))
    assert outcome.status is engine.UpdateStatus.FAILED
    copies = list(data.live.parent.rglob("obsolete.py"))
    assert copies, "old mirror must not be deleted by temporary-directory cleanup after failed rollback"
    assert copies[0].read_text() == "old deployment must survive failed activation\n"


def test_successful_activation_does_not_keep_obsolete_python_modules(engine, release_tree, monkeypatch):
    data = release_tree
    managed_mirror(engine, data)
    fake_supervisor(monkeypatch, engine)
    outcome = engine.apply_update(make_plan(engine, data))
    assert outcome.status is engine.UpdateStatus.RESTART_SCHEDULED
    assert not (data.live / "bridge" / "obsolete.py").exists()


def test_signed_release_symlink_is_rejected(engine, release_tree, monkeypatch):
    data = release_tree
    git(data.remote, "tag", "-d", "v0.0.2")
    (data.remote / "bridge" / "escape.py").symlink_to("/etc/passwd")
    git(data.remote, "add", ".")
    git(data.remote, "commit", "-m", "unsafe symlink fixture")
    git(
        data.remote, "-c", "gpg.format=ssh", "-c", f"user.signingkey={data.key}", "tag", "-s", "v0.0.2", "-m", "release"
    )
    fake_supervisor(monkeypatch, engine)
    outcome = engine.apply_update(make_plan(engine, data))
    assert outcome.code == "archive"
    assert git(data.source, "rev-parse", "HEAD") == data.old
    assert not list(data.live.iterdir())


def test_source_edit_during_preparation_is_preserved(engine, release_tree, monkeypatch):
    data = release_tree
    fake_supervisor(monkeypatch, engine)
    prepare = engine._prepare_payload

    def edited(*args):
        prepare(*args)
        (data.source / "bridge" / "feature.py").write_text("concurrent user edit\n")

    monkeypatch.setattr(engine, "_prepare_payload", edited)
    outcome = engine.apply_update(make_plan(engine, data))
    assert outcome.code == "dirty"
    assert git(data.source, "rev-parse", "HEAD") == data.old
    assert (data.source / "bridge" / "feature.py").read_text() == "concurrent user edit\n"


def test_private_key_is_never_copied_as_public_trust_policy(engine, release_tree, monkeypatch):
    data = release_tree
    data.signers.write_bytes(data.key.read_bytes())
    fake_supervisor(monkeypatch, engine)

    def no_fetch_or_copy(*_args, **_kwargs):
        pytest.fail("private key must be rejected before fetching or creating a signer snapshot")

    monkeypatch.setattr(engine, "_verified_release", no_fetch_or_copy)
    outcome = engine.apply_update(make_plan(engine, data))
    assert outcome.status is engine.UpdateStatus.REFUSED
    assert outcome.code == "trust"


@pytest.mark.parametrize(
    "line",
    [
        "principal secret not-public",
        "principal ssh-ed25519 invalid-base64",
        'principal namespaces="file" ssh-ed25519 AAAA',
    ],
)
def test_malformed_public_signer_policy_is_refused_before_network(engine, release_tree, monkeypatch, line):
    data = release_tree
    data.signers.write_text(line + "\n")
    fake_supervisor(monkeypatch, engine)
    monkeypatch.setattr(engine, "_verified_release", lambda *_a, **_k: pytest.fail("policy must be validated first"))
    result = engine.apply_update(make_plan(engine, data))
    assert result.code == "trust"

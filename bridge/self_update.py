"""Verified release preparation and guarded activation, independent of Telegram.

Preparation never changes the checkout or live files. Activation fast-forwards
only an unchanged clean main checkout, then replaces the dedicated managed live
mirror. A supervisor failure is reported as restart-required, never as success.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

APPLICATION = "cepeter/SillyTavern-Telegram-Bridge"
CANONICAL_GIT_URL = f"https://github.com/{APPLICATION}.git"
MARKER = ".bridge-deployment.json"
_VERSION = re.compile(r"[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}\Z")
_UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,120}\.service\Z")
_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024


class UpdateStatus(str, Enum):
    ALREADY_LATEST = "already_latest"
    REFUSED = "refused"
    FAILED = "failed"
    RESTART_SCHEDULED = "restart_scheduled"
    RESTART_REQUIRED = "restart_required"


@dataclass(frozen=True)
class UpdateOutcome:
    status: UpdateStatus
    version: str = ""
    commit: str = ""
    code: str = ""
    source_changed: bool = False
    live_changed: bool = False


@dataclass(frozen=True)
class UpdatePlan:
    source: Path
    live: Path
    trusted_signers: Path | None
    release_version: str
    unit: str = "sillytavern-telegram.service"
    remote_url: str = CANONICAL_GIT_URL


class UpdateRefused(RuntimeError):
    """A stable non-secret refusal code, not a subprocess diagnostic."""


def version_tuple(value: str) -> tuple[int, ...]:
    if not _VERSION.fullmatch(value):
        raise UpdateRefused("version")
    return tuple(int(part) for part in value.split("."))


def _run(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), check=True, text=True, capture_output=True, timeout=120, **kwargs)  # noqa: S603 -- fixed argv, explicit executable, no shell


def _executable(name: str) -> str:
    result = shutil.which(name)
    if not result:
        raise UpdateRefused("tools")
    return result


def _git(argv: Sequence[str], cwd: Path, *, executable: str, extra: Sequence[str] = ()) -> str:
    # Do not inherit proxy, helper, credential, include or signing configuration
    # from unrelated shell tools. The local repository is operator controlled.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "SystemRoot") if key in os.environ}
    env.update(
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="https:file"
    )
    command = [
        executable,
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "credential.helper=",
        "-c",
        "http.proxy=",
        "-c",
        "core.fsmonitor=false",
        *extra,
        "-C",
        str(cwd),
        *argv,
    ]
    return _run(command, env=env).stdout.strip()


def _private_parent(path: Path) -> None:
    if not path.is_dir() or path.is_symlink():
        raise UpdateRefused("target")
    info = path.stat()
    if os.name == "posix" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022):
        raise UpdateRefused("target")


def _validate_live_contents(live: Path) -> None:
    if not live.exists():
        return
    if not live.is_dir() or live.is_symlink():
        raise UpdateRefused("target")
    if not any(live.iterdir()):
        return
    marker = live / MARKER
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 4096:
        raise UpdateRefused("unmanaged_target")
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UpdateRefused("unmanaged_target") from None
    if not isinstance(metadata, dict) or metadata.get("application") != APPLICATION or metadata.get("format") != 1:
        raise UpdateRefused("unmanaged_target")
    # Even a marked tree cannot contain symlinks/special files that would reach
    # outside this managed installation during activation or later cleanup.
    for path in live.rglob("*"):
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise UpdateRefused("target")


def validate_plan(plan: UpdatePlan) -> None:
    version_tuple(plan.release_version)
    if os.name != "posix" or not _UNIT.fullmatch(plan.unit):
        raise UpdateRefused("supervisor")
    source, live = plan.source.absolute(), plan.live.absolute()
    if source != source.resolve() or live != live.resolve():
        raise UpdateRefused("target")
    forbidden = {Path("/"), Path.home().resolve()}
    if source in forbidden or live in forbidden or live == source:
        raise UpdateRefused("target")
    if source.is_relative_to(live) or live.is_relative_to(source):
        raise UpdateRefused("target")
    if not source.is_dir() or not (source / ".git").exists():
        raise UpdateRefused("source")
    for name in ("CHANGELOG.md", "requirements.lock", "sillytavern_telegram_bridge.py"):
        file = source / name
        if file.is_symlink() or not file.is_file():
            raise UpdateRefused("source")
    if plan.trusted_signers is None:
        raise UpdateRefused("trust")
    trust = plan.trusted_signers.absolute()
    if trust != trust.resolve() or trust.is_relative_to(source) or trust.is_relative_to(live):
        raise UpdateRefused("trust")
    _private_parent(source)
    _private_parent(live.parent)
    _validate_live_contents(live)


def _trusted_signers(path: Path) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        raise UpdateRefused("trust") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise UpdateRefused("trust")
        if not 0 < info.st_size <= 65536:
            raise UpdateRefused("trust")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(65537)
        if len(raw) > 65536 or b"\x00" in raw:
            raise UpdateRefused("trust")
        return raw
    finally:
        os.close(fd)


@contextmanager
def _update_lock(parent: Path) -> Iterator[None]:
    import fcntl

    fd = os.open(parent / ".bridge-update.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise UpdateRefused("target")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpdateRefused("busy") from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _verified_release(plan: UpdatePlan, workspace: Path, tools: Mapping[str, str], trust: bytes) -> tuple[Path, str]:
    bare = workspace / "objects.git"
    _run([tools["git"], "init", "--bare", str(bare)])
    _git(
        [
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            plan.remote_url,
            f"refs/tags/v{plan.release_version}:refs/tags/candidate",
        ],
        bare,
        executable=tools["git"],
    )
    tag = _git(["rev-parse", "refs/tags/candidate"], bare, executable=tools["git"])
    if _git(["cat-file", "-t", tag], bare, executable=tools["git"]) != "tag":
        raise UpdateRefused("signature")
    signed_text = _git(["cat-file", "tag", tag], bare, executable=tools["git"])
    headers = signed_text.split("\n\n", 1)[0].splitlines()
    if f"tag v{plan.release_version}" not in headers or "type commit" not in headers:
        raise UpdateRefused("signature")
    signers = workspace / "trusted-signers"
    signers.write_bytes(trust)
    signers.chmod(0o600)
    try:
        _git(
            ["verify-tag", tag],
            bare,
            executable=tools["git"],
            extra=[
                "-c",
                "gpg.format=ssh",
                "-c",
                f"gpg.ssh.program={tools['ssh-keygen']}",
                "-c",
                f"gpg.ssh.allowedSignersFile={signers}",
                "-c",
                "gpg.minTrustLevel=fully",
            ],
        )
    except subprocess.CalledProcessError:
        raise UpdateRefused("signature") from None
    commit = _git(["rev-parse", tag + "^{commit}"], bare, executable=tools["git"])
    return bare, commit


def _extract_release(bare: Path, commit: str, workspace: Path, git: str) -> Path:
    archive = workspace / "release.tar"
    _git(["archive", "--format=tar", f"--output={archive}", commit], bare, executable=git)
    if archive.stat().st_size > _MAX_ARCHIVE_BYTES:
        raise UpdateRefused("size")
    target = workspace / "release"
    target.mkdir(mode=0o700)
    total = count = 0
    with tarfile.open(archive, "r:") as bundle:
        for entry in bundle:
            count += 1
            parts = PurePosixPath(entry.name)
            if count > 10000 or parts.is_absolute() or ".." in parts.parts or "\\" in entry.name:
                raise UpdateRefused("archive")
            if any(part.casefold() == ".git" for part in parts.parts):
                raise UpdateRefused("archive")
            destination = target.joinpath(*parts.parts)
            if entry.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            if not entry.isfile() or entry.size > 32 * 1024 * 1024:
                raise UpdateRefused("archive")
            total += entry.size
            if total > _MAX_ARCHIVE_BYTES:
                raise UpdateRefused("size")
            destination.parent.mkdir(parents=True, exist_ok=True)
            stream = bundle.extractfile(entry)
            if stream is None:
                raise UpdateRefused("archive")
            with stream, destination.open("xb") as output:
                shutil.copyfileobj(stream, output, 1024 * 1024)
            destination.chmod(0o755 if entry.mode & 0o111 else 0o644)
    return target


def _prepare_payload(release: Path, destination: Path, commit: str, version: str) -> None:
    destination.mkdir(mode=0o700)
    shutil.copytree(release / "bridge", destination / "bridge")
    for name in ("sillytavern_telegram_bridge.py", "CHANGELOG.md", "requirements.lock"):
        shutil.copy2(release / name, destination / name)
    (destination / MARKER).write_text(
        json.dumps(
            {"application": APPLICATION, "format": 1, "commit": commit, "version": version},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _clean_source(source: Path, git: str) -> str:
    if _git(["symbolic-ref", "--short", "HEAD"], source, executable=git) != "main":
        raise UpdateRefused("branch")
    if _git(["status", "--porcelain"], source, executable=git):
        raise UpdateRefused("dirty")
    return _git(["rev-parse", "HEAD"], source, executable=git)


def _activate_live(payload: Path, live: Path, backup: Path) -> None:
    existed = live.exists()
    if existed:
        live.rename(backup)
    try:
        payload.rename(live)
    except OSError:
        if existed:
            backup.rename(live)
        raise


def apply_update(plan: UpdatePlan) -> UpdateOutcome:
    source_changed = live_changed = False
    commit = ""
    phase = "preflight"
    try:
        validate_plan(plan)
        tools = {name: _executable(name) for name in ("git", "ssh-keygen", "systemctl")}
        assert plan.trusted_signers is not None  # noqa: S101 -- validated above, type narrowing only
        trust = _trusted_signers(plan.trusted_signers)
        old = _clean_source(plan.source, tools["git"])
        state = _run([tools["systemctl"], "--user", "show", plan.unit, "--property=LoadState", "--value"])
        if state.stdout.strip() != "loaded":
            raise UpdateRefused("supervisor")
        with (
            _update_lock(plan.live.parent),
            tempfile.TemporaryDirectory(
                prefix=".bridge-update-",
                dir=plan.live.parent,
            ) as scratch,
        ):
            workspace = Path(scratch)
            phase = "verify"
            bare, commit = _verified_release(plan, workspace, tools, trust)
            try:
                _git(["merge-base", "--is-ancestor", old, commit], bare, executable=tools["git"])
            except subprocess.CalledProcessError:
                raise UpdateRefused("ancestry") from None
            if old == commit:
                return UpdateOutcome(UpdateStatus.ALREADY_LATEST, plan.release_version, commit)
            phase = "prepare"
            release = _extract_release(bare, commit, workspace, tools["git"])
            if (
                hashlib.sha256((release / "requirements.lock").read_bytes()).digest()
                != hashlib.sha256(
                    (plan.source / "requirements.lock").read_bytes(),
                ).digest()
            ):
                raise UpdateRefused("dependencies")
            payload = workspace / "payload"
            _prepare_payload(release, payload, commit, plan.release_version)
            _run([sys.executable, "-I", "-m", "compileall", "-q", str(payload)])
            # Detect a concurrent editor or changed branch before touching it.
            if _clean_source(plan.source, tools["git"]) != old:
                raise UpdateRefused("changed")
            _validate_live_contents(plan.live)
            phase = "activate"
            _git(
                ["fetch", "--no-tags", "--no-recurse-submodules", str(bare), commit],
                plan.source,
                executable=tools["git"],
            )
            _git(["merge", "--ff-only", commit], plan.source, executable=tools["git"])
            source_changed = True
            # A failed activation AND failed restoration must not let temporary
            # workspace cleanup erase the old deployment. Retain this sibling
            # backup for operator recovery, including supervisor failures.
            backup = Path(tempfile.mkdtemp(prefix=".bridge-previous-", dir=plan.live.parent))
            backup.rmdir()
            _activate_live(payload, plan.live, backup)
            live_changed = True
            try:
                _run([tools["systemctl"], "--user", "--no-block", "restart", plan.unit])
            except (OSError, subprocess.SubprocessError):
                return UpdateOutcome(
                    UpdateStatus.RESTART_REQUIRED, plan.release_version, commit, "restart", source_changed, live_changed
                )
            return UpdateOutcome(
                UpdateStatus.RESTART_SCHEDULED, plan.release_version, commit, "", source_changed, live_changed
            )
    except UpdateRefused as exc:
        return UpdateOutcome(UpdateStatus.REFUSED, plan.release_version, commit, str(exc), source_changed, live_changed)
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError):
        logging.warning("Self-update failed during %s; inspect the deployment before retrying", phase)
        return UpdateOutcome(UpdateStatus.FAILED, plan.release_version, commit, phase, source_changed, live_changed)

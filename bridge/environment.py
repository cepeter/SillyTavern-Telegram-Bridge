"""Process environment bootstrap for the executable entry point."""

from __future__ import annotations

import errno
import os
import re
import stat
from collections.abc import MutableMapping
from pathlib import Path

from bridge.config_values import ConfigurationError

_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def environment_file(
    environ: MutableMapping[str, str] | None = None,
) -> Path:
    source = os.environ if environ is None else environ
    return Path(
        source.get(
            "SILLYTAVERN_ENV_FILE",
            str(Path.home() / ".local/share/sillytavern-telegram/.env"),
        )
    ).expanduser()


def _read_private_environment(path: Path) -> str | None:
    """Read a bounded regular file after verifying its opened descriptor."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ConfigurationError(f"environment symlink is not a regular file: {path}") from None
        raise ConfigurationError(f"environment path is not a regular file or cannot be opened: {path}") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ConfigurationError(f"environment path is not a regular file: {path}")
        if os.name == "posix":
            if info.st_uid != os.geteuid():
                raise ConfigurationError(f"environment file owner must be the current user: {path}")
            if stat.S_IMODE(info.st_mode) & 0o077:
                raise ConfigurationError(f"environment file permissions must be private (chmod 600): {path}")
        limit = 1024 * 1024
        if info.st_size > limit:
            raise ConfigurationError(f"environment file exceeds the 1 MiB size limit: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ConfigurationError(f"environment file exceeds the 1 MiB size limit: {path}")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError:
            raise ConfigurationError(f"environment file must use UTF-8: {path}") from None
        if "\x00" in text:
            raise ConfigurationError(f"environment file contains a NUL character: {path}")
        return text
    finally:
        os.close(fd)


def load_environment_file(
    path: Path,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    target = os.environ if environ is None else environ
    path = Path(path)
    text = _read_private_environment(path)
    if text is None:
        return
    parsed: dict[str, str] = {}
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigurationError(f"invalid environment assignment at {path}:{line_number}")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not _ENVIRONMENT_NAME.fullmatch(key):
            raise ConfigurationError(f"invalid environment name at {path}:{line_number}")
        if value[:1] in {'"', "'"}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigurationError(f"unterminated quoted value at {path}:{line_number}")
            value = value[1:-1]
        parsed.setdefault(key, value)
    # Parsing is atomic: neither a bad later assignment nor NUL can partially
    # apply secrets. Existing process variables still have highest precedence.
    for key, value in parsed.items():
        target.setdefault(key, value)


def bootstrap_environment(
    environ: MutableMapping[str, str] | None = None,
) -> Path:
    target = os.environ if environ is None else environ
    path = environment_file(target)
    load_environment_file(path, target)
    return path

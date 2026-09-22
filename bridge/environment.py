"""Process environment bootstrap for the executable entry point."""
from __future__ import annotations

from collections.abc import MutableMapping
import os
from pathlib import Path
import re


DEFAULT_BRIDGE_HOME = (
    Path.home() / ".local/share/sillytavern-telegram"
)
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\\Z")


def environment_file(
    environ: MutableMapping[str, str] | None = None,
) -> Path:
    source = os.environ if environ is None else environ
    return Path(
        source.get(
            "SILLYTAVERN_ENV_FILE",
            str(DEFAULT_BRIDGE_HOME / ".env"),
        )
    ).expanduser()


def load_environment_file(
    path: Path,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    target = os.environ if environ is None else environ
    path = Path(path)

    if not path.exists():
        return
    if not path.is_file():
        raise RuntimeError(
            f"environment path is not a regular file: {path}"
        )

    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise RuntimeError(
                f"invalid environment assignment at {path}:{line_number}"
            )

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not _ENVIRONMENT_NAME.fullmatch(key):
            raise RuntimeError(
                f"invalid environment name at {path}:{line_number}: {key!r}"
            )

        if value[:1] in {"\\\"", "'"}:
            if len(value) < 2 or value[-1] != value[0]:
                raise RuntimeError(
                    f"unterminated quoted value at {path}:{line_number}"
                )
            value = value[1:-1]

        target.setdefault(key, value)


def bootstrap_environment(
    environ: MutableMapping[str, str] | None = None,
) -> Path:
    target = os.environ if environ is None else environ
    path = environment_file(target)
    load_environment_file(path, target)
    return path

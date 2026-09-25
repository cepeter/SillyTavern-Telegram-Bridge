"""Offline checks for the project's flat runtime lock and development inputs.

Validate exact pins, SHA-256 metadata, and direct requirements against the
Python 3.11 target on the current platform. This is not a dependency resolver,
artifact hash verifier, or vulnerability scanner. Installation and pip-audit
remain separate gates. Inputs are never echoed in parsing errors.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

HASH_TOKEN = re.compile(r"--hash=sha256:[0-9a-fA-F]{64}\Z")


def logical_records(text: str) -> Iterator[tuple[int, str]]:
    pending: list[str] = []
    start = 0
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.split(r"\s+#", line, maxsplit=1)[0].rstrip()
        if not pending:
            start = number
        continued = line.endswith("\\")
        pending.append(line[:-1].rstrip() if continued else line)
        if not continued:
            yield start, " ".join(pending)
            pending = []
    if pending:
        raise ValueError("unterminated requirement continuation")


def read_records(text: str, label: str, errors: list[str], *, hashed: bool) -> list[Requirement]:
    requirements: list[Requirement] = []
    try:
        records = list(logical_records(text))
    except ValueError:
        errors.append(f"{label}: unterminated requirement continuation")
        return requirements
    for number, record in records:
        where = f"{label} line {number}"
        if record.startswith("-") or "\x00" in record:
            errors.append(f"{where}: unsupported requirement directive or control character")
            continue
        raw_requirement = record
        if hashed:
            split = re.search(r"\s+--", record)
            raw_requirement = record[: split.start()] if split else record
            tokens = record[split.start() :].split() if split else []
            if not tokens or not all(HASH_TOKEN.fullmatch(token) for token in tokens):
                errors.append(f"{where}: each locked requirement needs valid SHA-256 hash metadata")
        try:
            requirement = Requirement(raw_requirement)
        except InvalidRequirement:
            errors.append(f"{where}: invalid requirement declaration")
            continue
        if requirement.url is not None:
            errors.append(f"{where}: URL/VCS requirements are unsupported; use reviewed index packages")
            continue
        if hashed or label == "development":
            specifiers = list(requirement.specifier)
            if len(specifiers) != 1 or specifiers[0].operator != "==" or "*" in specifiers[0].version:
                errors.append(f"{where}: requirement must have one exact version pin")
                continue
            try:
                Version(specifiers[0].version)
            except InvalidVersion:
                errors.append(f"{where}: invalid exact version pin")
                continue
        requirements.append(requirement)
    return requirements


def validate(
    manifest: str, locked: str, development: str, *, environment: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    errors: list[str] = []
    direct = read_records(manifest, "runtime manifest", errors, hashed=False)
    pins = read_records(locked, "runtime lock", errors, hashed=True)
    read_records(development, "development", errors, hashed=False)
    active_environment: dict[str, str] = {key: str(value) for key, value in default_environment().items()}
    active_environment.update(python_version="3.11", python_full_version="3.11.0", extra="")
    if environment is not None:
        active_environment.update(environment)
    active: dict[str, Version] = {}
    for requirement in pins:
        if requirement.marker and not requirement.marker.evaluate(active_environment):
            continue
        name = canonicalize_name(requirement.name)
        if name in active:
            errors.append(f"runtime lock: duplicate active pin for {name}")
            continue
        active[name] = Version(next(iter(requirement.specifier)).version)
    for requirement in direct:
        if requirement.marker and not requirement.marker.evaluate(active_environment):
            continue
        name = canonicalize_name(requirement.name)
        version = active.get(name)
        if version is None:
            errors.append(f"runtime lock: missing active direct requirement {name}")
        elif not requirement.specifier.contains(version, prereleases=True):
            errors.append(f"runtime lock: pinned {name} does not satisfy the runtime manifest")
    return tuple(errors)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=root / "requirements.txt")
    parser.add_argument("--lock", type=Path, default=root / "requirements.lock")
    parser.add_argument("--development", type=Path, default=root / "requirements-dev.txt")
    args = parser.parse_args()
    try:
        errors = validate(
            args.manifest.read_text(encoding="utf-8"),
            args.lock.read_text(encoding="utf-8"),
            args.development.read_text(encoding="utf-8"),
        )
    except (OSError, UnicodeError):
        print("dependency-lock: cannot read the configured UTF-8 dependency files", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("dependency-lock=ok (Python 3.11 target; current platform)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

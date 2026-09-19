"""Ordered, transactional SQLite schema migrations."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import sqlite3
import time


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


class MigrationError(RuntimeError):
    def __init__(self, version: int | None, name: str, message: str):
        self.version = version
        self.name = name
        self.detail = message
        if version is None:
            prefix = "Migration history"
        else:
            prefix = f"Migration {version:03d} ({name})"
        super().__init__(f"{prefix} failed: {message}")


_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at REAL NOT NULL
)
"""


def _validate_declarations(migrations: Sequence[Migration]) -> None:
    seen: set[int] = set()
    previous: int | None = None
    for migration in migrations:
        version = migration.version
        name = str(migration.name or "").strip()
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version <= 0
        ):
            raise MigrationError(
                None,
                "declarations",
                "migration version must be a positive integer",
            )
        if version in seen:
            raise MigrationError(
                version,
                name or "unnamed",
                f"duplicate migration version {version:03d}",
            )
        if previous is not None and version <= previous:
            raise MigrationError(
                version,
                name or "unnamed",
                "declared migration versions must be strictly increasing",
            )
        if not name:
            raise MigrationError(
                version,
                "unnamed",
                "migration name must not be empty",
            )
        seen.add(version)
        previous = version


def _load_applied(db: sqlite3.Connection) -> list[tuple[int, str]]:
    return [
        (int(version), str(name))
        for version, name in db.execute(
            "SELECT version,name FROM schema_migrations ORDER BY version"
        ).fetchall()
    ]


def _validate_history(
    applied: Sequence[tuple[int, str]],
    migrations: Sequence[Migration],
) -> None:
    declared = {migration.version: migration for migration in migrations}

    for version, name in applied:
        if version not in declared:
            raise MigrationError(
                version,
                name,
                f"database contains unknown migration version {version:03d}",
            )

    expected_prefix = tuple(
        (migration.version, migration.name)
        for migration in migrations[: len(applied)]
    )
    actual = tuple(applied)
    if tuple(version for version, _name in actual) != tuple(
        version for version, _name in expected_prefix
    ):
        raise MigrationError(
            None,
            "history",
            "applied migration versions are not a valid prefix of declarations",
        )

    for (version, actual_name), (_expected_version, expected_name) in zip(
        actual,
        expected_prefix,
    ):
        if actual_name != expected_name:
            raise MigrationError(
                version,
                actual_name,
                f"name mismatch: ledger={actual_name!r}, declared={expected_name!r}",
            )


def run_migrations(
    db: sqlite3.Connection,
    migrations: Sequence[Migration],
) -> None:
    declared = tuple(migrations)
    _validate_declarations(declared)

    if db.in_transaction:
        raise MigrationError(
            None,
            "history",
            "cannot run migrations inside an active transaction",
        )

    db.execute(_LEDGER_SQL)
    db.commit()

    applied = _load_applied(db)
    _validate_history(applied, declared)

    for migration in declared[len(applied) :]:
        try:
            db.execute("BEGIN IMMEDIATE")
            migration.apply(db)
            db.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) "
                "VALUES(?,?,?)",
                (migration.version, migration.name, time.time()),
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            raise MigrationError(
                migration.version,
                migration.name,
                str(exc),
            ) from exc

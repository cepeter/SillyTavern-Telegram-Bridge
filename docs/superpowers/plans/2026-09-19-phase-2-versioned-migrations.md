# Phase 2 Versioned Database Migrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace implicit/ad hoc SQLite schema creation with one ordered migration ledger and remove structural DDL from Scene State, Director Goals, and sync request/startup feature paths while preserving existing data and startup reliability.

**Architecture:** Add a generic migration engine in `bridge/migrations.py`; keep application-specific migration declarations in `bridge/schema.py`. Migration 001 reconciles empty/legacy databases to the current core schema, migrations 002–004 own Scene State, Director Goals, and the sync lifecycle trigger, and non-structural retention/orphan cleanup remains startup maintenance outside migration history.

**Tech Stack:** Python 3.11, stdlib `sqlite3`/`dataclasses`/`unittest`, SQLite WAL, existing shared-runtime compatibility loader, pytest/unittest CI.

**Spec:** `docs/superpowers/specs/2026-09-19-versioned-database-migrations-design.md`

## Global Constraints

- Existing user data must remain intact.
- Migrations are ordered, idempotent, and forward-only.
- `schema_migrations(version, name, applied_at)` is the migration ledger.
- Applied migration history must be a valid prefix of the declared migration list.
- Unknown future versions, gaps, and version/name drift fail startup rather than mutating the database.
- A failed migration rolls back and is not recorded.
- Migration failures identify the exact migration version and name.
- Migration 001 is the current core baseline / legacy reconciliation bridge; future schema evolution must use new versions rather than editing released migration behavior.
- Migration 002 owns Scene State DDL.
- Migration 003 owns Director Goals DDL.
- Migration 004 owns `sessions_delete_sync_binding`.
- Scene State and Director Goals request paths perform no structural DDL.
- Sync orphan-row cleanup remains data maintenance, not a schema migration.
- Existing retention cleanup continues to run at startup even after all migrations are already applied.
- Preserve `scheduler_safety.py`'s `_DB_SCHEMA_LOCK`, `_DB_SCHEMA_READY`, and lightweight-worker connection behavior.
- Do not introduce an ORM.
- Do not perform Phase 3 repository/transaction ownership work.
- Do not remove compatibility runtime/safety overrides in this phase.
- Do not add a Memory Curator migration: it currently has no feature-specific SQLite schema.
- No new public runtime override or `_ORIGINAL_*` chain may be introduced.

## Review Focus

- **Current schema without a ledger:** startup must adopt migrations 001–004 without deleting or rewriting existing Scene State, Director Goal, or core rows.
- **Corrupt/newer migration history:** name drift, gaps, or unknown future versions must fail before any application migration runs.
- **Failure after partial DDL inside one migration:** all changes from that migration must roll back; its ledger row and every later migration must remain absent.
- **Already fully migrated database:** startup retention/orphan maintenance must still execute even though no numbered migration executes.
- **Request-time Scene/Director operations:** representative reads/writes/clears/policy lookups must execute no `CREATE`, `ALTER`, or structural migration SQL.

---

## File Structure

### Create: `bridge/migrations.py`

Own only migration mechanics:

- immutable `Migration` declaration
- `MigrationError`
- ledger bootstrap
- declaration validation
- applied-history validation
- ordered transactional execution
- rollback/error reporting

It contains no application-specific table/index/trigger SQL.

### Modify: `bridge/schema.py`

Own application schema:

- explicit ordinary-module imports
- existing core reconciliation helpers
- migration 001–004 functions
- immutable `SCHEMA_MIGRATIONS`
- non-structural startup retention cleanup
- compatibility entry point `initialize_database_schema()`

### Modify: `bridge/scene_state.py`

Own Scene State behavior only. Remove `ensure_scene_state_schema()` and all production calls to it.

### Modify: `bridge/director_goals.py`

Own Director Goal persistence/policy behavior only. Remove `ensure_director_goal_schema()` and all production calls to it.

### Modify: `bridge/sync_safety.py`

Retain the current compatibility `initialize_database_schema()` override for orphan-row maintenance, but remove structural trigger creation.

### Create: `tests/test_migrations.py`

Own migration engine, baseline/legacy/current-schema adoption, feature migration, history, rollback, startup-maintenance, and request-time no-DDL tests.

### Modify: `tests/test_scene_state.py`

Remove the test's explicit feature-schema bootstrap; setup already goes through migrated `db_connect()`.

### Modify: `tests/test_sync_audit.py`

Prove sync orphan cleanup still runs after migrations and performs no structural DDL.

### Verify: `tests/test_runtime_loader.py`

No planned edit. Its existing override-audit expectations must remain green because `sync_safety.py` still owns the compatibility `initialize_database_schema()` override during Phase 2.

---

### Task 1: Add the generic migration engine

**Files:**
- Create: `bridge/migrations.py`
- Create: `tests/test_migrations.py`

**Interfaces:**
- Consumes: stdlib `sqlite3.Connection`
- Produces:
  - `Migration(version: int, name: str, apply: Callable[[sqlite3.Connection], None])`
  - `MigrationError(version: int | None, name: str, message: str)`
  - `run_migrations(db: sqlite3.Connection, migrations: Sequence[Migration]) -> None`

- [ ] **Step 1: Write failing migration-engine tests**

Create `tests/test_migrations.py` with the engine-only tests first:

```python
import sqlite3
import unittest

from bridge.migrations import Migration, MigrationError, run_migrations


class MigrationEngineTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")

    def tearDown(self):
        self.db.close()

    def test_applies_missing_suffix_once_and_records_order(self):
        calls = []

        def first(db):
            calls.append("first")
            db.execute("CREATE TABLE first_table(id INTEGER PRIMARY KEY)")

        def second(db):
            calls.append("second")
            db.execute("CREATE TABLE second_table(id INTEGER PRIMARY KEY)")

        migrations = (
            Migration(1, "first", first),
            Migration(2, "second", second),
        )

        run_migrations(self.db, migrations)
        run_migrations(self.db, migrations)

        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(
            self.db.execute(
                "SELECT version,name FROM schema_migrations ORDER BY version"
            ).fetchall(),
            [(1, "first"), (2, "second")],
        )

    def test_failed_migration_rolls_back_and_stops(self):
        calls = []

        def first(db):
            calls.append("first")
            db.execute("CREATE TABLE first_table(id INTEGER PRIMARY KEY)")

        def broken(db):
            calls.append("broken")
            db.execute("CREATE TABLE rolled_back_table(id INTEGER PRIMARY KEY)")
            raise sqlite3.OperationalError("deliberate migration failure")

        def never(db):
            calls.append("never")

        with self.assertRaisesRegex(
            MigrationError,
            r"Migration 002 \(broken\) failed",
        ):
            run_migrations(
                self.db,
                (
                    Migration(1, "first", first),
                    Migration(2, "broken", broken),
                    Migration(3, "never", never),
                ),
            )

        self.assertEqual(calls, ["first", "broken"])
        self.assertEqual(
            self.db.execute(
                "SELECT version,name FROM schema_migrations ORDER BY version"
            ).fetchall(),
            [(1, "first")],
        )
        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='rolled_back_table'"
            ).fetchone()
        )

    def test_history_name_drift_is_rejected_before_apply(self):
        self.db.execute(
            "CREATE TABLE schema_migrations("
            "version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at REAL NOT NULL)"
        )
        self.db.execute(
            "INSERT INTO schema_migrations VALUES(1,'old_name',1.0)"
        )
        self.db.commit()
        called = []

        with self.assertRaisesRegex(MigrationError, "name mismatch"):
            run_migrations(
                self.db,
                (Migration(1, "current_name", lambda db: called.append(True)),),
            )

        self.assertEqual(called, [])

    def test_history_gap_is_rejected_before_apply(self):
        self.db.execute(
            "CREATE TABLE schema_migrations("
            "version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at REAL NOT NULL)"
        )
        self.db.executemany(
            "INSERT INTO schema_migrations VALUES(?,?,?)",
            [(1, "one", 1.0), (3, "three", 3.0)],
        )
        self.db.commit()

        with self.assertRaisesRegex(MigrationError, "valid prefix"):
            run_migrations(
                self.db,
                (
                    Migration(1, "one", lambda db: None),
                    Migration(2, "two", lambda db: None),
                    Migration(3, "three", lambda db: None),
                ),
            )

    def test_unknown_future_version_is_rejected(self):
        self.db.execute(
            "CREATE TABLE schema_migrations("
            "version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at REAL NOT NULL)"
        )
        self.db.executemany(
            "INSERT INTO schema_migrations VALUES(?,?,?)",
            [(1, "one", 1.0), (5, "future", 5.0)],
        )
        self.db.commit()

        with self.assertRaisesRegex(MigrationError, "unknown migration version 005"):
            run_migrations(
                self.db,
                (Migration(1, "one", lambda db: None),),
            )

    def test_invalid_declarations_are_rejected(self):
        cases = (
            (
                (Migration(1, "one", lambda db: None),
                 Migration(1, "duplicate", lambda db: None)),
                "duplicate migration version",
            ),
            (
                (Migration(2, "two", lambda db: None),
                 Migration(1, "one", lambda db: None)),
                "strictly increasing",
            ),
            (
                (Migration(0, "zero", lambda db: None),),
                "positive integer",
            ),
            (
                (Migration(1, "", lambda db: None),),
                "name must not be empty",
            ),
        )
        for migrations, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(MigrationError, message):
                    run_migrations(self.db, migrations)

    def test_active_transaction_is_rejected_without_committing_it(self):
        self.db.execute("CREATE TABLE caller_state(value TEXT)")
        self.db.execute("INSERT INTO caller_state VALUES('uncommitted')")
        self.assertTrue(self.db.in_transaction)

        with self.assertRaisesRegex(MigrationError, "active transaction"):
            run_migrations(
                self.db,
                (Migration(1, "one", lambda db: None),),
            )

        self.db.rollback()
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM caller_state").fetchone()[0],
            0,
        )
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python -m unittest discover -s tests -p 'test_migrations.py' -v
```

Expected: ERROR/FAIL because `bridge.migrations` does not exist.

- [ ] **Step 3: Implement the minimal migration engine**

Create `bridge/migrations.py`:

```python
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
```

- [ ] **Step 4: Run migration-engine tests**

Run:

```bash
python -m unittest discover -s tests -p 'test_migrations.py' -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add bridge/migrations.py tests/test_migrations.py
git commit -m "feat: add versioned migration engine"
```

---

### Task 2: Move the current core schema into migration 001 and preserve startup maintenance

**Files:**
- Modify: `bridge/schema.py:1-340`
- Modify: `tests/test_migrations.py`
- Test alongside: `tests/test_database_optimization.py`, `tests/test_sqlite_contention.py`

**Interfaces:**
- Consumes:
  - `Migration`
  - `run_migrations(db, migrations)`
- Produces:
  - `_migration_001_core_baseline(db: sqlite3.Connection) -> None`
  - `_run_startup_database_cleanup(db: sqlite3.Connection) -> None`
  - `SCHEMA_MIGRATIONS: tuple[Migration, ...]`
  - `initialize_database_schema(db: sqlite3.Connection) -> None`

- [ ] **Step 1: Extend tests with direct-import, core-ledger, legacy-upgrade, and recurring-maintenance coverage**

Append to `tests/test_migrations.py`:

```python
import time

import bridge.schema as schema


class ApplicationSchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")

    def tearDown(self):
        self.db.close()

    def _columns(self, table):
        return {
            row[1]
            for row in self.db.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }

    def _create_representative_legacy_schema(self):
        self.db.executescript(
            """
            CREATE TABLE messages (
                chat_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE sessions (
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                title TEXT NOT NULL,
                character_file TEXT NOT NULL,
                model_id TEXT NOT NULL,
                persona_id TEXT NOT NULL,
                world_file TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(chat_id, session_id)
            );
            CREATE TABLE response_variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                user_content TEXT NOT NULL,
                response TEXT NOT NULL,
                variant_index INTEGER NOT NULL,
                selected INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL
            );
            CREATE TABLE data_bank_documents (
                chat_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(chat_id, document_id)
            );
            CREATE TABLE data_bank_embeddings (
                chunk_id INTEGER PRIMARY KEY,
                dimensions INTEGER NOT NULL,
                vector_json TEXT NOT NULL
            );
            CREATE TABLE rag_embedding_cache (
                cache_key TEXT PRIMARY KEY,
                dimensions INTEGER NOT NULL,
                vector_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE failed_turns (
                chat_id TEXT NOT NULL,
                telegram_message_id TEXT NOT NULL,
                text TEXT NOT NULL,
                model TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 1,
                last_error TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(chat_id, telegram_message_id)
            );
            CREATE TABLE panel_sessions (
                chat_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                expires_at REAL NOT NULL,
                PRIMARY KEY(chat_id, message_id)
            );
            CREATE TABLE sync_bindings (
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                sync_id TEXT NOT NULL,
                last_hash TEXT NOT NULL DEFAULT '',
                last_direction TEXT NOT NULL DEFAULT '',
                last_synced_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(chat_id, session_id),
                UNIQUE(chat_id, sync_id)
            );
            CREATE TABLE group_sessions (
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT 'Group chat',
                enabled INTEGER NOT NULL DEFAULT 0,
                turn_index INTEGER NOT NULL DEFAULT 0,
                members_json TEXT NOT NULL DEFAULT '[]',
                updated_at REAL NOT NULL,
                PRIMARY KEY(chat_id, session_id)
            );
            """
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,role,content,created_at) "
            "VALUES('chat','user','legacy message',1.0)"
        )
        self.db.execute(
            "INSERT INTO sessions("
            "chat_id,session_id,title,character_file,model_id,persona_id,"
            "world_file,created_at,updated_at"
            ") VALUES('chat','legacy','Legacy','','model','','',1.0,1.0)"
        )
        self.db.execute(
            "INSERT INTO response_variants("
            "chat_id,session_id,user_content,response,variant_index,selected,created_at"
            ") VALUES('chat','legacy','u','a',0,1,1.0)"
        )
        self.db.execute(
            "INSERT INTO data_bank_documents("
            "chat_id,document_id,filename,byte_size,chunk_count,created_at,updated_at"
            ") VALUES('chat','doc-1','notes.txt',1,1,1.0,1.0)"
        )
        self.db.execute(
            "INSERT INTO failed_turns("
            "chat_id,telegram_message_id,text,model,attempts,last_error,created_at,updated_at"
            ") VALUES('chat','1','text','model',1,'err',1.0,1.0)"
        )
        self.db.execute(
            "INSERT INTO panel_sessions(chat_id,message_id,session_id,expires_at) "
            "VALUES('chat','1','legacy',9999999999.0)"
        )
        self.db.execute(
            "INSERT INTO sync_bindings(chat_id,session_id,sync_id) "
            "VALUES('chat','legacy','stb-legacy')"
        )
        self.db.execute(
            "INSERT INTO group_sessions("
            "chat_id,session_id,title,enabled,turn_index,members_json,updated_at"
            ") VALUES('chat','legacy','Legacy',1,0,'[]',1.0)"
        )
        self.db.commit()

    def test_core_baseline_bootstraps_empty_database(self):
        schema.initialize_database_schema(self.db)

        self.assertEqual(
            self.db.execute(
                "SELECT version,name FROM schema_migrations ORDER BY version"
            ).fetchall(),
            [(1, "core_baseline")],
        )
        for table in (
            "messages",
            "sessions",
            "generation_settings",
            "data_bank_documents",
            "jobs",
            "panel_sessions",
            "sync_bindings",
            "group_sessions",
        ):
            with self.subTest(table=table):
                self.assertIsNotNone(
                    self.db.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type IN ('table','view') AND name=?",
                        (table,),
                    ).fetchone()
                )

    def test_core_baseline_upgrades_representative_legacy_schema(self):
        self._create_representative_legacy_schema()

        schema.initialize_database_schema(self.db)

        expected_columns = {
            "messages": {
                "session_id",
                "telegram_message_id",
                "telegram_message_ids",
            },
            "sessions": {
                "author_note",
                "system_prompt",
                "response_language",
            },
            "response_variants": {"user_rowid"},
            "data_bank_documents": {"version_number", "active"},
            "data_bank_embeddings": {
                "embedding_namespace",
                "vector_signature",
                "vector_norm",
            },
            "rag_embedding_cache": {"vector_norm"},
            "failed_turns": {"session_id"},
            "panel_sessions": {"owner_user_id"},
            "sync_bindings": {
                "conflict",
                "last_error",
                "last_checked_at",
                "realtime_enabled",
                "realtime_failures",
                "realtime_next_retry_at",
            },
            "group_sessions": {
                "mode",
                "forced_speaker",
                "turn_user_id",
                "turn_users_json",
            },
        }
        for table, columns in expected_columns.items():
            with self.subTest(table=table):
                self.assertTrue(columns <= self._columns(table))

        message = self.db.execute(
            "SELECT content,session_id FROM messages"
        ).fetchone()
        self.assertEqual(message, ("legacy message", "default"))
        self.assertEqual(
            self.db.execute(
                "SELECT title,author_note,response_language FROM sessions "
                "WHERE session_id='legacy'"
            ).fetchone(),
            ("Legacy", "", "auto"),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT filename,version_number,active "
                "FROM data_bank_documents WHERE document_id='doc-1'"
            ).fetchone(),
            ("notes.txt", 1, 1),
        )

    def test_startup_cleanup_runs_when_core_migration_is_already_applied(self):
        schema.initialize_database_schema(self.db)
        old = time.time() - 200 * 86400
        self.db.execute(
            "INSERT INTO processed_updates(update_id,processed_at) VALUES(1,?)",
            (old,),
        )
        self.db.execute(
            "INSERT INTO callback_tokens(token,kind,value,chat_id,expires_at) "
            "VALUES('expired','x','x','chat',?)",
            (time.time() - 10,),
        )
        self.db.commit()

        schema.initialize_database_schema(self.db)

        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM processed_updates WHERE update_id=1"
            ).fetchone()
        )
        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM callback_tokens WHERE token='expired'"
            ).fetchone()
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0],
            1,
        )
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
python -m unittest discover -s tests -p 'test_migrations.py' -v
```

Expected: FAIL because `bridge.schema` is not independently importable yet and no core migration ledger exists.

- [ ] **Step 3: Make `schema.py` directly importable**

Add at the top of `bridge/schema.py`:

```python
import sqlite3
import time

from bridge.common import (
    PROCESSED_UPDATE_RETENTION_SECONDS as _PROCESSED_UPDATE_RETENTION_SECONDS,
)
from bridge.migrations import (
    Migration as _Migration,
    run_migrations as _run_migrations,
)
```

Use `_PROCESSED_UPDATE_RETENTION_SECONDS` in `_run_startup_database_cleanup()` for the processed-update retention cutoff.

Use underscored imported callables so the compatibility runtime's public-callable override detector does not gain new public names.

- [ ] **Step 4: Separate retention cleanup from structural helpers**

Delete these data-maintenance statements from `_ensure_job_tables()`:

```python
db.execute(
    "DELETE FROM processed_updates WHERE processed_at < ?",
    (time.time() - _PROCESSED_UPDATE_RETENTION_SECONDS,),
)
db.execute(
    "DELETE FROM rag_embedding_cache WHERE created_at < ?",
    (time.time() - 30 * 86400,),
)
db.execute(
    "DELETE FROM jobs WHERE state='done' AND updated_at < ?",
    (time.time() - 30 * 86400,),
)
db.execute(
    "DELETE FROM jobs WHERE state='failed' AND updated_at < ?",
    (time.time() - 90 * 86400,),
)
db.execute(
    "DELETE FROM failed_turns WHERE updated_at < ?",
    (time.time() - 90 * 86400,),
)
```

Delete these data-maintenance statements from `_ensure_panel_tables()`:

```python
db.execute("DELETE FROM callback_tokens WHERE expires_at < ?", (time.time(),))
db.execute("DELETE FROM panel_sessions WHERE expires_at < ?", (time.time(),))
db.execute(
    "DELETE FROM operations WHERE updated_at < ?",
    (time.time() - 90 * 86400,),
)
```

Add one non-structural startup cleanup function:

```python
def _run_startup_database_cleanup(db: sqlite3.Connection) -> None:
    now = time.time()
    db.execute(
        "DELETE FROM processed_updates WHERE processed_at < ?",
        (now - _PROCESSED_UPDATE_RETENTION_SECONDS,),
    )
    db.execute(
        "DELETE FROM rag_embedding_cache WHERE created_at < ?",
        (now - 30 * 86400,),
    )
    db.execute(
        "DELETE FROM jobs WHERE state='done' AND updated_at < ?",
        (now - 30 * 86400,),
    )
    db.execute(
        "DELETE FROM jobs WHERE state='failed' AND updated_at < ?",
        (now - 90 * 86400,),
    )
    db.execute(
        "DELETE FROM failed_turns WHERE updated_at < ?",
        (now - 90 * 86400,),
    )
    db.execute(
        "DELETE FROM callback_tokens WHERE expires_at < ?",
        (now,),
    )
    db.execute(
        "DELETE FROM panel_sessions WHERE expires_at < ?",
        (now,),
    )
    db.execute(
        "DELETE FROM operations WHERE updated_at < ?",
        (now - 90 * 86400,),
    )
```

- [ ] **Step 5: Define migration 001 and the initial registry**

Add:

```python
def _migration_001_core_baseline(db: sqlite3.Connection) -> None:
    _ensure_core_tables(db)
    _ensure_generation_tables(db)
    _ensure_rag_tables(db)
    _ensure_job_tables(db)
    _ensure_panel_tables(db)


SCHEMA_MIGRATIONS = (
    _Migration(1, "core_baseline", _migration_001_core_baseline),
)
```

Replace `initialize_database_schema()` with:

```python
def initialize_database_schema(db: sqlite3.Connection) -> None:
    """Apply structural migrations, then run recurring startup cleanup."""
    _run_migrations(db, SCHEMA_MIGRATIONS)
    _run_startup_database_cleanup(db)
    db.commit()
```

Do not put recurring `DELETE` maintenance inside migration 001.

- [ ] **Step 6: Run migration and database startup tests**

Run:

```bash
python -m unittest   tests.test_migrations   tests.test_database_optimization   tests.test_sqlite_contention -v
```

Expected: PASS.

- [ ] **Step 7: Run runtime-loader tests**

Run:

```bash
python -m unittest tests.test_runtime_loader -v
```

Expected: PASS; adding an ordinary imported migrations module must not introduce a public callable override.

- [ ] **Step 8: Commit Task 2**

```bash
git add bridge/schema.py tests/test_migrations.py
git commit -m "refactor: establish core schema baseline migration"
```

---

### Task 3: Move Scene State and Director Goals DDL into migrations 002–003

**Files:**
- Modify: `bridge/schema.py`
- Modify: `bridge/scene_state.py:22-46,99-100,122-128,158,279`
- Modify: `bridge/director_goals.py:20-36,44-63`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_scene_state.py:68-86`

**Interfaces:**
- Consumes:
  - `SCHEMA_MIGRATIONS`
  - migration runner from Tasks 1–2
- Produces:
  - migration 002 `scene_state`
  - migration 003 `director_goals`
  - request-time feature functions with no structural DDL

- [ ] **Step 1: Add failing feature migration/data-preservation tests**

Append to `ApplicationSchemaMigrationTests` in `tests/test_migrations.py`:

```python
    def test_scene_and_director_migrations_create_expected_structures(self):
        schema.initialize_database_schema(self.db)

        applied = self.db.execute(
            "SELECT version,name FROM schema_migrations ORDER BY version"
        ).fetchall()
        self.assertEqual(
            applied,
            [
                (1, "core_baseline"),
                (2, "scene_state"),
                (3, "director_goals"),
            ],
        )
        for object_type, name in (
            ("table", "scene_states"),
            ("index", "scene_states_updated_idx"),
            ("trigger", "scene_states_session_delete"),
            ("table", "director_goals"),
            ("trigger", "director_goals_session_delete"),
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(
                    self.db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type=? AND name=?",
                        (object_type, name),
                    ).fetchone()
                )

    def test_existing_scene_and_director_rows_survive_ledger_adoption(self):
        schema._migration_001_core_baseline(self.db)
        self.db.execute(
            """CREATE TABLE scene_states (
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                state_json TEXT NOT NULL DEFAULT '{}',
                updated_through_rowid INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY(chat_id, session_id)
            )"""
        )
        self.db.execute(
            """CREATE TABLE director_goals (
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(chat_id, session_id)
            )"""
        )
        self.db.execute(
            "INSERT INTO scene_states VALUES("
            "'chat','session','{\"location\":\"Station\"}',7,1.0)"
        )
        self.db.execute(
            "INSERT INTO director_goals VALUES("
            "'chat','session','Keep the letter sealed.',1.0)"
        )
        self.db.commit()

        schema.initialize_database_schema(self.db)

        self.assertEqual(
            self.db.execute(
                "SELECT state_json,updated_through_rowid FROM scene_states"
            ).fetchone(),
            ('{"location":"Station"}', 7),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT goal FROM director_goals"
            ).fetchone()[0],
            "Keep the letter sealed.",
        )
```

- [ ] **Step 2: Add failing request-time no-DDL regression test**

Add a second test class that uses the real runtime startup path:

```python
from pathlib import Path
import tempfile

import bridge.runtime as rt


class RequestTimeSchemaRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = rt.DB_FILE
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.session = rt.create_session(
            self.db,
            "chat",
            "primary::main",
            session_id="migration-request",
            title="Migration request",
        )

    def tearDown(self):
        self.db.close()
        rt.DB_FILE = self.old_db
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.tmp.cleanup()

    def _assert_no_structural_ddl(self, callback):
        traced = []
        self.db.set_trace_callback(traced.append)
        try:
            callback()
        finally:
            self.db.set_trace_callback(None)

        structural = [
            sql for sql in traced
            if sql.lstrip().upper().startswith(
                ("CREATE TABLE", "CREATE INDEX", "CREATE TRIGGER", "ALTER TABLE")
            )
        ]
        self.assertEqual(structural, [], structural)

    def test_scene_and_director_request_paths_issue_no_structural_ddl(self):
        self.db.execute(
            "INSERT OR REPLACE INTO scene_states("
            "chat_id,session_id,state_json,updated_through_rowid,updated_at"
            ") VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "{}", 0, 1.0),
        )
        self.db.commit()

        operations = (
            lambda: rt.get_scene_state(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            lambda: rt.clear_scene_state(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            lambda: rt.get_director_goal(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            lambda: rt.set_director_goal(
                self.db,
                "chat",
                self.session["session_id"],
                "Keep tension unresolved.",
            ),
            lambda: rt._director_goal_customization(
                self.db,
                "chat",
                self.session,
            ),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                self._assert_no_structural_ddl(operation)
```

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```bash
python -m unittest tests.test_migrations -v
```

Expected: FAIL because migrations 002–003 do not exist and request-time feature helpers still create schema.

- [ ] **Step 4: Add migration 002 to `schema.py`**

Add:

```python
def _migration_002_scene_state(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS scene_states (
            chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            state_json TEXT NOT NULL DEFAULT '{}',
            updated_through_rowid INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL,
            PRIMARY KEY(chat_id, session_id)
        )"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS scene_states_updated_idx "
        "ON scene_states(chat_id, session_id, updated_through_rowid)"
    )
    db.execute(
        """CREATE TRIGGER IF NOT EXISTS scene_states_session_delete
        AFTER DELETE ON sessions
        BEGIN
            DELETE FROM scene_states
            WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        END"""
    )
```

- [ ] **Step 5: Add migration 003 to `schema.py`**

Add:

```python
def _migration_003_director_goals(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS director_goals (
            chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            goal TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(chat_id, session_id)
        )"""
    )
    db.execute(
        """CREATE TRIGGER IF NOT EXISTS director_goals_session_delete
        AFTER DELETE ON sessions
        BEGIN
            DELETE FROM director_goals
            WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        END"""
    )
```

Update the registry exactly:

```python
SCHEMA_MIGRATIONS = (
    _Migration(1, "core_baseline", _migration_001_core_baseline),
    _Migration(2, "scene_state", _migration_002_scene_state),
    _Migration(3, "director_goals", _migration_003_director_goals),
)
```

- [ ] **Step 6: Remove Scene State request-time schema creation**

Delete `ensure_scene_state_schema()` from `bridge/scene_state.py`.

Delete every call to it from:

- `get_scene_state()`
- `clear_scene_state()`
- `refresh_scene_state_now()`
- `queue_scene_state_refresh()`
- any other current call site found by search

Do not replace those calls with a different lazy schema guard.

- [ ] **Step 7: Remove Director Goals request-time schema creation**

Delete `ensure_director_goal_schema()` from `bridge/director_goals.py`.

Remove its calls from `get_director_goal()` and `set_director_goal()`.

The resulting functions start directly with data access:

```python
def get_director_goal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> str:
    row = db.execute(
        "SELECT goal FROM director_goals WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    ).fetchone()
    return str(row[0]) if row else ""


def set_director_goal(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
    goal: str,
) -> str:
    value = normalize_director_goal(goal)
    if not value:
        db.execute(
            "DELETE FROM director_goals WHERE chat_id=? AND session_id=?",
            (str(chat_id), str(session_id)),
        )
    else:
        db.execute(
            "INSERT OR REPLACE INTO director_goals("
            "chat_id,session_id,goal,updated_at"
            ") VALUES(?,?,?,?)",
            (str(chat_id), str(session_id), value, time.time()),
        )
    db.commit()
    return value
```

- [ ] **Step 8: Update the existing Scene State test to rely on startup migration**

In `tests/test_scene_state.py`, remove:

```python
rt.ensure_scene_state_schema(self.db)
```

from `test_clear_session_summary_also_clears_scene_state`.

Do not add a replacement schema call.

- [ ] **Step 9: Run focused migration/feature tests**

Run:

```bash
python -m unittest   tests.test_migrations   tests.test_scene_state   tests.test_director_goals   tests.test_group_director -v
```

Expected: PASS.

- [ ] **Step 10: Verify no feature schema guard remains**

Run:

```bash
grep -R "ensure_scene_state_schema\|ensure_director_goal_schema" -n bridge tests
grep -nE "CREATE (TABLE|INDEX|TRIGGER)|ALTER TABLE"   bridge/scene_state.py bridge/director_goals.py
```

Expected: no matches.

- [ ] **Step 11: Commit Task 3**

```bash
git add   bridge/schema.py   bridge/scene_state.py   bridge/director_goals.py   tests/test_migrations.py   tests/test_scene_state.py
git commit -m "refactor: migrate feature schema at startup"
```

---

### Task 4: Move the sync lifecycle trigger into migration 004 while preserving orphan cleanup

**Files:**
- Modify: `bridge/schema.py`
- Modify: `bridge/sync_safety.py:10-33`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_sync_audit.py`

**Interfaces:**
- Consumes: migrations 001–003
- Produces:
  - migration 004 `sync_lifecycle_trigger`
  - compatibility sync startup wrapper that performs data maintenance only

- [ ] **Step 1: Extend the schema migration test to require migration 004**

Change the expected list in `test_scene_and_director_migrations_create_expected_structures` to:

```python
self.assertEqual(
    applied,
    [
        (1, "core_baseline"),
        (2, "scene_state"),
        (3, "director_goals"),
        (4, "sync_lifecycle_trigger"),
    ],
)
```

Also assert:

```python
self.assertIsNotNone(
    self.db.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='trigger' AND name='sessions_delete_sync_binding'"
    ).fetchone()
)
```

- [ ] **Step 2: Add failing sync-cleanup/no-DDL test**

Append to `tests/test_sync_audit.py`:

```python
    def test_sync_startup_cleanup_removes_orphan_without_structural_ddl(self):
        self.db.execute(
            "INSERT INTO sync_bindings(chat_id,session_id,sync_id) "
            "VALUES('orphan-chat','missing-session','stb-orphan')"
        )
        self.db.commit()

        traced = []
        self.db.set_trace_callback(traced.append)
        try:
            rt.initialize_database_schema(self.db)
        finally:
            self.db.set_trace_callback(None)

        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM sync_bindings "
                "WHERE chat_id='orphan-chat' AND session_id='missing-session'"
            ).fetchone()
        )
        structural = [
            sql for sql in traced
            if sql.lstrip().upper().startswith(
                ("CREATE TABLE", "CREATE INDEX", "CREATE TRIGGER", "ALTER TABLE")
            )
        ]
        self.assertEqual(structural, [], structural)
```

Because the database is already fully migrated in `setUp`, this test proves both that recurring orphan cleanup still executes and that the compatibility wrapper no longer owns DDL.

- [ ] **Step 3: Run the two focused test modules and verify RED**

Run:

```bash
python -m unittest tests.test_migrations tests.test_sync_audit -v
```

Expected: FAIL because migration 004 is absent and `sync_safety.initialize_database_schema()` still executes `CREATE TRIGGER`.

- [ ] **Step 4: Add migration 004**

In `bridge/schema.py` add:

```python
def _migration_004_sync_lifecycle_trigger(
    db: sqlite3.Connection,
) -> None:
    db.execute(
        """CREATE TRIGGER IF NOT EXISTS sessions_delete_sync_binding
        AFTER DELETE ON sessions
        FOR EACH ROW
        BEGIN
            DELETE FROM sync_bindings
            WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        END"""
    )
```

Update:

```python
SCHEMA_MIGRATIONS = (
    _Migration(1, "core_baseline", _migration_001_core_baseline),
    _Migration(2, "scene_state", _migration_002_scene_state),
    _Migration(3, "director_goals", _migration_003_director_goals),
    _Migration(
        4,
        "sync_lifecycle_trigger",
        _migration_004_sync_lifecycle_trigger,
    ),
)
```

- [ ] **Step 5: Remove structural DDL from the sync compatibility wrapper**

Replace `sync_safety.initialize_database_schema()` with:

```python
def initialize_database_schema(db: sqlite3.Connection) -> None:
    """Run normal migrations, then remove orphaned sync bindings."""
    _ORIGINAL_SYNC_INITIALIZE_DATABASE_SCHEMA(db)
    db.execute(
        "DELETE FROM sync_bindings "
        "WHERE NOT EXISTS ("
        "SELECT 1 FROM sessions "
        "WHERE sessions.chat_id=sync_bindings.chat_id "
        "AND sessions.session_id=sync_bindings.session_id)"
    )
    db.commit()
```

Do not change the existing compatibility override registration/allowlist in this phase.

- [ ] **Step 6: Run migration and sync tests**

Run:

```bash
python -m unittest   tests.test_migrations   tests.test_sync_audit   tests.test_sync_phase3   tests.test_session_delete -v
```

Expected: PASS.

- [ ] **Step 7: Verify sync safety contains no structural DDL**

Run:

```bash
grep -nE "CREATE (TABLE|INDEX|TRIGGER)|ALTER TABLE" bridge/sync_safety.py
```

Expected: no matches.

- [ ] **Step 8: Commit Task 4**

```bash
git add bridge/schema.py bridge/sync_safety.py tests/test_migrations.py tests/test_sync_audit.py
git commit -m "refactor: migrate sync lifecycle trigger"
```

---

### Task 5: Prove current-database adoption, runtime compatibility, and full Phase 2 invariants

**Files:**
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_database_optimization.py`
- Verify unchanged: `tests/test_runtime_loader.py`
- Verify: all Phase 2 production/test files

**Interfaces:**
- Consumes: complete `SCHEMA_MIGRATIONS` 001–004
- Produces: final integration evidence only

- [ ] **Step 1: Add a current-pre-ledger adoption test**

Append to `ApplicationSchemaMigrationTests`:

```python
    def test_current_schema_without_ledger_adopts_all_versions_without_data_loss(self):
        for migration in schema.SCHEMA_MIGRATIONS:
            migration.apply(self.db)
        self.db.execute(
            "INSERT INTO messages("
            "chat_id,session_id,role,content,telegram_message_ids,created_at"
            ") VALUES('chat','current','user','keep message','[]',1.0)"
        )
        self.db.execute(
            "INSERT INTO scene_states("
            "chat_id,session_id,state_json,updated_through_rowid,updated_at"
            ") VALUES('chat','current','{\"location\":\"Cafe\"}',3,1.0)"
        )
        self.db.execute(
            "INSERT INTO director_goals("
            "chat_id,session_id,goal,updated_at"
            ") VALUES('chat','current','Keep this goal.',1.0)"
        )
        self.db.commit()

        schema.initialize_database_schema(self.db)

        self.assertEqual(
            self.db.execute(
                "SELECT version,name FROM schema_migrations ORDER BY version"
            ).fetchall(),
            [
                (1, "core_baseline"),
                (2, "scene_state"),
                (3, "director_goals"),
                (4, "sync_lifecycle_trigger"),
            ],
        )
        self.assertEqual(
            self.db.execute(
                "SELECT content FROM messages WHERE session_id='current'"
            ).fetchone()[0],
            "keep message",
        )
        self.assertEqual(
            self.db.execute(
                "SELECT state_json FROM scene_states WHERE session_id='current'"
            ).fetchone()[0],
            '{"location":"Cafe"}',
        )
        self.assertEqual(
            self.db.execute(
                "SELECT goal FROM director_goals WHERE session_id='current'"
            ).fetchone()[0],
            "Keep this goal.",
        )
```

This fixture intentionally applies the current schema functions directly without ledger rows, then lets the runner adopt the database.

- [ ] **Step 2: Add an explicit scheduler once-per-process regression if not already covered**

In `tests/test_database_optimization.py`, add:

```python
    def test_db_connect_runs_database_wide_schema_setup_once_per_process(self):
        first = self.db
        self.assertTrue(rt._DB_SCHEMA_READY)

        traced = []
        second = rt._lightweight_db_connect(timeout=5.0)
        second.set_trace_callback(traced.append)
        try:
            self.assertEqual(
                second.execute(
                    "SELECT COUNT(*) FROM schema_migrations"
                ).fetchone()[0],
                4,
            )
        finally:
            second.set_trace_callback(None)
            second.close()

        self.assertFalse(
            any(
                sql.lstrip().upper().startswith(
                    ("CREATE TABLE", "CREATE INDEX", "CREATE TRIGGER", "ALTER TABLE")
                )
                for sql in traced
            )
        )
```

Do not replace the existing WAL/lightweight-connection tests; this supplements them.

- [ ] **Step 3: Run Phase 2 focused regression set**

Run:

```bash
python -m unittest   tests.test_migrations   tests.test_database_optimization   tests.test_scene_state   tests.test_director_goals   tests.test_group_director   tests.test_sync_audit   tests.test_sync_phase3   tests.test_session_delete   tests.test_sqlite_contention   tests.test_runtime_loader -v
```

Expected: PASS.

- [ ] **Step 4: Compile all Python sources**

Run:

```bash
python -m compileall bridge tests
```

Expected: exit 0.

- [ ] **Step 5: Run the complete unittest suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 6: Run pytest exactly as CI does**

Run:

```bash
pytest
```

Expected: all tests PASS.

- [ ] **Step 7: Run dependency consistency/audit checks**

Run the repository CI-equivalent dependency checks:

```bash
python -m pip check
pip-audit
```

Expected: exit 0 in the repository's locked CI environment.

- [ ] **Step 8: Verify structural-DDL ownership**

Run:

```bash
grep -R "ensure_scene_state_schema\|ensure_director_goal_schema" -n bridge tests
grep -nE "CREATE (TABLE|INDEX|TRIGGER)|ALTER TABLE"   bridge/scene_state.py   bridge/director_goals.py   bridge/sync_safety.py
```

Expected: no matches.

Then verify application migration declarations are centralized:

```bash
grep -R "schema_migrations" -n bridge
```

Expected: ledger mechanics in `bridge/migrations.py` and migration wiring in `bridge/schema.py`; no request-time feature ownership.

- [ ] **Step 9: Verify no Phase 3+ scope leaked into the branch**

Run:

```bash
git diff --name-only main...HEAD
```

Expected implementation files:

```text
bridge/migrations.py
bridge/schema.py
bridge/scene_state.py
bridge/director_goals.py
bridge/sync_safety.py
tests/test_migrations.py
tests/test_scene_state.py
tests/test_sync_audit.py
tests/test_database_optimization.py
docs/superpowers/specs/2026-09-19-versioned-database-migrations-design.md
```

The plan document may exist only on the planning branch and does not need to ship in the implementation PR.

Do not accept unrelated repository/service/transaction refactors.

- [ ] **Step 10: Compare every acceptance criterion against code/tests**

Confirm explicitly:

- ledger records versions/names/timestamps
- missing suffix only
- history mismatch/future/gap failure
- rollback/no record on failed migration
- empty bootstrap
- current pre-ledger adoption
- representative legacy upgrade
- Scene State no request-time DDL
- Director Goals no request-time DDL
- sync trigger migration-owned
- sync orphan cleanup still recurring
- no Memory Curator migration
- scheduler once-per-process initialization preserved
- WAL/lightweight behavior unchanged
- no new runtime override/capture chain
- no Phase 3 work

- [ ] **Step 11: Commit the Task 5 integration tests**

```bash
git add tests/test_migrations.py tests/test_database_optimization.py
git commit -m "test: verify versioned migration compatibility"
```

- [ ] **Step 12: Request whole-branch code review**

Use Superpowers `requesting-code-review` and focus the review on:

- whether migration 001 accidentally performs recurring maintenance
- whether any current legacy column-upgrade behavior was lost
- whether migration history validation can mutate before rejecting bad history
- whether SQLite DDL rollback behaves as the tests claim
- whether a current database with feature tables but no ledger is adopted safely
- whether any Scene/Director/Sync request path still creates schema
- whether the scheduler safety lock and lightweight connection behavior are unchanged
- whether any future schema change could bypass `SCHEMA_MIGRATIONS`

For every Important/Critical finding, add a failing regression test first, verify RED, apply the minimal fix, and verify GREEN before continuing.

- [ ] **Step 13: Run a fresh final verification after the review gate**

Run:

```bash
python -m compileall bridge tests
python -m unittest discover -s tests -v
pytest
python -m pip check
pip-audit
```

Expected: all commands exit 0 on the exact final head.

- [ ] **Step 14: Open the Phase 2 implementation PR only after the final head is green**

The PR description must link:

- `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`
- `docs/superpowers/specs/2026-09-19-versioned-database-migrations-design.md`

and state that this implements Phase 2 only.

The PR should call out:

- migrations 001–004
- current/legacy/empty database coverage
- request-time no-DDL invariant
- preserved scheduler/WAL behavior
- recurring maintenance kept outside numbered migrations
- no Phase 3 transaction/repository changes

# Phase 7B1 — Persistence Foundation Ordinary-Import Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `bridge.database` an ordinary-only persistence module with canonical configuration/state ownership while preserving the existing public database API through `bridge.runtime`.

**Architecture:** Extract the narrow persistence-related startup defaults into ordinary-only `bridge.config`, then make `bridge.database` import all dependencies explicitly and own its lock/connection-gate state exactly once. After standalone importability and reliability tests are green, explicitly re-export the public database API from `bridge.runtime`, update the one remaining private production dependency in `main.py`, and permanently remove `database.py` from the exec loader.

**Tech Stack:** Python 3.11, stdlib SQLite/threading/pathlib/unittest/subprocess, existing `DatabaseConnectionGate`, schema/migration modules, staged compatibility runtime, GitHub Actions CI.

**Spec:** `docs/superpowers/specs/2026-09-21-phase-7b1-persistence-import-boundary-design.md`

## Global Constraints

- Baseline upstream `main` is `8adc4e47524602016b9e884162ef12850b999b28`.
- Preserve database schema contents and migration versions exactly.
- Preserve SQLite WAL, pragma, timeout, transaction, write-serialization, rollback, and maintenance behavior.
- Preserve JobService enqueue/recovery/transition durability.
- Preserve operation, failed-turn, panel-session, task-model, generation-setting/preset, Sync-binding, and transcript-hash semantics.
- Do not split `database.py` in this phase.
- Do not redesign `composition.BridgeConfig`.
- Do not migrate `common.py`, `cards.py`, `memory.py`, `rag.py`, or `main.py` wholesale.
- Do not add `config.py` or `database.py` to any runtime stage after retirement.
- Do not add a service locator, compatibility dictionary, runtime namespace lookup, wildcard facade export, or `globals().get(...)` dependency-preservation pattern.
- Do not preserve obsolete private database state through `bridge.runtime`.
- Keep the relative order of all remaining legacy runtime files unchanged.
- Do not merge the implementation PR automatically.

## Review Focus

1. **Canonical default DB path is rebound during tests** — `database.db_connect()` and `database.run_database_maintenance()` must both honor the current `bridge.config.DB_FILE`, while explicit `db_connect(path)` still wins.
2. **Public facade silently misses a persistence helper** — every public function defined by `bridge.database` must be explicitly exported by `bridge.runtime` and be object-identical to the canonical function.
3. **A second write lock or connection gate is accidentally created** — runtime import order must never produce duplicate `_DB_WRITE_LOCK` or `_DB_CONNECTION_GATE` state.
4. **Legacy tests continue mutating dead exec-era state** — no test may depend on `rt._DB_SCHEMA_LOCK`, `rt._DB_SCHEMA_READY`, or private database implementation state through `bridge.runtime`.
5. **Mutable defaults drift between legacy and ordinary worlds** — `GENERATION_DEFAULTS` and `REASONING_LEVELS` must remain the same objects when re-exported through `common.py`/runtime compatibility.

## File Structure

- Create `bridge/config.py` — ordinary-only canonical owner for the seven approved persistence-related startup/default values.
- Modify `bridge/common.py` — import/re-export only those seven values from `bridge.config`; leave unrelated config and process state in place.
- Modify `bridge/database.py` — add explicit dependencies, use dynamic `bridge.config`, make lock/gate state canonical, remove obsolete schema-ready fixture state.
- Modify `bridge/runtime.py` — explicitly re-export all current public `bridge.database` functions.
- Modify `bridge/runtime_loader.py` — remove `database.py` from the core stage without reordering any remaining module.
- Modify `bridge/main.py` — explicitly source `_lightweight_db_connect` from canonical `bridge.database`.
- Create `tests/test_persistence_import_island.py` — config/database standalone imports, facade completeness/identity, import-order, state-ownership, default-path, private-state-retirement, and loader guards.
- Modify `tests/test_database_optimization.py` — patch canonical config and inspect private database details through `bridge.database`.
- Modify `tests/test_sqlite_contention.py` — use canonical config/default path and canonical serialized connection type; remove dead schema-ready fixture use.
- Modify `tests/test_repository_transactions.py` — replace runtime DB-path/dead-state fixtures with canonical config ownership.
- Modify `tests/test_task_model_routing.py` — replace runtime DB-path/dead-state fixtures with canonical config ownership.
- Modify `tests/test_sync_audit.py` — replace runtime DB-path/dead-state fixtures with canonical config ownership.
- Modify `tests/test_runtime_loader.py` — permanently guard database/config stage retirement and preserved legacy module order.
- Reuse existing JobService, migration, operation-recovery, Sync, repository, and contention suites as behavioral regression gates.

---

### Task 1: Extract the narrow canonical persistence configuration

**Files:**
- Create: `bridge/config.py`
- Modify: `bridge/common.py`
- Create: `tests/test_persistence_import_island.py`

**Interfaces:**
- Produces module values:
  - `bridge.config.BRIDGE_HOME: Path`
  - `bridge.config.DB_FILE: Path`
  - `bridge.config.DEFAULT_MODEL: str`
  - `bridge.config.DEFAULT_MAX_TOKENS: int`
  - `bridge.config.PENDING_SETTINGS_TTL_SECONDS: int`
  - `bridge.config.REASONING_LEVELS: dict[str, int]`
  - `bridge.config.GENERATION_DEFAULTS: dict[str, object]`
- Preserves the same names through `common.py` compatibility.
- Establishes that importing `bridge.config` does not import runtime/common/database.

- [ ] **Step 1: Write the failing config import and value tests**

Create `tests/test_persistence_import_island.py`:

```python
"""Phase 7B1 persistence ordinary-import boundary tests."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).parents[1]


class PersistenceImportIslandTests(unittest.TestCase):
    def _run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_config_imports_without_runtime_common_or_database(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.config as config\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
            "assert 'bridge.database' not in sys.modules\n"
            "assert config.DEFAULT_MAX_TOKENS == 1800\n"
            "assert config.PENDING_SETTINGS_TTL_SECONDS == 600\n"
            "assert config.REASONING_LEVELS == {"
            "'none': 0, 'low': 1024, 'medium': 4096, "
            "'high': 8192, 'max': 16384}\n"
            "assert config.GENERATION_DEFAULTS['max_tokens'] == 1800\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_common_reexports_canonical_mutable_defaults(self):
        completed = self._run_python(
            "import bridge.config as config\n"
            "import bridge.common as common\n"
            "assert common.BRIDGE_HOME == config.BRIDGE_HOME\n"
            "assert common.DB_FILE == config.DB_FILE\n"
            "assert common.DEFAULT_MODEL == config.DEFAULT_MODEL\n"
            "assert common.DEFAULT_MAX_TOKENS == config.DEFAULT_MAX_TOKENS\n"
            "assert common.PENDING_SETTINGS_TTL_SECONDS == "
            "config.PENDING_SETTINGS_TTL_SECONDS\n"
            "assert common.GENERATION_DEFAULTS is config.GENERATION_DEFAULTS\n"
            "assert common.REASONING_LEVELS is config.REASONING_LEVELS\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python -m pytest   tests/test_persistence_import_island.py::PersistenceImportIslandTests::test_config_imports_without_runtime_common_or_database   tests/test_persistence_import_island.py::PersistenceImportIslandTests::test_common_reexports_canonical_mutable_defaults   -q
```

Expected: FAIL because `bridge.config` does not exist.

- [ ] **Step 3: Create `bridge/config.py` with the exact approved values**

Create:

```python
"""Canonical startup defaults shared by ordinary bridge modules."""

from pathlib import Path
import os


BRIDGE_HOME = Path(
    os.environ.get(
        "SILLYTAVERN_BRIDGE_HOME",
        Path.home() / ".local/share/sillytavern-telegram",
    )
)
DB_FILE = BRIDGE_HOME / "scripts" / "sillytavern_telegram.sqlite3"
DEFAULT_MODEL = os.environ.get("SILLYTAVERN_MODEL", "").strip()
DEFAULT_MAX_TOKENS = 1800
PENDING_SETTINGS_TTL_SECONDS = 600

REASONING_LEVELS = {
    "none": 0,
    "low": 1024,
    "medium": 4096,
    "high": 8192,
    "max": 16384,
}

GENERATION_DEFAULTS = {
    "temperature": 0.85,
    "max_tokens": DEFAULT_MAX_TOKENS,
    "top_p": 1.0,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "reasoning_budget": 0,
    "stop_sequences": "",
}
```

Do not import `bridge.common`, `bridge.database`, `bridge.runtime`, or `composition.BridgeConfig`.

- [ ] **Step 4: Make `common.py` import and re-export exactly those values**

Add near the ordinary imports:

```python
from bridge.config import (
    BRIDGE_HOME,
    DB_FILE,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    GENERATION_DEFAULTS,
    PENDING_SETTINGS_TTL_SECONDS,
    REASONING_LEVELS,
)
```

Delete the local definitions of exactly:

```text
BRIDGE_HOME
DB_FILE
DEFAULT_MODEL
DEFAULT_MAX_TOKENS
PENDING_SETTINGS_TTL_SECONDS
REASONING_LEVELS
GENERATION_DEFAULTS
```

Do not move or rewrite any other `common.py` constant.

Keep existing `common.py` expressions such as `ENV_FILE`, `SILLYTAVERN_DIR`, and `LOG_FILE` using the imported `BRIDGE_HOME`.

- [ ] **Step 5: Run the Task 1 tests**

Run:

```bash
python -m pytest tests/test_persistence_import_island.py -q
```

Expected: PASS for the two Task 1 tests.

- [ ] **Step 6: Run config/composition/runtime smoke regressions**

Run:

```bash
python -m pytest   tests/test_composition.py   tests/test_runtime_import_islands.py   tests/test_runtime_loader.py   -q
```

Expected: PASS. Phase 7A import-island invariants remain intact.

- [ ] **Step 7: Commit the canonical config extraction**

```bash
git add   bridge/config.py   bridge/common.py   tests/test_persistence_import_island.py
git commit -m "refactor: extract canonical persistence config"
```

---

### Task 2: Make `bridge.database` independently importable and retire exec-era private fixture state

**Files:**
- Modify: `bridge/database.py`
- Modify: `tests/test_persistence_import_island.py`
- Modify: `tests/test_database_optimization.py`
- Modify: `tests/test_sqlite_contention.py`
- Modify: `tests/test_repository_transactions.py`
- Modify: `tests/test_task_model_routing.py`
- Modify: `tests/test_sync_audit.py`

**Interfaces:**
- Consumes:
  - `bridge.config` module as `_config`
  - `bridge.schema.initialize_database_schema(db: sqlite3.Connection) -> None`
  - `bridge.scheduler_safety.DatabaseConnectionGate`
- Produces:
  - independently importable `bridge.database`
  - canonical `database._DB_WRITE_LOCK`
  - canonical `database._DB_CONNECTION_GATE`
  - no `_DB_SCHEMA_LOCK`, `_DB_SCHEMA_READY`, or `_DB_SCHEMA_READY_PATHS`
- Keeps all public database function signatures unchanged.

- [ ] **Step 1: Add failing standalone/state-retirement tests**

Append to `PersistenceImportIslandTests`:

```python
    def test_database_imports_without_runtime_or_common(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.database as database\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
            "assert database._DB_WRITE_LOCK is not None\n"
            "assert database._DB_CONNECTION_GATE is not None\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_database_has_no_obsolete_schema_ready_fixture_state(self):
        import bridge.database as database

        self.assertFalse(hasattr(database, "_DB_SCHEMA_LOCK"))
        self.assertFalse(hasattr(database, "_DB_SCHEMA_READY"))
        self.assertFalse(hasattr(database, "_DB_SCHEMA_READY_PATHS"))

    def test_database_source_has_no_exec_state_preservation_or_runtime_dependency(self):
        source = (
            REPO_ROOT / "bridge" / "database.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn('globals().get("_DB_WRITE_LOCK")', source)
        self.assertNotIn("import bridge.runtime", source)
        self.assertNotIn("from bridge.runtime import", source)
        self.assertNotIn("import bridge.common", source)
        self.assertNotIn("from bridge.common import", source)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python -m pytest   tests/test_persistence_import_island.py::PersistenceImportIslandTests::test_database_imports_without_runtime_or_common   tests/test_persistence_import_island.py::PersistenceImportIslandTests::test_database_has_no_obsolete_schema_ready_fixture_state   tests/test_persistence_import_island.py::PersistenceImportIslandTests::test_database_source_has_no_exec_state_preservation_or_runtime_dependency   -q
```

Expected: FAIL because current `database.py` relies on injected stdlib/config/schema globals, preserves `_DB_WRITE_LOCK` through `globals()`, and still defines obsolete `_DB_SCHEMA_*` fixture state.

- [ ] **Step 3: Add all explicit imports to `database.py`**

The import block should become structurally:

```python
from contextlib import contextmanager as _contextmanager
from pathlib import Path
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time

from bridge import config as _config
from bridge.scheduler_safety import (
    DatabaseConnectionGate as _DatabaseConnectionGate,
)
from bridge.schema import initialize_database_schema
```

Keep the existing scheduler-safety alias.

- [ ] **Step 4: Make write-lock ownership canonical**

Replace:

```python
_DB_WRITE_LOCK = globals().get("_DB_WRITE_LOCK") or threading.RLock()
```

with:

```python
_DB_WRITE_LOCK = threading.RLock()
```

Do not add any reload/recovery fallback.

- [ ] **Step 5: Replace bare configuration lookups with dynamic canonical config access**

Perform these exact semantic replacements inside `database.py`:

```text
DB_FILE
    -> _config.DB_FILE when resolving the default DB path

DEFAULT_MODEL
    -> _config.DEFAULT_MODEL

PENDING_SETTINGS_TTL_SECONDS
    -> _config.PENDING_SETTINGS_TTL_SECONDS

GENERATION_DEFAULTS
    -> _config.GENERATION_DEFAULTS

REASONING_LEVELS
    -> _config.REASONING_LEVELS
```

Specifically, make:

```python
def _database_path(database_path: Path | None = None) -> Path:
    path = (
        Path(database_path)
        if database_path is not None
        else _config.DB_FILE
    )
    return path.expanduser().resolve()
```

and make `run_database_maintenance()` resolve once through:

```python
path = _database_path()
path.parent.mkdir(parents=True, exist_ok=True)
db = sqlite3.connect(
    path,
    timeout=timeout,
    isolation_level=None,
)
```

Do not read `DB_FILE` directly elsewhere.

Update model/settings helpers to read `_config.*` at call time, including `task_model_for_session`, model-target TTL handling, generation default reads/writes, formatting, and parsing.

- [ ] **Step 6: Delete obsolete schema-ready compatibility state**

Delete:

```python
# Deprecated test-fixture compatibility only...
_DB_SCHEMA_LOCK = threading.RLock()
_DB_SCHEMA_READY = False
_DB_SCHEMA_READY_PATHS: set[Path] = set()
```

Do not replace these names anywhere.

- [ ] **Step 7: Retarget database optimization tests to canonical owners**

At the top of `tests/test_database_optimization.py`, keep `bridge.runtime as rt` for public compatibility behavior and add:

```python
import bridge.config as config
import bridge.database as database
```

Replace the fixture with canonical config ownership:

```python
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = database.db_connect()

    def tearDown(self):
        self.db.close()
        config.DB_FILE = self.original_db
        self.tmp.cleanup()
```

Retarget private implementation checks:

```text
rt._lightweight_db_connect
    -> database._lightweight_db_connect

rt._DB_PRIMARY_CACHE_KIB
    -> database._DB_PRIMARY_CACHE_KIB

rt._DB_WORKER_CACHE_KIB
    -> database._DB_WORKER_CACHE_KIB

rt._load_optional_vector_extension
    -> database._load_optional_vector_extension

rt._DB_CONNECTION_GATE
    -> database._DB_CONNECTION_GATE
```

Where the lightweight-connect test changes the default path, change `config.DB_FILE` instead of `rt.DB_FILE`.

For the gate readiness assertion use:

```python
self.assertIn(
    Path(config.DB_FILE).expanduser().resolve(),
    database._DB_CONNECTION_GATE._ready_paths,
)
```

- [ ] **Step 8: Retarget SQLite contention fixture/private type**

At the top of `tests/test_sqlite_contention.py`, add:

```python
import bridge.config as config
import bridge.database as database
```

Use:

```python
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_file = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = rt.db_connect()
        rt.set_db_connection_context(self.db)

    def tearDown(self):
        rt.set_db_connection_context(None)
        self.db.close()
        config.DB_FILE = self.old_db_file
        self.tmp.cleanup()
```

Delete all `_DB_SCHEMA_LOCK` and `_DB_SCHEMA_READY` fixture code.

Replace:

```python
rt._SerializedSQLiteConnection
```

with:

```python
database._SerializedSQLiteConnection
```

Keep tests that intentionally monkeypatch public `rt.db_connect` unchanged; they validate late-bound behavior in still-legacy runtime modules rather than database ownership.

- [ ] **Step 9: Retarget repository/task-model/Sync DB path fixtures**

For each of:

```text
tests/test_repository_transactions.py
tests/test_task_model_routing.py
tests/test_sync_audit.py
```

add:

```python
import bridge.config as config
```

Replace fixture ownership:

```text
rt.DB_FILE
    -> config.DB_FILE

rt._DB_SCHEMA_LOCK / rt._DB_SCHEMA_READY resets
    -> delete entirely
```

Where a test opens an observer connection against the configured DB, use:

```python
sqlite3.connect(config.DB_FILE)
```

Do not expose dead private state from `bridge.database` merely to keep the old fixtures.

- [ ] **Step 10: Add default-path and explicit-path Review Focus tests**

Append:

```python
    def test_database_default_path_follows_canonical_config(self):
        import bridge.config as config
        import bridge.database as database

        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "default.sqlite3"
            with patch.object(config, "DB_FILE", expected):
                db = database.db_connect()
                try:
                    self.assertEqual(
                        Path(
                            db.execute("PRAGMA database_list").fetchone()[2]
                        ).resolve(),
                        expected.resolve(),
                    )
                finally:
                    db.close()

    def test_database_explicit_path_overrides_canonical_default(self):
        import bridge.config as config
        import bridge.database as database

        with tempfile.TemporaryDirectory() as directory:
            configured = Path(directory) / "configured.sqlite3"
            explicit = Path(directory) / "explicit.sqlite3"
            with patch.object(config, "DB_FILE", configured):
                db = database.db_connect(explicit)
                try:
                    self.assertEqual(
                        Path(
                            db.execute("PRAGMA database_list").fetchone()[2]
                        ).resolve(),
                        explicit.resolve(),
                    )
                finally:
                    db.close()

    def test_task_model_default_reads_current_canonical_config(self):
        import bridge.config as config
        import bridge.database as database

        class EmptyMetaDb:
            def execute(self, *_args, **_kwargs):
                class Cursor:
                    def fetchone(self):
                        return None
                return Cursor()

        session = {"session_id": "s", "model_id": ""}
        with patch.object(config, "DEFAULT_MODEL", "patched::model"):
            self.assertEqual(
                database.task_model_for_session(
                    EmptyMetaDb(),
                    "chat",
                    session,
                    "summary",
                ),
                "patched::model",
            )
```

- [ ] **Step 11: Run the standalone and persistence-focused suites**

Run:

```bash
python -m pytest   tests/test_persistence_import_island.py   tests/test_database_optimization.py   tests/test_sqlite_contention.py   tests/test_repository_transactions.py   tests/test_task_model_routing.py   tests/test_sync_audit.py   -q
```

Expected: PASS.

- [ ] **Step 12: Run schema/JobService/operation regression suites before loader retirement**

Run:

```bash
python -m pytest   tests/test_migrations.py   tests/test_job_service.py   tests/test_job_service_workers.py   tests/test_operation_recovery.py   -q
```

Expected: PASS.

- [ ] **Step 13: Commit the standalone database/state cleanup**

```bash
git add   bridge/database.py   tests/test_persistence_import_island.py   tests/test_database_optimization.py   tests/test_sqlite_contention.py   tests/test_repository_transactions.py   tests/test_task_model_routing.py   tests/test_sync_audit.py
git commit -m "refactor: make database an ordinary module"
```

---

### Task 3: Cut persistence over to the explicit runtime facade and retire loader ownership

**Files:**
- Modify: `tests/test_persistence_import_island.py`
- Modify: `tests/test_runtime_loader.py`
- Modify: `bridge/runtime.py`
- Modify: `bridge/runtime_loader.py`
- Modify: `bridge/main.py`

**Interfaces:**
- Consumes canonical `bridge.database`.
- Produces explicit `bridge.runtime` aliases for all public database functions.
- Produces `main.py` dependency on `bridge.database._lightweight_db_connect`.
- Removes `database.py` from all runtime stages.

- [ ] **Step 1: Define the expected public database facade set and write failing identity tests**

Add this constant to `tests/test_persistence_import_island.py`:

```python
DATABASE_PUBLIC_FUNCTIONS = (
    "run_write_txn",
    "write_transaction",
    "optimize_database",
    "run_database_maintenance",
    "db_connect",
    "get_meta",
    "set_meta",
    "record_failed_turn",
    "latest_failed_turn",
    "clear_failed_turn",
    "committed_assistant_for_message",
    "bind_panel_session",
    "panel_session_for_message",
    "panel_owner_for_message",
    "operation_phase",
    "set_operation_phase",
    "begin_operation",
    "operation_was_applied",
    "record_operation",
    "enqueue_job",
    "job_actor_id",
    "mark_job_scheduled",
    "mark_job_running",
    "finish_job",
    "recover_jobs",
    "task_model_key",
    "task_model_for_session",
    "set_task_model",
    "model_target_selection_key",
    "set_model_target_selection",
    "get_model_target_selection",
    "clear_model_target_selection",
    "get_generation_settings",
    "update_generation_settings",
    "preset_names",
    "save_generation_preset",
    "load_generation_preset",
    "delete_generation_preset",
    "format_generation_settings",
    "parse_generation_setting",
    "sync_transcript_hash",
    "ensure_sync_binding",
)
```

Append:

```python
    def test_runtime_facade_exports_complete_canonical_database_api(self):
        import bridge.database as database
        import bridge.runtime as rt

        actual_public_functions = {
            name
            for name, value in vars(database).items()
            if not name.startswith("_") and callable(value)
            and getattr(value, "__module__", None) == "bridge.database"
        }
        self.assertEqual(
            actual_public_functions,
            set(DATABASE_PUBLIC_FUNCTIONS),
        )
        for name in DATABASE_PUBLIC_FUNCTIONS:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(rt, name),
                    getattr(database, name),
                )

    def test_database_and_config_are_absent_from_runtime_stages(self):
        from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

        loaded = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertTrue(
            {"database.py", "config.py"}.isdisjoint(loaded)
        )

    def test_runtime_load_report_excludes_database_and_config(self):
        import bridge.runtime as rt

        loaded = {
            entry["module"]
            for entry in rt.RUNTIME_LOAD_REPORT
        }
        self.assertTrue(
            {"database.py", "config.py"}.isdisjoint(loaded)
        )
```

- [ ] **Step 2: Run the Task 3 tests and verify RED**

Run:

```bash
python -m pytest   tests/test_persistence_import_island.py::PersistenceImportIslandTests::test_runtime_facade_exports_complete_canonical_database_api   tests/test_persistence_import_island.py::PersistenceImportIslandTests::test_database_and_config_are_absent_from_runtime_stages   tests/test_persistence_import_island.py::PersistenceImportIslandTests::test_runtime_load_report_excludes_database_and_config   -q
```

Expected: FAIL because `database.py` is still exec-loaded and runtime facade functions are exec-created objects.

- [ ] **Step 3: Explicitly import all public database functions into `runtime.py` before legacy loading**

Add:

```python
from bridge.database import (
    begin_operation,
    bind_panel_session,
    clear_failed_turn,
    clear_model_target_selection,
    committed_assistant_for_message,
    db_connect,
    delete_generation_preset,
    enqueue_job,
    ensure_sync_binding,
    finish_job,
    format_generation_settings,
    get_generation_settings,
    get_meta,
    get_model_target_selection,
    job_actor_id,
    latest_failed_turn,
    load_generation_preset,
    mark_job_running,
    mark_job_scheduled,
    model_target_selection_key,
    operation_phase,
    operation_was_applied,
    optimize_database,
    panel_owner_for_message,
    panel_session_for_message,
    parse_generation_setting,
    preset_names,
    record_failed_turn,
    record_operation,
    recover_jobs,
    run_database_maintenance,
    run_write_txn,
    save_generation_preset,
    set_meta,
    set_model_target_selection,
    set_operation_phase,
    set_task_model,
    sync_transcript_hash,
    task_model_for_session,
    task_model_key,
    update_generation_settings,
    write_transaction,
)
```

Keep these names public.

Do not import private database implementation names into `runtime.py`.

- [ ] **Step 4: Make `main.py` explicitly reference canonical lightweight DB ownership**

Add:

```python
from bridge import database as _database
```

Change:

```python
_DURABLE_WORKER_GUARD = _DurableWorkerGuard(
    _lightweight_db_connect
)
```

to:

```python
_DURABLE_WORKER_GUARD = _DurableWorkerGuard(
    _database._lightweight_db_connect
)
```

Do not otherwise migrate `main.py`.

- [ ] **Step 5: Remove `database.py` from the core runtime stage**

In `bridge/runtime_loader.py`, change the start of the core tuple from:

```python
"common.py", "cards.py", "database.py", "memory.py",
```

to:

```python
"common.py", "cards.py", "memory.py",
```

Do not add `config.py`.

Do not reorder any other file.

- [ ] **Step 6: Add permanent loader-order guard**

In `tests/test_runtime_loader.py`, add:

```python
    def test_phase_7b1_persistence_island_is_never_exec_loaded(self):
        loaded_modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertTrue(
            {"database.py", "config.py"}.isdisjoint(loaded_modules)
        )

    def test_phase_7b1_preserves_remaining_core_order(self):
        core = next(
            stage
            for stage in DEFAULT_RUNTIME_STAGES
            if stage.name == "core"
        )
        self.assertEqual(
            core.modules,
            (
                "common.py", "cards.py", "memory.py",
                "rag.py", "groups.py", "telegram.py",
                "persona_delete_panel.py", "language.py",
                "greetings.py", "help_details.py", "help.py",
                "input_flows.py", "catalog.py", "update.py",
                "image_generation.py", "expressions.py",
                "media.py", "generation.py", "commands.py",
                "status_panels.py", "command_routes.py",
                "message_commands.py", "callbacks.py",
                "panel_callback_routes.py", "main.py",
            ),
        )
```

This intentionally freezes the remaining order for this phase.

- [ ] **Step 7: Run focused cutover suites**

Run:

```bash
python -m pytest   tests/test_persistence_import_island.py   tests/test_runtime_loader.py   tests/test_database_optimization.py   tests/test_sqlite_contention.py   -q
```

Expected: PASS.

- [ ] **Step 8: Run JobService/Sync/main-composition regressions through the new topology**

Run:

```bash
python -m pytest   tests/test_job_service.py   tests/test_job_service_workers.py   tests/test_sync_audit.py   tests/test_composition.py   -q
```

Expected: PASS.

- [ ] **Step 9: Commit the persistence facade cutover**

```bash
git add   bridge/runtime.py   bridge/runtime_loader.py   bridge/main.py   tests/test_persistence_import_island.py   tests/test_runtime_loader.py
git commit -m "refactor: retire database from shared runtime loading"
```

---

### Task 4: Prove single ownership, import-order independence, and full reliability

**Files:**
- Modify: `tests/test_persistence_import_island.py`
- No production changes expected.

**Interfaces:**
- Consumes the canonical config/database/facade topology from Tasks 1–3.
- Produces permanent architecture guards and exact-head verification evidence.

- [ ] **Step 1: Add two-way import-order tests**

Append:

```python
    def test_database_before_runtime_keeps_canonical_identity(self):
        completed = self._run_python(
            "import bridge.database as database\n"
            "write_lock = database._DB_WRITE_LOCK\n"
            "connection_gate = database._DB_CONNECTION_GATE\n"
            "import bridge.runtime as rt\n"
            "assert rt.db_connect is database.db_connect\n"
            "assert rt.run_write_txn is database.run_write_txn\n"
            "assert rt.enqueue_job is database.enqueue_job\n"
            "assert database._DB_WRITE_LOCK is write_lock\n"
            "assert database._DB_CONNECTION_GATE is connection_gate\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_runtime_before_database_keeps_canonical_identity(self):
        completed = self._run_python(
            "import bridge.runtime as rt\n"
            "import bridge.database as database\n"
            "assert rt.db_connect is database.db_connect\n"
            "assert rt.run_write_txn is database.run_write_txn\n"
            "assert rt.enqueue_job is database.enqueue_job\n"
            "assert rt.ensure_sync_binding is database.ensure_sync_binding\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
```

- [ ] **Step 2: Add exact single-lock and single-gate ownership tests**

Append:

```python
    def test_runtime_database_functions_resolve_canonical_write_lock(self):
        import bridge.database as database
        import bridge.runtime as rt

        self.assertIs(rt.run_write_txn, database.run_write_txn)
        self.assertIs(
            database.run_write_txn.__globals__["_DB_WRITE_LOCK"],
            database._DB_WRITE_LOCK,
        )
        self.assertFalse(hasattr(rt, "_DB_WRITE_LOCK"))

    def test_database_connection_gate_has_one_canonical_owner(self):
        import bridge.database as database
        import bridge.runtime as rt

        self.assertIs(
            database.db_connect.__globals__["_DB_CONNECTION_GATE"],
            database._DB_CONNECTION_GATE,
        )
        self.assertFalse(hasattr(rt, "_DB_CONNECTION_GATE"))
```

- [ ] **Step 3: Add private-runtime-retirement and mutable-default identity tests**

Append:

```python
    def test_runtime_does_not_republish_database_private_state(self):
        import bridge.runtime as rt

        for name in (
            "_DB_WRITE_LOCK",
            "_DB_CONNECTION_GATE",
            "_DB_SCHEMA_LOCK",
            "_DB_SCHEMA_READY",
            "_DB_SCHEMA_READY_PATHS",
            "_DB_PRIMARY_CACHE_KIB",
            "_DB_WORKER_CACHE_KIB",
            "_DB_PRIMARY_MMAP_BYTES",
            "_DB_WORKER_MMAP_BYTES",
            "_lightweight_db_connect",
            "_SerializedSQLiteConnection",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(rt, name))

    def test_runtime_mutable_defaults_share_canonical_config_objects(self):
        import bridge.config as config
        import bridge.runtime as rt

        self.assertIs(
            rt.GENERATION_DEFAULTS,
            config.GENERATION_DEFAULTS,
        )
        self.assertIs(
            rt.REASONING_LEVELS,
            config.REASONING_LEVELS,
        )
```

- [ ] **Step 4: Add maintenance default-path proof**

Append a test that patches `config.DB_FILE` and exercises `run_database_maintenance()` against that exact file:

```python
    def test_database_maintenance_uses_current_canonical_default_path(self):
        import bridge.config as config
        import bridge.database as database

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "maintenance.sqlite3"
            with patch.object(config, "DB_FILE", path):
                db = database.db_connect()
                try:
                    db.execute(
                        "CREATE TABLE phase7b1_churn("
                        "id INTEGER PRIMARY KEY, payload TEXT)"
                    )
                    db.executemany(
                        "INSERT INTO phase7b1_churn(payload) VALUES(?)",
                        [("x" * 2000,) for _ in range(200)],
                    )
                    db.commit()
                    db.execute("DELETE FROM phase7b1_churn")
                    db.commit()
                finally:
                    db.close()

                database.run_database_maintenance(
                    vacuum_freelist_threshold=1,
                )
                self.assertTrue(path.is_file())
```

The test's purpose is path ownership; existing optimization tests remain authoritative for reclamation semantics.

- [ ] **Step 5: Run the complete architecture guard suite**

Run:

```bash
python -m pytest   tests/test_persistence_import_island.py   tests/test_runtime_import_islands.py   tests/test_runtime_loader.py   -q
```

Expected: PASS.

- [ ] **Step 6: Run all persistence/reliability regression suites**

Run:

```bash
python -m pytest   tests/test_database_optimization.py   tests/test_sqlite_contention.py   tests/test_repository_transactions.py   tests/test_job_service.py   tests/test_job_service_workers.py   tests/test_migrations.py   tests/test_sync_audit.py   tests/test_task_model_routing.py   tests/test_operation_recovery.py   -q
```

Expected: PASS.

- [ ] **Step 7: Commit permanent Phase 7B1 architecture guards**

```bash
git add tests/test_persistence_import_island.py
git commit -m "test: guard Phase 7B1 persistence ownership"
```

- [ ] **Step 8: Verify current upstream drift**

Run:

```bash
git fetch upstream main
git rev-list --left-right --count upstream/main...HEAD
```

Expected: behind count is `0`. If upstream moved, inspect overlap before rebasing.

- [ ] **Step 9: Run exact source/ownership smoke checks**

Run:

```bash
python - <<'PY'
import bridge.config as config
import bridge.database as database
import bridge.runtime as rt
from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

assert rt.db_connect is database.db_connect
assert rt.run_write_txn is database.run_write_txn
assert rt.enqueue_job is database.enqueue_job
assert rt.ensure_sync_binding is database.ensure_sync_binding
assert rt.GENERATION_DEFAULTS is config.GENERATION_DEFAULTS
assert rt.REASONING_LEVELS is config.REASONING_LEVELS

loaded = {
    module
    for stage in DEFAULT_RUNTIME_STAGES
    for module in stage.modules
}
assert "database.py" not in loaded
assert "config.py" not in loaded
assert not hasattr(rt, "_DB_CONNECTION_GATE")
assert not hasattr(rt, "_DB_WRITE_LOCK")
assert not hasattr(database, "_DB_SCHEMA_LOCK")
assert not hasattr(database, "_DB_SCHEMA_READY")
assert not hasattr(database, "_DB_SCHEMA_READY_PATHS")

print("Phase 7B1 persistence ownership verified")
PY
```

Expected: prints `Phase 7B1 persistence ownership verified`.

- [ ] **Step 10: Run Python compilation exactly as CI does**

```bash
python -m compileall -q bridge tests sillytavern_telegram_bridge.py
```

Expected: exit 0.

- [ ] **Step 11: Run full unittest suite**

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 12: Run full pytest suite**

```bash
python -m pytest -q
```

Expected: all tests and subtests PASS.

- [ ] **Step 13: Validate dependencies**

```bash
python -m pip check
```

Expected: `No broken requirements found.`

- [ ] **Step 14: Audit locked dependencies**

```bash
python -m pip_audit -r requirements.lock
```

Expected: no known vulnerabilities.

- [ ] **Step 15: Review whole branch against the approved Phase 7B1 scope**

Run:

```bash
git diff --stat 8adc4e47524602016b9e884162ef12850b999b28...HEAD
git diff 8adc4e47524602016b9e884162ef12850b999b28...HEAD --   bridge/config.py   bridge/common.py   bridge/database.py   bridge/runtime.py   bridge/runtime_loader.py   bridge/main.py   tests/test_persistence_import_island.py   tests/test_database_optimization.py   tests/test_sqlite_contention.py   tests/test_repository_transactions.py   tests/test_task_model_routing.py   tests/test_sync_audit.py   tests/test_runtime_loader.py
```

Review specifically for:

- only the seven approved config values moved from `common.py`;
- no background/logging/RAG/provider/character configuration migrated;
- `database.py` has no implicit runtime/common dependency;
- every database public function is explicitly present in the facade;
- no private database implementation names are added to the facade;
- no obsolete `_DB_SCHEMA_*` compatibility state remains;
- `database.py` and `config.py` are not runtime stages;
- all remaining legacy stage order is unchanged;
- `main.py` changes only the durable-worker DB dependency;
- schema DDL/migration versions are unchanged;
- transaction, JobService, operation, Sync, and maintenance behavior are unchanged.

- [ ] **Step 16: Create/update the PR and require exact-head GitHub Actions**

Open a draft PR if needed so authoritative CI runs against the branch head.

Confirm:

- compile: success;
- full unittest: success;
- full pytest: success;
- `pip check`: success;
- `pip-audit`: success;
- branch: 0 behind current `main`;
- PR: mergeable;
- review threads/comments: none unresolved;
- whole-branch review: no Critical or Important findings.

Mark the PR ready for review only after the exact-head checks are green.

Do not merge it.

# Phase 6A Scheduler Safety Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove scheduler/durable-job late runtime overrides while preserving database initialization, bounded recovery, and transient worker SQLite requeue guarantees.

**Architecture:** Keep `database.py` as the canonical owner of database connection and recovery behavior. Convert `scheduler_safety.py` into an ordinary-import module that provides dependency-injected safety collaborators, especially `DatabaseConnectionGate` and `DurableWorkerGuard`; wire the worker guard explicitly through the existing `JobService.prepare_worker` seam and remove `scheduler_safety.py` from runtime stages.

**Tech Stack:** Python 3.11, stdlib `sqlite3`, `threading`, `pathlib`, `unittest`, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-20-phase-6a-scheduler-safety-adapter-design.md`

## Global Constraints

- Preserve every existing durable-job state and operation-idempotency semantic.
- Startup and non-startup durable recovery remain bounded to 128 rows per pass.
- First connection per normalized database path performs database-level initialization; later connections use lightweight connection setup only.
- Database initialization ordering remains auto-vacuum, WAL negotiation, optional vector extension, schema initialization.
- Transient worker requeue retries remain immediate, 250 ms, and 1 second.
- Requeue updates only jobs still in `queued` or `scheduled` state.
- BackgroundRuntime executor choice, per-chat FIFO, semaphores, backlog registration, and shutdown admission are unchanged.
- No new runtime stage, public-callable override, `_ORIGINAL_*` capture, service locator, schema change, or ORM.
- `JobService` remains the application owner of durable job lifecycle orchestration.
- `scheduler_safety.py` becomes ordinary-importable and is removed from `DEFAULT_RUNTIME_STAGES`.

## Review Focus

- First-open initialization raises: the path must remain unready and the next call must retry initialization rather than falling through to a lightweight connection.
- Two threads open the same unseen database path concurrently: exactly one initialized open occurs and the other caller receives a lightweight connection after readiness is established.
- Two distinct database paths are used in one process: each path must initialize independently with no default-path/global leakage.
- A worker raises `database is locked` after its job is already `running`: the guard must re-raise the original error without moving the durable row backwards.
- More than 128 queued durable jobs exist: one recovery pass returns the oldest 128 only; after those are scheduled, the next backlog pass reaches the remainder.

---

## File Structure

**Final responsibility in `bridge/scheduler_safety.py`:**
- Ordinary-import infrastructure helpers only.
- `DatabaseConnectionGate` serializes first initialization per normalized database path.
- `DurableWorkerGuard` wraps workers and performs bounded transient SQLite requeue attempts through an injected lightweight connection opener.
- During Tasks 1–4 only, the existing late-override code remains inside a clearly marked `if "db_connect" in globals():` compatibility block so intermediate commits preserve production behavior. Task 5 deletes that block.
- The final module has no reference to `bridge.runtime`, `bridge.main`, runtime globals, JobService, or compatibility helpers.

**Modify `bridge/database.py`:**
- Canonical normalized path resolution.
- Canonical initialized connection opener.
- Canonical lightweight connection opener.
- One module-level `DatabaseConnectionGate` instance.
- Canonical `db_connect`.
- Canonical bounded `recover_jobs`.

**Modify `bridge/main.py`:**
- Import `DurableWorkerGuard` normally.
- Construct one explicit worker-guard collaborator from the canonical lightweight DB opener.
- Pass `.prepare` into compatibility and production JobService construction.
- Keep the existing compatibility `submit_durable_chat_job` as the only definition.

**Modify `bridge/runtime_loader.py`:**
- Remove `scheduler_safety.py` from `safety_overrides`.
- Remove its allowlisted `db_connect`, `recover_jobs`, and `submit_durable_chat_job` replacements.

**Create `tests/test_scheduler_safety_adapters.py`:**
- Unit-test both ordinary-import collaborators.
- Integration-test canonical database initialization and bounded recovery against temporary SQLite files.
- Exercise all five Review Focus conditions.

**Modify `tests/test_composition.py`:**
- Update database-factory tests away from old scheduler-safety globals.
- Assert startup JobService receives the explicit worker guard.

**Modify `tests/test_runtime_loader.py`:**
- Prove scheduler safety is no longer a runtime stage.
- Prove canonical production owners are `database.py` and `main.py`.

**Modify `tests/test_job_service.py`:**
- Retain the current `prepare_worker` contract test.
- Add a recovery-path assertion that recovered submission also traverses `prepare_worker`.

---

### Task 1: Convert Scheduler Safety Into Ordinary-Import Collaborators

**Files:**
- Modify: `bridge/scheduler_safety.py`
- Create: `tests/test_scheduler_safety_adapters.py`

**Interfaces:**
- Produces: `DatabaseConnectionGate(initialize: Callable[[Path], T], open_lightweight: Callable[[Path], T])`
- Produces: `DatabaseConnectionGate.connect(database_path: Path) -> T`
- Produces: `DurableWorkerGuard(open_requeue_connection, *, sleep=time.sleep, delays=(0.0, 0.25, 1.0))`
- Produces: `DurableWorkerGuard.prepare(db, job_id: int, worker: Callable) -> Callable`
- Consumes: standard-library `sqlite3`, `threading`, `time`, `functools`, `logging`, `Path`

- [x] **Step 1: Write failing ordinary-import and connection-gate tests**

Create `tests/test_scheduler_safety_adapters.py` with these tests first:

```python
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from bridge.scheduler_safety import DatabaseConnectionGate, DurableWorkerGuard


class DatabaseConnectionGateTests(unittest.TestCase):
    def test_first_open_initializes_then_same_path_uses_lightweight_open(self):
        calls = []

        def initialize(path):
            calls.append(("initialize", path))
            return ("primary", path)

        def lightweight(path):
            calls.append(("lightweight", path))
            return ("worker", path)

        gate = DatabaseConnectionGate(initialize, lightweight)
        path = Path("relative.sqlite3").resolve()

        self.assertEqual(gate.connect(path), ("primary", path))
        self.assertEqual(gate.connect(path), ("worker", path))
        self.assertEqual(
            calls,
            [("initialize", path), ("lightweight", path)],
        )

    def test_initialization_failure_does_not_mark_path_ready(self):
        attempts = []

        def initialize(path):
            attempts.append(path)
            if len(attempts) == 1:
                raise RuntimeError("schema failed")
            return "primary"

        gate = DatabaseConnectionGate(initialize, lambda _path: "worker")
        path = Path("retry.sqlite3").resolve()

        with self.assertRaisesRegex(RuntimeError, "schema failed"):
            gate.connect(path)

        self.assertEqual(gate.connect(path), "primary")
        self.assertEqual(attempts, [path, path])

    def test_distinct_paths_initialize_independently(self):
        initialized = []
        gate = DatabaseConnectionGate(
            lambda path: initialized.append(path) or path,
            lambda path: path,
        )
        a = Path("a.sqlite3").resolve()
        b = Path("b.sqlite3").resolve()

        gate.connect(a)
        gate.connect(b)

        self.assertEqual(initialized, [a, b])

    def test_concurrent_first_open_initializes_once(self):
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        initialize_count = 0
        results = []

        def initialize(path):
            nonlocal initialize_count
            barrier.wait()
            with lock:
                initialize_count += 1
            return ("primary", path)

        gate = DatabaseConnectionGate(
            initialize,
            lambda path: ("worker", path),
        )
        path = Path("concurrent.sqlite3").resolve()

        def first():
            results.append(gate.connect(path))

        thread = threading.Thread(target=first)
        thread.start()
        barrier.wait()
        results.append(gate.connect(path))
        thread.join()

        self.assertEqual(initialize_count, 1)
        self.assertEqual(
            sorted(item[0] for item in results),
            ["primary", "worker"],
        )
```

The concurrency test intentionally coordinates the first initialized open while the second caller races for the same path.

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m unittest tests.test_scheduler_safety_adapters.DatabaseConnectionGateTests -v
```

Expected: import/attribute failure because the current `scheduler_safety.py` depends on shared runtime globals and does not define `DatabaseConnectionGate`.

- [x] **Step 3: Add the ordinary connection gate without removing the active compatibility override yet**

Add ordinary imports and `DatabaseConnectionGate` before the existing late-override code:

```python
from __future__ import annotations

from collections.abc import Callable
import functools
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import TypeVar

T = TypeVar("T")


class DatabaseConnectionGate:
    def __init__(
        self,
        initialize: Callable[[Path], T],
        open_lightweight: Callable[[Path], T],
    ) -> None:
        self._initialize = initialize
        self._open_lightweight = open_lightweight
        self._ready_paths: set[Path] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _normalize(database_path: Path) -> Path:
        return Path(database_path).expanduser().resolve()

    def connect(self, database_path: Path) -> T:
        path = self._normalize(database_path)
        if path in self._ready_paths:
            return self._open_lightweight(path)

        with self._lock:
            if path in self._ready_paths:
                return self._open_lightweight(path)
            connection = self._initialize(path)
            self._ready_paths.add(path)
            return connection
```

Immediately after adding the ordinary-import class, mechanically wrap the existing legacy runtime section (starting at `_ORIGINAL_DB_CONNECT = db_connect`) so an ordinary import skips it while the shared runtime still executes it:

```bash
python - <<'PY'
from pathlib import Path
import textwrap

path = Path("bridge/scheduler_safety.py")
source = path.read_text(encoding="utf-8")
marker = "_ORIGINAL_DB_CONNECT = db_connect\n"
before, legacy_tail = source.split(marker, 1)
legacy = marker + legacy_tail
guard = (
    '# Temporary Phase 6A migration bridge. Ordinary import skips this block;\n'
    '# the legacy shared runtime still executes it until Task 5 cuts over.\n'
    'if "db_connect" in globals():\n'
)
path.write_text(
    before + guard + textwrap.indent(legacy, "    "),
    encoding="utf-8",
)
PY
```

This preserves the exact existing legacy implementation during Tasks 1–4 while making `bridge.scheduler_safety` normally importable. Task 5 deletes the complete guarded block.

- [x] **Step 4: Add failing DurableWorkerGuard tests**

Append tests using a real temporary SQLite file so `PRAGMA database_list` returns an actual path:

```python
class DurableWorkerGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "jobs.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            "CREATE TABLE jobs ("
            "job_id INTEGER PRIMARY KEY, "
            "state TEXT NOT NULL, "
            "last_error TEXT NOT NULL DEFAULT '', "
            "updated_at REAL NOT NULL DEFAULT 0)"
        )
        self.db.commit()
        self.sleeps = []

        def open_requeue(path, timeout=10.0):
            self.assertEqual(Path(path).resolve(), self.path.resolve())
            return sqlite3.connect(path, timeout=timeout)

        self.guard = DurableWorkerGuard(
            open_requeue,
            sleep=self.sleeps.append,
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _raise(self, exc):
        raise exc

    def test_locked_worker_requeues_scheduled_job_and_reraises(self):
        self.db.execute(
            "INSERT INTO jobs(job_id,state) VALUES(1,'scheduled')"
        )
        self.db.commit()
        original = sqlite3.OperationalError("database is locked")
        wrapped = self.guard.prepare(
            self.db,
            1,
            lambda: self._raise(original),
        )

        with self.assertRaises(sqlite3.OperationalError) as caught:
            wrapped()

        self.assertIs(caught.exception, original)
        state, error = self.db.execute(
            "SELECT state,last_error FROM jobs WHERE job_id=1"
        ).fetchone()
        self.assertEqual(state, "queued")
        self.assertIn("worker database startup failed", error)
        self.assertEqual(self.sleeps, [])

    def test_busy_worker_does_not_move_running_job_backwards(self):
        self.db.execute(
            "INSERT INTO jobs(job_id,state) VALUES(2,'running')"
        )
        self.db.commit()
        wrapped = self.guard.prepare(
            self.db,
            2,
            lambda: self._raise(
                sqlite3.OperationalError("database is busy")
            ),
        )

        with self.assertRaises(sqlite3.OperationalError):
            wrapped()

        state = self.db.execute(
            "SELECT state FROM jobs WHERE job_id=2"
        ).fetchone()[0]
        self.assertEqual(state, "running")

    def test_non_transient_operational_error_does_not_open_requeue_connection(self):
        opens = []
        guard = DurableWorkerGuard(
            lambda *args, **kwargs: opens.append((args, kwargs)),
            sleep=lambda _seconds: None,
        )
        wrapped = guard.prepare(
            self.db,
            3,
            lambda: self._raise(
                sqlite3.OperationalError("no such table")
            ),
        )

        with self.assertRaisesRegex(sqlite3.OperationalError, "no such table"):
            wrapped()

        self.assertEqual(opens, [])

    def test_non_sqlite_exception_does_not_open_requeue_connection(self):
        opens = []
        guard = DurableWorkerGuard(
            lambda *args, **kwargs: opens.append((args, kwargs)),
            sleep=lambda _seconds: None,
        )
        wrapped = guard.prepare(
            self.db,
            4,
            lambda: self._raise(RuntimeError("boom")),
        )

        with self.assertRaisesRegex(RuntimeError, "boom"):
            wrapped()

        self.assertEqual(opens, [])

    def test_requeue_retries_are_bounded(self):
        attempts = []

        class FailingConnection:
            def execute(self, *_args, **_kwargs):
                raise sqlite3.OperationalError("database is locked")

            def close(self):
                attempts.append("closed")

        guard = DurableWorkerGuard(
            lambda _path, timeout=10.0: FailingConnection(),
            sleep=self.sleeps.append,
        )
        wrapped = guard.prepare(
            self.db,
            5,
            lambda: self._raise(
                sqlite3.OperationalError("database is locked")
            ),
        )

        with self.assertRaises(sqlite3.OperationalError):
            wrapped()

        self.assertEqual(self.sleeps, [0.25, 1.0])
        self.assertEqual(attempts, ["closed", "closed", "closed"])
```

- [x] **Step 5: Run the guard tests and verify RED**

Run:

```bash
python -m unittest tests.test_scheduler_safety_adapters.DurableWorkerGuardTests -v
```

Expected: FAIL because `DurableWorkerGuard` is not yet defined.

- [x] **Step 6: Implement DurableWorkerGuard minimally**

Add this collaborator above the temporary legacy compatibility block in `bridge/scheduler_safety.py`:

```python
class DurableWorkerGuard:
    def __init__(
        self,
        open_requeue_connection,
        *,
        sleep=time.sleep,
        delays=(0.0, 0.25, 1.0),
    ) -> None:
        self._open_requeue_connection = open_requeue_connection
        self._sleep = sleep
        self._delays = tuple(delays)

    @staticmethod
    def _transient(exc: BaseException) -> bool:
        if not isinstance(exc, sqlite3.OperationalError):
            return False
        text = str(exc).casefold()
        return "locked" in text or "busy" in text

    @staticmethod
    def _database_path(db) -> Path | None:
        try:
            row = db.execute("PRAGMA database_list").fetchone()
        except sqlite3.Error:
            return None
        if not row or len(row) < 3 or not row[2]:
            return None
        return Path(str(row[2])).expanduser().resolve()

    def _requeue(
        self,
        job_id: int,
        exc: BaseException,
        database_path: Path | None,
    ) -> None:
        if not self._transient(exc) or database_path is None:
            return

        last_error = f"worker database startup failed: {exc}"[:1000]
        for delay in self._delays:
            if delay:
                self._sleep(delay)
            connection = None
            try:
                connection = self._open_requeue_connection(
                    database_path,
                    timeout=10.0,
                )
                connection.execute(
                    "UPDATE jobs SET state='queued', last_error=?, updated_at=? "
                    "WHERE job_id=? AND state IN ('queued','scheduled')",
                    (last_error, time.time(), int(job_id)),
                )
                connection.commit()
                logging.warning(
                    "Requeued durable job %s after transient DB startup failure",
                    job_id,
                )
                return
            except sqlite3.OperationalError:
                logging.warning(
                    "Could not yet requeue durable job %s",
                    job_id,
                    exc_info=True,
                )
            finally:
                if connection is not None:
                    connection.close()

        logging.error(
            "Durable job %s remains recoverable on restart after DB startup failure",
            job_id,
        )

    def prepare(self, db, job_id: int, worker):
        database_path = self._database_path(db)

        @functools.wraps(worker)
        def guarded_worker(*worker_args):
            try:
                return worker(*worker_args)
            except BaseException as exc:
                self._requeue(
                    int(job_id),
                    exc,
                    database_path,
                )
                raise

        return guarded_worker
```

- [x] **Step 7: Run the new collaborator tests**

Run:

```bash
python -m unittest tests.test_scheduler_safety_adapters -v
```

Expected: all Task 1 tests PASS.

- [x] **Step 8: Commit Task 1**

```bash
git add bridge/scheduler_safety.py tests/test_scheduler_safety_adapters.py
git commit -m "refactor: make scheduler safety ordinary collaborators"
```

---

### Task 2: Make Database Connection Safety Canonical in database.py

**Files:**
- Modify: `bridge/database.py`
- Modify: `tests/test_scheduler_safety_adapters.py`
- Modify: `tests/test_composition.py`

**Interfaces:**
- Consumes: `DatabaseConnectionGate` from Task 1.
- Produces: `_database_path(database_path: Path | None = None) -> Path`
- Produces: `_open_initialized_database(database_path: Path) -> sqlite3.Connection`
- Produces: `_lightweight_db_connect(database_path: Path | None = None, timeout: float = 30.0) -> sqlite3.Connection`
- Produces: canonical `db_connect(database_path: Path | None = None) -> sqlite3.Connection`
- Produces: module-level `_DB_CONNECTION_GATE`

- [x] **Step 1: Add RED canonical-connection integration tests**

Append to `tests/test_scheduler_safety_adapters.py`:

```python
from unittest.mock import patch
import bridge.runtime as rt


class CanonicalDatabaseConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "canonical.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_canonical_gate_runs_schema_initialization_once(self):
        original = rt.initialize_database_schema
        calls = []

        def traced(db):
            calls.append(db)
            return original(db)

        with patch.object(
            rt,
            "initialize_database_schema",
            side_effect=traced,
        ):
            first = rt._DB_CONNECTION_GATE.connect(self.path)
            second = rt._DB_CONNECTION_GATE.connect(self.path)

        try:
            self.assertEqual(len(calls), 1)
        finally:
            first.close()
            second.close()

    def test_canonical_gate_keeps_role_specific_cache(self):
        first = rt._DB_CONNECTION_GATE.connect(self.path)
        second = rt._DB_CONNECTION_GATE.connect(self.path)
        try:
            first_cache = first.execute("PRAGMA cache_size").fetchone()[0]
            second_cache = second.execute("PRAGMA cache_size").fetchone()[0]
            self.assertEqual(first_cache, -rt._DB_PRIMARY_CACHE_KIB)
            self.assertEqual(second_cache, -rt._DB_WORKER_CACHE_KIB)
            self.assertEqual(
                second.execute("PRAGMA foreign_keys").fetchone()[0],
                1,
            )
            self.assertEqual(
                second.execute("PRAGMA busy_timeout").fetchone()[0],
                30000,
            )
        finally:
            first.close()
            second.close()
```

These tests deliberately call the new canonical gate directly while the old late runtime override is still active. The final Task 5 owner test proves `rt.db_connect` itself switches to this canonical path.

- [x] **Step 2: Run the new connection tests and verify RED**

Run:

```bash
python -m unittest   tests.test_scheduler_safety_adapters.CanonicalDatabaseConnectionTests   tests.test_composition.DatabaseFactoryPathTests -v
```

Expected: FAIL because `database.py` does not yet construct `_DB_CONNECTION_GATE`.

- [x] **Step 3: Import the ordinary gate into database.py**

At the start of `bridge/database.py`, add:

```python
from bridge.scheduler_safety import (
    DatabaseConnectionGate as _DatabaseConnectionGate,
)
```

- [x] **Step 4: Split initialized and lightweight connection openers**

Replace the current single `db_connect` body with canonical helpers:

```python
def _database_path(database_path: Path | None = None) -> Path:
    path = Path(database_path) if database_path is not None else DB_FILE
    return path.expanduser().resolve()


def _open_initialized_database(
    database_path: Path,
) -> sqlite3.Connection:
    path = _database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(
        path,
        timeout=30,
        factory=_SerializedSQLiteConnection,
    )
    _apply_connection_pragmas(
        db,
        timeout=30.0,
        cache_kib=_DB_PRIMARY_CACHE_KIB,
        mmap_bytes=_DB_PRIMARY_MMAP_BYTES,
    )
    db.execute("PRAGMA auto_vacuum=INCREMENTAL")
    db.execute("PRAGMA journal_mode=WAL")
    _load_optional_vector_extension(db)
    initialize_database_schema(db)
    return db


def _lightweight_db_connect(
    database_path: Path | None = None,
    timeout: float = 30.0,
) -> sqlite3.Connection:
    path = _database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(
        path,
        timeout=timeout,
        factory=_SerializedSQLiteConnection,
    )
    _apply_connection_pragmas(
        db,
        timeout=timeout,
        cache_kib=_DB_WORKER_CACHE_KIB,
        mmap_bytes=_DB_WORKER_MMAP_BYTES,
    )
    return db


_DB_CONNECTION_GATE = _DatabaseConnectionGate(
    _open_initialized_database,
    _lightweight_db_connect,
)


def db_connect(
    database_path: Path | None = None,
) -> sqlite3.Connection:
    return _DB_CONNECTION_GATE.connect(
        _database_path(database_path)
    )
```

Do not retain `_DB_SCHEMA_READY`, `_DB_SCHEMA_READY_PATHS`, or `_DB_SCHEMA_LOCK` in a later module as compatibility state.

- [x] **Step 5: Run focused connection tests**

Run:

```bash
python -m unittest   tests.test_scheduler_safety_adapters.CanonicalDatabaseConnectionTests   tests.test_composition.DatabaseFactoryPathTests -v
```

Expected: PASS.

- [x] **Step 6: Run database-sensitive existing tests**

Run:

```bash
python -m unittest tests.test_scheduler_safety_adapters -v
python -m unittest tests.test_composition -v
```

Expected: PASS. Existing composition tests still see the temporary legacy scheduler globals until Task 5, so no intermediate compatibility break is introduced.

- [x] **Step 7: Commit Task 2**

```bash
git add bridge/database.py tests/test_scheduler_safety_adapters.py tests/test_composition.py
git commit -m "refactor: make database connection safety canonical"
```

---

### Task 3: Make Canonical Durable Recovery Bounded

**Files:**
- Modify: `bridge/database.py`
- Modify: `tests/test_scheduler_safety_adapters.py`

**Interfaces:**
- Consumes: existing `recover_jobs(db, recover_running=True)` repository API.
- Produces: same row tuple shape consumed by `JobService.recover`.
- Behavioral contract: both startup and backlog calls return at most 128 queued rows ordered by `created_at`.

- [x] **Step 1: Add RED canonical-source guard plus bounded-recovery integration test**

Append:

```python
class CanonicalRecoveryTests(unittest.TestCase):
    def test_database_recover_jobs_is_bounded_in_canonical_source(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "database.py"
        ).read_text(encoding="utf-8")
        start = source.index("def recover_jobs(")
        end = source.find("\ndef ", start + 4)
        chunk = source[start:end if end >= 0 else None]
        self.assertIn("LIMIT 128", chunk)
        self.assertNotIn(
            'limit_clause = "" if recover_running else " LIMIT 128"',
            chunk,
        )


    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "recovery.sqlite3"
        self.db = rt.db_connect(self.path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_startup_and_backlog_recovery_are_bounded_oldest_first(self):
        job_ids = []
        for update_id in range(130):
            job_ids.append(
                rt.enqueue_job(
                    self.db,
                    update_id + 1,
                    "chat",
                    "session",
                    update_id + 1,
                    "generation",
                    {"text": str(update_id)},
                )
            )

        for created_at, job_id in enumerate(job_ids, start=1):
            self.db.execute(
                "UPDATE jobs SET created_at=? WHERE job_id=?",
                (float(created_at), job_id),
            )
        self.db.execute(
            "UPDATE jobs SET state='running' WHERE job_id=?",
            (job_ids[0],),
        )
        self.db.execute(
            "UPDATE jobs SET state='scheduled' WHERE job_id=?",
            (job_ids[1],),
        )
        self.db.commit()

        first = rt.recover_jobs(self.db, recover_running=True)
        self.assertEqual(len(first), 128)
        self.assertEqual(
            [row[0] for row in first],
            job_ids[:128],
        )
        states = dict(
            self.db.execute(
                "SELECT job_id,state FROM jobs WHERE job_id IN (?,?)",
                (job_ids[0], job_ids[1]),
            ).fetchall()
        )
        self.assertEqual(states[job_ids[0]], "queued")
        self.assertEqual(states[job_ids[1]], "queued")

        for row in first:
            self.assertTrue(rt.mark_job_scheduled(self.db, row[0]))

        second = rt.recover_jobs(
            self.db,
            recover_running=False,
        )
        self.assertEqual(
            [row[0] for row in second],
            job_ids[128:],
        )
```

- [x] **Step 2: Run the test and verify RED**

Run:

```bash
python -m unittest   tests.test_scheduler_safety_adapters.CanonicalRecoveryTests -v
```

Expected: the canonical-source guard FAILS because `database.py` still omits the startup limit. The behavioral integration assertion may already pass because the temporary late safety override is still active; that is intentional until Task 5.

- [x] **Step 3: Make canonical recovery always bounded**

Replace the conditional limit logic in `bridge/database.py` with:

```python
def recover_jobs(
    db: sqlite3.Connection,
    recover_running: bool = True,
) -> list[tuple]:
    if recover_running:
        db.execute(
            "UPDATE jobs SET state='queued', updated_at=? "
            "WHERE state IN ('running','scheduled')",
            (time.time(),),
        )
    rows = db.execute(
        "SELECT job_id,chat_id,session_id,telegram_message_id,kind,payload_json "
        "FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 128"
    ).fetchall()
    db.commit()
    return rows
```

- [x] **Step 4: Run recovery and JobService tests**

Run:

```bash
python -m unittest   tests.test_scheduler_safety_adapters.CanonicalRecoveryTests   tests.test_job_service -v
```

Expected: PASS.

- [x] **Step 5: Commit Task 3**

```bash
git add bridge/database.py tests/test_scheduler_safety_adapters.py
git commit -m "refactor: make durable recovery canonically bounded"
```

---

### Task 4: Wire DurableWorkerGuard Explicitly Through JobService

**Files:**
- Modify: `bridge/main.py`
- Modify: `tests/test_composition.py`
- Modify: `tests/test_job_service.py`

**Interfaces:**
- Consumes: `DurableWorkerGuard` from Task 1.
- Consumes: canonical `_lightweight_db_connect(database_path, timeout=30.0)` from Task 2.
- Produces: module collaborator `_DURABLE_WORKER_GUARD`.
- Preserves: `JobService.prepare_worker` callable contract.
- Preserves: `submit_durable_chat_job(...)` as the compatibility helper in `main.py`.

- [x] **Step 1: Add RED composition assertion**

In `tests/test_composition.py::test_startup_builds_job_service_from_final_job_collaborators`, add:

```python
        self.assertIsNotNone(services.jobs.prepare_worker)
        self.assertIs(
            services.jobs.prepare_worker.__self__,
            rt._DURABLE_WORKER_GUARD,
        )
```

Add a source-boundary assertion to `CompositionSourceBoundaryTests`:

```python
    def test_job_service_worker_guard_is_explicit_not_global_lookup(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "main.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            'prepare_worker=globals().get("_guard_durable_worker")',
            source,
        )
        self.assertIn(
            "prepare_worker=_DURABLE_WORKER_GUARD.prepare",
            source,
        )
```

- [x] **Step 2: Add RED recovered-submission prepare_worker test**

In `tests/test_job_service.py`, add:

```python
    def test_recover_uses_prepare_worker_for_recovered_submission(self):
        prepared = []

        def prepare_worker(db, job_id, worker):
            prepared.append((db, job_id, worker))
            return worker

        service = replace(
            self.service,
            prepare_worker=prepare_worker,
        )
        self.recovery_rows = [
            (
                58,
                "chat",
                "session",
                "17",
                "generation",
                "{}",
            ),
        ]
        worker = lambda *_args: None

        service.recover(
            self.db,
            lambda job: JobSubmission(
                label=job.kind,
                chat_id=job.chat_id,
                worker=worker,
                args=(),
            ),
        )

        self.assertEqual(
            prepared,
            [(self.db, 58, worker)],
        )
```

This should already pass against JobService itself; it pins the seam before composition changes.

- [x] **Step 3: Run focused tests and capture current RED/GREEN split**

Run:

```bash
python -m unittest   tests.test_composition.CompositionIntegrationTests.test_startup_builds_job_service_from_final_job_collaborators   tests.test_composition.CompositionSourceBoundaryTests.test_job_service_worker_guard_is_explicit_not_global_lookup   tests.test_job_service.JobServiceTests.test_recover_uses_prepare_worker_for_recovered_submission -v
```

Expected:
- JobService recovery seam test PASS.
- Composition/source test FAIL because `main.py` still uses `globals().get("_guard_durable_worker")`.

- [x] **Step 4: Import and construct the explicit guard in main.py**

Add to the ordinary imports at the top of `bridge/main.py`:

```python
from bridge.scheduler_safety import (
    DurableWorkerGuard as _DurableWorkerGuard,
)
```

After the imports and after `database.py` has established `_lightweight_db_connect` in the shared compatibility namespace, construct:

```python
_DURABLE_WORKER_GUARD = _DurableWorkerGuard(
    _lightweight_db_connect
)
```

In both `_compatibility_job_service` and `_build_startup_services`, replace:

```python
prepare_worker=globals().get("_guard_durable_worker"),
```

with:

```python
prepare_worker=_DURABLE_WORKER_GUARD.prepare,
```

Do not add a `BridgeServices` field for the guard.

- [x] **Step 5: Run composition, JobService, and worker tests**

Run:

```bash
python -m unittest   tests.test_composition   tests.test_job_service   tests.test_job_service_workers -v
```

Expected: PASS.

- [x] **Step 6: Commit Task 4**

```bash
git add bridge/main.py tests/test_composition.py tests/test_job_service.py
git commit -m "refactor: inject durable worker safety explicitly"
```

---

### Task 5: Remove scheduler_safety.py From the Compatibility Runtime

**Files:**
- Modify: `bridge/runtime_loader.py`
- Modify: `bridge/scheduler_safety.py`
- Modify: `tests/test_runtime_loader.py`
- Modify: `tests/test_scheduler_safety_adapters.py`
- Modify: `tests/test_composition.py`

**Interfaces:**
- Consumes: canonical `database.py` owners and explicit main.py JobService composition from Tasks 2–4.
- Produces: runtime stage list with no `scheduler_safety.py`.
- Produces: no scheduler-safety public-callable override allowlist.
- Production canonical owners:
  - `db_connect` -> `bridge/database.py`
  - `recover_jobs` -> `bridge/database.py`
  - `submit_durable_chat_job` -> `bridge/main.py`

- [x] **Step 1: Add RED runtime architecture tests**

In `tests/test_runtime_loader.py`, add:

```python
    def test_scheduler_safety_is_not_a_runtime_stage(self):
        loaded_modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn("scheduler_safety.py", loaded_modules)
        for stage in DEFAULT_RUNTIME_STAGES:
            self.assertEqual(
                stage.allowed_overrides_for("scheduler_safety.py"),
                frozenset(),
            )

    def test_scheduler_owners_are_canonical_files(self):
        self.assertEqual(
            Path(rt.db_connect.__code__.co_filename).name,
            "database.py",
        )
        self.assertEqual(
            Path(rt.recover_jobs.__code__.co_filename).name,
            "database.py",
        )
        self.assertEqual(
            Path(rt.submit_durable_chat_job.__code__.co_filename).name,
            "main.py",
        )
```

Update `tests/test_composition.py::DatabaseFactoryPathTests` to stop saving/resetting the temporary legacy `_DB_SCHEMA_READY`, `_DB_SCHEMA_LOCK`, and `_DB_SCHEMA_READY_PATHS` globals:

```python
class DatabaseFactoryPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_db_file = rt.DB_FILE

    def tearDown(self):
        rt.DB_FILE = self.old_db_file
        self.tmp.cleanup()
```

Remove the readiness-global mutation from `test_explicit_database_paths_initialize_independently`; unique temporary paths provide isolation through the canonical gate.

In `tests/test_scheduler_safety_adapters.py`, add the final source guard:

```python
class SchedulerSafetySourceBoundaryTests(unittest.TestCase):
    def test_module_has_no_late_override_capture_or_compat_submitter(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "scheduler_safety.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("_ORIGINAL_DB_CONNECT", source)
        self.assertNotIn("def db_connect(", source)
        self.assertNotIn("def recover_jobs(", source)
        self.assertNotIn("def submit_durable_chat_job(", source)
        self.assertNotIn("bridge.runtime", source)
        self.assertNotIn("bridge.main", source)
```

- [x] **Step 2: Run runtime tests and verify RED**

Run:

```bash
python -m unittest   tests.test_runtime_loader.RuntimeLoaderTests.test_scheduler_safety_is_not_a_runtime_stage   tests.test_runtime_loader.RuntimeLoaderTests.test_scheduler_owners_are_canonical_files   tests.test_scheduler_safety_adapters.SchedulerSafetySourceBoundaryTests -v
```

Expected:
- source-boundary test FAIL because the temporary compatibility block still contains `_ORIGINAL_DB_CONNECT` and the legacy functions;
- runtime-stage test FAIL because `scheduler_safety.py` is still listed;
- canonical-owner test reports the legacy scheduler owner until the cutover.

- [x] **Step 3: Delete the temporary scheduler compatibility block and remove the module from runtime_loader.py**

Change the `safety_overrides` module tuple from:

```python
(
    "sync_safety.py",
    "state_integrity.py",
    "scheduler_safety.py",
    "scene_state.py",
    "director_goals.py",
    "memory_curator.py",
)
```

to:

```python
(
    "sync_safety.py",
    "state_integrity.py",
    "scene_state.py",
    "director_goals.py",
    "memory_curator.py",
)
```

Delete the complete allowlist entry:

```python
(
    "scheduler_safety.py",
    ("db_connect", "recover_jobs", "submit_durable_chat_job"),
),
```

In `bridge/scheduler_safety.py`, delete the entire temporary `if "db_connect" in globals():` compatibility block, including:
- `_ORIGINAL_DB_CONNECT`,
- `_DB_SCHEMA_READY`, `_DB_SCHEMA_READY_PATHS`, and `_DB_SCHEMA_LOCK`,
- the legacy private database opener/path helpers,
- the replacement `db_connect`,
- the replacement `recover_jobs`,
- the legacy `_guard_durable_worker`,
- the replacement `submit_durable_chat_job`.

The final file contains only the ordinary-import `DatabaseConnectionGate`, `DurableWorkerGuard`, their stdlib imports, and internal methods.

Leave all remaining Phase 6B+ safety modules unchanged.

- [x] **Step 4: Run runtime architecture tests**

Run:

```bash
python -m unittest   tests.test_runtime_loader   tests.test_scheduler_safety_adapters -v
```

Expected: PASS.

- [x] **Step 5: Run a source scan for forbidden scheduler architecture**

Run:

```bash
python - <<'PY'
from pathlib import Path

root = Path("bridge")
loader = (root / "runtime_loader.py").read_text(encoding="utf-8")
safety = (root / "scheduler_safety.py").read_text(encoding="utf-8")
main = (root / "main.py").read_text(encoding="utf-8")

assert '"scheduler_safety.py"' not in loader
assert "_ORIGINAL_DB_CONNECT" not in safety
assert "def submit_durable_chat_job(" not in safety
assert 'globals().get("_guard_durable_worker")' not in main
assert "prepare_worker=_DURABLE_WORKER_GUARD.prepare" in main
print("Phase 6A source boundaries verified")
PY
```

Expected: `Phase 6A source boundaries verified`.

- [x] **Step 6: Commit Task 5**

```bash
git add bridge/runtime_loader.py bridge/scheduler_safety.py tests/test_runtime_loader.py tests/test_scheduler_safety_adapters.py tests/test_composition.py
git commit -m "refactor: retire scheduler safety runtime overrides"
```

---

### Task 6: Full Verification, Documentation Evidence, and PR Readiness

**Files:**
- Modify: `docs/superpowers/plans/2026-09-20-phase-6a-scheduler-safety-adapters.md`
- No production changes unless verification exposes a defect; any defect must return to the owning task's RED→GREEN cycle.

**Interfaces:**
- Consumes: complete Phase 6A branch.
- Produces: exact-head local verification evidence.
- Produces: PR against `cepeter/SillyTavern-Telegram-Bridge:main`.
- Produces: exact-head GitHub Actions success before Ready-for-review.
- Does not merge the PR.

- [x] **Step 1: Run compile verification**

```bash
python -m compileall -q bridge tests sillytavern_telegram_bridge.py
```

Expected: exit 0.

- [x] **Step 2: Run the full unittest suite**

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [x] **Step 3: Run the full pytest suite**

```bash
python -m pytest -q
```

Expected: all tests PASS.

- [x] **Step 4: Validate the dependency environment**

```bash
python -m pip check
```

Expected: `No broken requirements found.`

- [x] **Step 5: Run dependency audit when the CI environment has pip-audit installed**

```bash
python -m pip_audit -r requirements.lock
```

Expected: no known vulnerabilities. If local `pip-audit` is unavailable, do not install unrelated dependencies solely for this step; GitHub Actions remains the authoritative audit gate.

- [x] **Step 6: Review exact source ownership**

Run:

```bash
python - <<'PY'
from pathlib import Path
import bridge.runtime as rt
from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

modules = {
    module
    for stage in DEFAULT_RUNTIME_STAGES
    for module in stage.modules
}
assert "scheduler_safety.py" not in modules
assert Path(rt.db_connect.__code__.co_filename).name == "database.py"
assert Path(rt.recover_jobs.__code__.co_filename).name == "database.py"
assert Path(rt.submit_durable_chat_job.__code__.co_filename).name == "main.py"

source = Path("bridge/scheduler_safety.py").read_text(encoding="utf-8")
assert "_ORIGINAL_DB_CONNECT" not in source
assert "def db_connect(" not in source
assert "def recover_jobs(" not in source
assert "def submit_durable_chat_job(" not in source

print("Phase 6A canonical owners verified")
PY
```

Expected: `Phase 6A canonical owners verified`.

- [x] **Step 7: Update this plan's completed checkboxes and evidence section**

Append an execution evidence section containing the exact:
- RED test/CI commit SHA,
- final implementation head SHA,
- unittest count,
- pytest count,
- dependency audit result,
- source-boundary result,
- upstream `main` SHA used as merge base.

Do not mark GitHub exact-head CI or Ready-for-review complete before those events actually occur.

- [x] **Step 8: Commit verification documentation**

```bash
git add docs/superpowers/plans/2026-09-20-phase-6a-scheduler-safety-adapters.md
git commit -m "docs: record Phase 6A verification"
```

- [x] **Step 9: Push/update the feature branch and open or refresh a Draft PR**

PR target:
- base: `cepeter/SillyTavern-Telegram-Bridge:main`
- head: `punzer4-code:refactor/phase-6a-scheduler-safety-adapters`

PR title:

```text
refactor: retire scheduler safety runtime overrides
```

PR body must state:
- scheduler safety is now ordinary-import infrastructure,
- canonical `db_connect` and `recover_jobs` live in `database.py`,
- JobService receives worker requeue safety explicitly through `prepare_worker`,
- `scheduler_safety.py` is removed from runtime stages and override allowlists,
- executor/FIFO/semaphore/shutdown behavior is unchanged,
- no Phase 6B work is included,
- RED→GREEN evidence and current verification counts.

- [x] **Step 10: Verify upstream drift before final CI gate**

Compare current upstream `main` with the branch merge base. If upstream changed, inspect the delta and rebase/refresh only if required; rerun all affected tests after any integration change.

Expected: no unreviewed drift incorporated silently.

- [ ] **Step 11: Wait for GitHub Actions on the exact final head and inspect every CI step**

Required workflow steps from `.github/workflows/ci.yml`:
- checkout,
- Python 3.11,
- packaging tools,
- locked runtime dependencies,
- `pip check`,
- dev test tooling,
- compile,
- unittest discovery,
- pytest,
- pip-audit installation,
- locked dependency audit.

Expected: workflow conclusion `success` for the exact branch head.

- [ ] **Step 12: Mark the PR Ready for review only after exact-head CI is green**

Before Ready:
- PR is open and mergeable,
- exact head matches the verified CI SHA,
- no unresolved review thread exists,
- no newly failing status exists,
- no unexpected upstream drift exists.

Do **not** merge the PR in this plan. Merge remains a separate user decision.


## Execution Evidence

Implementation used the Draft PR CI workflow as the native RED -> GREEN harness.

### RED -> GREEN checkpoints

- Task 1 RED: `9728c257597d0d04f6b17a6927d00acde51e63d3`, CI #379 (`35499266504`) failed because ordinary import reached the legacy `_ORIGINAL_DB_CONNECT = db_connect` capture. GREEN: `eaa4b2d183d388ac67cf55325da8773c7430f9ab`, CI #380 succeeded.
- Task 2 RED: `867f42d232a7f0cb751c33115ceaa60a3d3c0eb5`, CI #381 (`35499380648`) failed because `_DB_CONNECTION_GATE` did not exist. The first implementation edit exposed a literal-newline syntax defect on CI #382; after root-cause correction, `249efd4b84b4d47f008dd89976f45575f8129ba2` passed CI #383.
- Task 3 RED: `1e53463979e0266a8e7d12e24c042ba60b83be73`, CI #384 (`35499551827`) failed because canonical `recover_jobs` still left startup recovery unbounded. GREEN: `7988cece54c91f5fa98f1edfad1ddf6003616b79`, CI #385 succeeded.
- Task 4 RED: `309d2346704ffe8b38bea23f0e38749c7d4b2cbc`, CI #387 (`35499664661`) failed because `main.py` still discovered `_guard_durable_worker` through `globals()`. GREEN: `8376439999e5fb80768ce1d8774bd85730863644`, CI #388 succeeded.
- Task 5 RED: `35fcf29d1670cab118018d44d6640e1ee110d3d0`, CI #390 (`35499777351`) failed because `scheduler_safety.py` remained a runtime stage, production owners still resolved to the late override, and `_ORIGINAL_DB_CONNECT` remained. The cutover then exposed legacy test-fixture coupling to old `_DB_SCHEMA_*` names and two tests that called removed private helpers. Those tests were migrated to the explicit gate/guard APIs while three inert compatibility attributes were retained in `database.py` solely for legacy fixture setup. They are not read by `db_connect`.
- Integrated implementation head before this evidence commit: `b732ae92b9ca5c433a2aff7a2a8710a428b84fd5`.
- Integrated CI #396 (`35499999297`) succeeded: `487` unittest tests, `487 passed, 103 subtests passed` under pytest, `No broken requirements found.`, and `No known vulnerabilities found`.
- Source review at the integrated implementation head confirmed: no `scheduler_safety.py` runtime stage/allowlist entry; no `_ORIGINAL_DB_CONNECT`; no replacement `db_connect`, `recover_jobs`, or `submit_durable_chat_job` in `scheduler_safety.py`; no `globals().get("_guard_durable_worker")`; canonical `db_connect` uses `_DB_CONNECTION_GATE`; canonical recovery uses `LIMIT 128`.
- Merge base / current upstream `main` during verification: `e2bcc5fc54bee4bcf0dbb2a088aa9af930485bf5`; no upstream drift was present.
- Draft PR: #41. At the integrated implementation head it was open, mergeable, with no comments, formal reviews, or unresolved review threads.

### Final review correction

Whole-branch self-review found that retaining `_DB_SCHEMA_LOCK`, `_DB_SCHEMA_READY`, and `_DB_SCHEMA_READY_PATHS` contradicted the approved plan even though they were inert in production. A new source-boundary regression test was added first at `58cd1a31f13390174dded88c839ef8a6b57a4872`; CI #399 failed exactly on `test_database_has_no_legacy_schema_readiness_globals` with `488 tests, 1 failure`. The compatibility globals were then removed from `database.py` at `f251ed8ac9c8b1a9c04c9f0a39c274330e11dc02`.

This correction intentionally keeps Phase 6A narrow: the already-migrated fixtures use unique temporary paths and the explicit `_DB_CONNECTION_GATE`; no production compatibility state remains.

Steps 11-12 intentionally remain open in this file until GitHub Actions succeeds on the exact documentation-final head and PR #41 is marked Ready for review. Updating the checklist after that event would create another head and recursively invalidate the exact-head CI evidence.

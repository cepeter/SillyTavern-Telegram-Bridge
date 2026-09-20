# Phase 6E Sync Safety Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the final Sync runtime overrides by moving orphan-binding startup cleanup into canonical schema maintenance and realtime polling hardening into an ordinary `SyncPollSafetyAdapter`, preserving all existing Sync safety behavior.

**Architecture:** `schema.py` becomes the sole owner of orphan `sync_bindings` startup cleanup. A new `bridge/sync_poll_safety.py` owns fair bounded realtime polling, durable-job exclusion, lock lifetime, retry/backoff, and disable policy; `sync_api.py` composes it explicitly and remains the public owner of `phase3_sync_poll`. The final cutover deletes `sync_safety.py` and removes both Sync override allowlist entries atomically.

**Tech Stack:** Python 3.11, stdlib `dataclasses`, `sqlite3`, `threading`, `unittest`, `unittest.mock`, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-20-phase-6e-sync-safety-design.md`

## Global Constraints

- Phase 6E changes ownership/composition only; user-visible Sync behavior must not change.
- Orphan `sync_bindings` cleanup moves into `schema.py::_run_startup_database_cleanup`.
- Do not add or renumber schema migrations.
- Do not add request-time DDL or an extra commit inside startup cleanup.
- Poll candidates must be ordered by `last_checked_at ASC, chat_id, session_id`.
- The hardened poll query must not use SQL `LIMIT 32`.
- Only candidates that acquire the chat lock and pass durable-job exclusion consume the 32-attempt budget.
- Durable jobs in `queued`, `scheduled`, or `running` state block realtime Sync for that chat.
- Durable-job inspection failure releases the chat lock, logs, skips the candidate, and does not fail the poll.
- Every acquired chat lock must be released on every path.
- Expected non-transient errors disable immediately.
- Expected transient errors use `min(60.0, interval * (2 ** min(count, 5)))`.
- The fifth expected failure is persisted before disable.
- Unexpected failures 1–4 use the same hardened exponential delay.
- The fifth unexpected failure is persisted before disable.
- Unexpected disable reason remains exactly `unexpected Phase 3 binding failure`.
- `SyncService` remains structurally unchanged.
- `phase3_sync_now`, conflict/checkpoint semantics, and realtime worker lifecycle remain unchanged.
- `chat_job_lock` implementation remains unchanged.
- No new public runtime override, `_ORIGINAL_*` capture, service locator, or load-order dependency.
- No Phase 7 work is authorized.

## Review Focus

- More than 32 leading candidates are locked: the poll must scan past them and still reach an eligible candidate without spending the attempt budget.
- Durable-job lookup raises after the chat lock is acquired: the lock must be released and the candidate skipped without aborting the whole poll.
- A transient expected error occurs with prior failure count 4: count 5 must be persisted before realtime is disabled.
- An unexpected exception occurs with prior failure count 4: count 5 and the exact generic error text must be persisted before disable.
- A retry error string exceeds 1000 characters: persisted `last_error` must be truncated while the disable path still receives the full expected exception string when terminal.

---

## File Structure

**Create `bridge/sync_poll_safety.py`**
- Ordinary-import polling hardening only.
- Defines `SyncPollSafetyAdapter`.
- Owns candidate query/fairness, lock acquisition, durable-job exclusion, attempt counting, retry/backoff, terminal disable, and lock release.
- Imports no `bridge.runtime`, `bridge.sync_api`, `bridge.sync_safety`, Telegram/UI, or recovery modules.

**Modify `bridge/schema.py`**
- Add orphan `sync_bindings` deletion to `_run_startup_database_cleanup`.
- Do not modify `SCHEMA_MIGRATIONS`.

**Modify `bridge/sync_api.py`**
- Import `SyncPollSafetyAdapter`.
- Compose `_SYNC_POLL_SAFETY` using call-time lambdas for shared-runtime collaborators.
- During Task 3, keep the existing raw public `phase3_sync_poll` unchanged so the still-loaded `sync_safety.py` remains the single public safety wrapper.
- During Task 4, replace that raw body with a stable delegate to `_SYNC_POLL_SAFETY.poll(db)` while deleting `sync_safety.py`.

**Delete `bridge/sync_safety.py`**
- Its startup and polling responsibilities are fully migrated.

**Modify `bridge/runtime_loader.py`**
- Remove `sync_safety.py` from `safety_overrides`.
- Remove its `initialize_database_schema` / `phase3_sync_poll` override allowlist.

**Create `tests/test_sync_poll_safety.py`**
- Ordinary-import unit tests for `SyncPollSafetyAdapter`.
- Must not import `bridge.runtime`.

**Modify `tests/test_sync_audit.py`**
- Move integration tests from `_ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL` patching to the explicit final `phase3_sync_now` boundary.
- Strengthen canonical startup cleanup coverage.

**Modify `tests/test_runtime_loader.py`**
- Assert final canonical owners and complete Sync-safety runtime retirement.

**Do not modify `bridge/sync_service.py`.**

---

### Task 1: Add the Ordinary SyncPollSafetyAdapter

**Files:**
- Create: `bridge/sync_poll_safety.py`
- Create: `tests/test_sync_poll_safety.py`

**Interfaces:**
- Produces:
  - `SyncPollSafetyAdapter(sync_now, chat_lock, disable_realtime, expected_errors, sync_interval, now, log_warning, attempt_limit=32)`
  - `SyncPollSafetyAdapter.poll(db: sqlite3.Connection) -> None`
- Consumes:
  - `sync_now(db, chat_id, session_id) -> str`
  - `chat_lock(chat_id) -> lock-like object supporting acquire(blocking=False) and release()`
  - `disable_realtime(db, chat_id, session_id, error) -> None`
  - `expected_errors: tuple[type[Exception], ...]`
  - `sync_interval() -> float`
  - `now() -> float`
  - `log_warning(message, *args, **kwargs) -> None`

- [ ] **Step 1: Write the failing ordinary-import adapter tests**

Create `tests/test_sync_poll_safety.py`:

```python
from pathlib import Path
import sqlite3
import threading
import unittest

from bridge.sync_poll_safety import SyncPollSafetyAdapter


class ExpectedSyncError(RuntimeError):
    def __init__(self, message, *, transient=False):
        super().__init__(message)
        self.transient = transient


class SyncPollSafetyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute(
            """CREATE TABLE sync_bindings (
                chat_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                realtime_enabled INTEGER NOT NULL DEFAULT 0,
                realtime_failures INTEGER NOT NULL DEFAULT 0,
                realtime_next_retry_at REAL NOT NULL DEFAULT 0,
                last_checked_at REAL NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(chat_id, session_id)
            )"""
        )
        self.db.execute(
            """CREATE TABLE jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                state TEXT NOT NULL
            )"""
        )
        self.db.commit()

        self.now_value = 1000.0
        self.interval = 2.0
        self.calls = []
        self.disabled = []
        self.warnings = []
        self.locks = {}

        def lock_for(chat_id):
            return self.locks.setdefault(
                str(chat_id),
                threading.Lock(),
            )

        self.lock_for = lock_for
        self.sync_now = lambda _db, chat_id, session_id: self.calls.append(
            (chat_id, session_id)
        )

        def disable(db, chat_id, session_id, error):
            persisted = db.execute(
                "SELECT realtime_failures,last_error "
                "FROM sync_bindings "
                "WHERE chat_id=? AND session_id=?",
                (chat_id, session_id),
            ).fetchone()
            self.disabled.append(
                (chat_id, session_id, error, persisted)
            )
            db.execute(
                "UPDATE sync_bindings "
                "SET realtime_enabled=0,realtime_next_retry_at=0,"
                "last_error=? "
                "WHERE chat_id=? AND session_id=?",
                (str(error)[:1000], chat_id, session_id),
            )
            db.commit()

        self.disable = disable

        def warning(message, *args, **kwargs):
            self.warnings.append(
                (message, args, kwargs)
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=self.sync_now,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=warning,
        )

    def tearDown(self):
        self.db.close()

    def add_binding(
        self,
        chat_id,
        session_id,
        *,
        failures=0,
        retry_at=0.0,
        checked_at=0.0,
        enabled=1,
    ):
        self.db.execute(
            "INSERT INTO sync_bindings("
            "chat_id,session_id,realtime_enabled,"
            "realtime_failures,realtime_next_retry_at,"
            "last_checked_at,last_error"
            ") VALUES(?,?,?,?,?,?,?)",
            (
                chat_id,
                session_id,
                enabled,
                failures,
                retry_at,
                checked_at,
                "",
            ),
        )
        self.db.commit()

    def test_oldest_binding_is_attempted_first(self):
        self.add_binding(
            "newer",
            "s-new",
            checked_at=20.0,
        )
        self.add_binding(
            "oldest",
            "s-old",
            checked_at=1.0,
        )

        self.adapter.poll(self.db)

        self.assertEqual(
            self.calls,
            [
                ("oldest", "s-old"),
                ("newer", "s-new"),
            ],
        )

    def test_future_retry_candidate_is_not_attempted(self):
        self.add_binding(
            "future",
            "s1",
            retry_at=self.now_value + 1,
        )

        self.adapter.poll(self.db)

        self.assertEqual(self.calls, [])

    def test_locked_candidate_does_not_consume_attempt_budget(self):
        for index in range(3):
            self.add_binding(
                f"locked-{index}",
                f"s{index}",
                checked_at=float(index),
            )
            self.lock_for(
                f"locked-{index}"
            ).acquire()
        self.add_binding(
            "eligible",
            "s3",
            checked_at=3.0,
        )
        self.adapter = SyncPollSafetyAdapter(
            sync_now=self.sync_now,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
            attempt_limit=1,
        )

        try:
            self.adapter.poll(self.db)
        finally:
            for index in range(3):
                self.lock_for(
                    f"locked-{index}"
                ).release()

        self.assertEqual(
            self.calls,
            [("eligible", "s3")],
        )

    def test_scans_past_32_locked_candidates(self):
        for index in range(32):
            chat_id = f"a{index:02d}"
            self.add_binding(
                chat_id,
                f"s{index:02d}",
                checked_at=0.0,
            )
            self.lock_for(chat_id).acquire()
        self.add_binding(
            "z-eligible",
            "s32",
            checked_at=0.0,
        )

        try:
            self.adapter.poll(self.db)
        finally:
            for index in range(32):
                self.lock_for(
                    f"a{index:02d}"
                ).release()

        self.assertEqual(
            self.calls,
            [("z-eligible", "s32")],
        )

    def test_attempt_limit_counts_only_eligible_acquired_candidates(self):
        for index in range(4):
            self.add_binding(
                f"chat-{index}",
                f"s{index}",
                checked_at=float(index),
            )
        self.adapter = SyncPollSafetyAdapter(
            sync_now=self.sync_now,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
            attempt_limit=2,
        )

        self.adapter.poll(self.db)

        self.assertEqual(
            self.calls,
            [
                ("chat-0", "s0"),
                ("chat-1", "s1"),
            ],
        )

    def test_each_durable_job_state_blocks_sync_and_releases_lock(self):
        for state in (
            "queued",
            "scheduled",
            "running",
        ):
            with self.subTest(state=state):
                chat_id = f"chat-{state}"
                session_id = f"s-{state}"
                self.add_binding(chat_id, session_id)
                self.db.execute(
                    "INSERT INTO jobs(chat_id,state) "
                    "VALUES(?,?)",
                    (chat_id, state),
                )
                self.db.commit()

        self.adapter.poll(self.db)

        self.assertEqual(self.calls, [])
        for state in (
            "queued",
            "scheduled",
            "running",
        ):
            lock = self.lock_for(
                f"chat-{state}"
            )
            self.assertTrue(
                lock.acquire(blocking=False)
            )
            lock.release()

    def test_done_job_does_not_block_sync(self):
        self.add_binding("chat", "session")
        self.db.execute(
            "INSERT INTO jobs(chat_id,state) "
            "VALUES('chat','done')"
        )
        self.db.commit()

        self.adapter.poll(self.db)

        self.assertEqual(
            self.calls,
            [("chat", "session")],
        )

    def test_durable_job_query_failure_releases_lock_and_skips(self):
        class BrokenDb:
            def execute(inner_self, sql, params=()):
                if "FROM sync_bindings" in sql:
                    return self.db.execute(
                        sql,
                        params,
                    )
                raise sqlite3.OperationalError(
                    "database is locked"
                )

        self.add_binding("chat", "session")
        broken = BrokenDb()

        self.adapter.poll(broken)

        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.warnings), 1)
        self.assertEqual(
            self.warnings[0][0],
            "Could not inspect durable jobs before sync for chat %s",
        )
        self.assertEqual(
            self.warnings[0][1],
            ("chat",),
        )
        self.assertTrue(
            self.warnings[0][2]["exc_info"]
        )
        lock = self.lock_for("chat")
        self.assertTrue(
            lock.acquire(blocking=False)
        )
        lock.release()

    def test_success_releases_chat_lock(self):
        self.add_binding("chat", "session")

        self.adapter.poll(self.db)

        lock = self.lock_for("chat")
        self.assertTrue(
            lock.acquire(blocking=False)
        )
        lock.release()

    def test_expected_non_transient_error_disables_immediately(self):
        self.add_binding("chat", "session")

        def fail(*_args):
            raise ExpectedSyncError(
                "bad request",
                transient=False,
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        self.assertEqual(
            self.disabled,
            [
                (
                    "chat",
                    "session",
                    "bad request",
                    (1, ""),
                )
            ],
        )

    def test_expected_transient_error_uses_exponential_backoff(self):
        self.add_binding(
            "chat",
            "session",
            failures=2,
        )

        def fail(*_args):
            raise ExpectedSyncError(
                "temporary",
                transient=True,
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        row = self.db.execute(
            "SELECT realtime_enabled,realtime_failures,"
            "realtime_next_retry_at,last_error "
            "FROM sync_bindings "
            "WHERE chat_id='chat' AND session_id='session'"
        ).fetchone()
        self.assertEqual(
            row,
            (
                1,
                3,
                self.now_value + 16.0,
                "temporary",
            ),
        )
        self.assertEqual(self.disabled, [])

    def test_expected_backoff_is_capped_at_sixty_seconds(self):
        self.interval = 30.0
        self.add_binding(
            "chat",
            "session",
            failures=3,
        )

        def fail(*_args):
            raise ExpectedSyncError(
                "temporary",
                transient=True,
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        retry_at = self.db.execute(
            "SELECT realtime_next_retry_at "
            "FROM sync_bindings "
            "WHERE chat_id='chat' AND session_id='session'"
        ).fetchone()[0]
        self.assertEqual(
            retry_at,
            self.now_value + 60.0,
        )

    def test_expected_fifth_failure_is_persisted_before_disable(self):
        self.add_binding(
            "chat",
            "session",
            failures=4,
        )

        def fail(*_args):
            raise ExpectedSyncError(
                "temporary",
                transient=True,
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        self.assertEqual(
            self.disabled,
            [
                (
                    "chat",
                    "session",
                    "temporary",
                    (5, ""),
                )
            ],
        )

    def test_retry_error_text_is_truncated_to_1000_characters(self):
        self.add_binding("chat", "session")
        message = "x" * 1200

        def fail(*_args):
            raise ExpectedSyncError(
                message,
                transient=True,
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        stored = self.db.execute(
            "SELECT last_error "
            "FROM sync_bindings "
            "WHERE chat_id='chat' AND session_id='session'"
        ).fetchone()[0]
        self.assertEqual(
            stored,
            "x" * 1000,
        )

    def test_terminal_expected_error_passes_full_string_to_disable(self):
        self.add_binding("chat", "session")
        message = "x" * 1200

        def fail(*_args):
            raise ExpectedSyncError(
                message,
                transient=False,
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        self.assertEqual(
            self.disabled[0][2],
            message,
        )

    def test_unexpected_error_uses_hardened_exponential_backoff(self):
        self.add_binding(
            "chat",
            "session",
            failures=1,
        )

        def fail(*_args):
            raise RuntimeError("boom")

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        row = self.db.execute(
            "SELECT realtime_failures,"
            "realtime_next_retry_at,last_error "
            "FROM sync_bindings "
            "WHERE chat_id='chat' AND session_id='session'"
        ).fetchone()
        self.assertEqual(
            row,
            (
                2,
                self.now_value + 8.0,
                "unexpected Phase 3 binding failure",
            ),
        )

    def test_unexpected_failure_logs_warning(self):
        self.add_binding("chat", "session")

        def fail(*_args):
            raise RuntimeError("boom")

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda message, *args, **kwargs:
                self.warnings.append(
                    (message, args, kwargs)
                ),
        )

        self.adapter.poll(self.db)

        self.assertEqual(
            self.warnings[0][0],
            "Phase 3 binding failed for session %s",
        )
        self.assertEqual(
            self.warnings[0][1],
            ("session",),
        )
        self.assertTrue(
            self.warnings[0][2]["exc_info"]
        )

    def test_unexpected_fifth_failure_is_persisted_before_disable(self):
        self.add_binding(
            "chat",
            "session",
            failures=4,
        )

        def fail(*_args):
            raise RuntimeError("boom")

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        self.assertEqual(
            self.disabled,
            [
                (
                    "chat",
                    "session",
                    "unexpected Phase 3 binding failure",
                    (5, ""),
                )
            ],
        )

    def test_expected_transient_error_releases_chat_lock(self):
        self.add_binding("chat", "session")

        def fail(*_args):
            raise ExpectedSyncError(
                "temporary",
                transient=True,
            )

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        lock = self.lock_for("chat")
        self.assertTrue(
            lock.acquire(blocking=False)
        )
        lock.release()

    def test_error_path_releases_chat_lock(self):
        self.add_binding("chat", "session")

        def fail(*_args):
            raise RuntimeError("boom")

        self.adapter = SyncPollSafetyAdapter(
            sync_now=fail,
            chat_lock=self.lock_for,
            disable_realtime=self.disable,
            expected_errors=(ExpectedSyncError, ValueError),
            sync_interval=lambda: self.interval,
            now=lambda: self.now_value,
            log_warning=lambda *_args, **_kwargs: None,
        )

        self.adapter.poll(self.db)

        lock = self.lock_for("chat")
        self.assertTrue(
            lock.acquire(blocking=False)
        )
        lock.release()


class SyncPollSafetySourceBoundaryTests(unittest.TestCase):
    def test_sync_poll_safety_has_no_runtime_sync_or_ui_imports(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "sync_poll_safety.py"
        ).read_text(encoding="utf-8")

        for forbidden in (
            "bridge.runtime",
            "bridge.sync_api",
            "bridge.sync_safety",
            "bridge.telegram",
            "bridge.recovery",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(
                    forbidden,
                    source,
                )

    def test_hardened_query_has_no_sql_limit_32(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "sync_poll_safety.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("LIMIT 32", source)
        self.assertIn(
            "ORDER BY last_checked_at ASC,chat_id,session_id",
            source,
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the adapter test suite and verify RED**

Run:

```bash
python -m unittest tests.test_sync_poll_safety -v
```

Expected: import failure because `bridge/sync_poll_safety.py` does not exist.

- [ ] **Step 3: Implement the minimal polling-safety adapter**

Create `bridge/sync_poll_safety.py`:

```python
"""Explicit safety policy for realtime Live Sync polling."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import sqlite3


_UNEXPECTED_SYNC_ERROR = (
    "unexpected Phase 3 binding failure"
)


@dataclass(frozen=True)
class SyncPollSafetyAdapter:
    sync_now: Callable[
        [sqlite3.Connection, str, str],
        str,
    ]
    chat_lock: Callable[[str], object]
    disable_realtime: Callable[
        [sqlite3.Connection, str, str, str],
        None,
    ]
    expected_errors: tuple[type[Exception], ...]
    sync_interval: Callable[[], float]
    now: Callable[[], float]
    log_warning: Callable[..., None]
    attempt_limit: int = 32

    def _try_chat_lock(
        self,
        db: sqlite3.Connection,
        chat_id: str,
    ):
        lock = self.chat_lock(str(chat_id))
        if not lock.acquire(blocking=False):
            return None
        try:
            pending = db.execute(
                "SELECT 1 FROM jobs WHERE chat_id=? "
                "AND state IN ('queued','scheduled','running') "
                "LIMIT 1",
                (str(chat_id),),
            ).fetchone()
        except Exception:
            lock.release()
            self.log_warning(
                "Could not inspect durable jobs before sync for chat %s",
                chat_id,
                exc_info=True,
            )
            return None
        if pending:
            lock.release()
            return None
        return lock

    def _delay(self, count: int) -> float:
        return min(
            60.0,
            self.sync_interval()
            * (2 ** min(count, 5)),
        )

    @staticmethod
    def _persist_failure_count(
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        count: int,
    ) -> None:
        db.execute(
            "UPDATE sync_bindings SET realtime_failures=? "
            "WHERE chat_id=? AND session_id=?",
            (count, chat_id, session_id),
        )
        db.commit()

    def _persist_retry(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        count: int,
        error: str,
    ) -> None:
        db.execute(
            "UPDATE sync_bindings SET "
            "realtime_failures=?,"
            "realtime_next_retry_at=?,"
            "last_error=? "
            "WHERE chat_id=? AND session_id=?",
            (
                count,
                self.now() + self._delay(count),
                error[:1000],
                chat_id,
                session_id,
            ),
        )
        db.commit()

    def poll(
        self,
        db: sqlite3.Connection,
    ) -> None:
        rows = db.execute(
            "SELECT chat_id,session_id,realtime_failures "
            "FROM sync_bindings "
            "WHERE realtime_enabled=1 "
            "AND realtime_next_retry_at<=? "
            "ORDER BY last_checked_at ASC,chat_id,session_id",
            (self.now(),),
        ).fetchall()

        attempted = 0
        for chat_id, session_id, failures in rows:
            if attempted >= self.attempt_limit:
                break

            lock = self._try_chat_lock(
                db,
                str(chat_id),
            )
            if lock is None:
                continue

            attempted += 1
            try:
                try:
                    self.sync_now(
                        db,
                        str(chat_id),
                        str(session_id),
                    )
                except self.expected_errors as exc:
                    count = int(failures or 0) + 1
                    if (
                        not getattr(
                            exc,
                            "transient",
                            False,
                        )
                        or count >= 5
                    ):
                        self._persist_failure_count(
                            db,
                            str(chat_id),
                            str(session_id),
                            count,
                        )
                        self.disable_realtime(
                            db,
                            str(chat_id),
                            str(session_id),
                            str(exc),
                        )
                        continue
                    self._persist_retry(
                        db,
                        str(chat_id),
                        str(session_id),
                        count,
                        str(exc),
                    )
                except Exception:
                    self.log_warning(
                        "Phase 3 binding failed for session %s",
                        session_id,
                        exc_info=True,
                    )
                    count = int(failures or 0) + 1
                    if count >= 5:
                        self._persist_failure_count(
                            db,
                            str(chat_id),
                            str(session_id),
                            count,
                        )
                        self.disable_realtime(
                            db,
                            str(chat_id),
                            str(session_id),
                            _UNEXPECTED_SYNC_ERROR,
                        )
                        continue
                    self._persist_retry(
                        db,
                        str(chat_id),
                        str(session_id),
                        count,
                        _UNEXPECTED_SYNC_ERROR,
                    )
            finally:
                lock.release()
```

- [ ] **Step 4: Run the adapter tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_sync_poll_safety -v
```

Expected: all tests PASS.

- [ ] **Step 5: Run a focused source scan**

Run:

```bash
python - <<'PY'
from pathlib import Path

source = Path(
    "bridge/sync_poll_safety.py"
).read_text(encoding="utf-8")

assert "LIMIT 32" not in source
assert (
    "ORDER BY last_checked_at ASC,chat_id,session_id"
    in source
)
for forbidden in (
    "bridge.runtime",
    "bridge.sync_api",
    "bridge.sync_safety",
    "bridge.telegram",
    "bridge.recovery",
):
    assert forbidden not in source

print("Sync poll safety boundary verified")
PY
```

Expected:

```text
Sync poll safety boundary verified
```

- [ ] **Step 6: Commit Task 1**

```bash
git add   bridge/sync_poll_safety.py   tests/test_sync_poll_safety.py
git commit -m "refactor: add Live Sync poll safety adapter"
```

---

### Task 2: Move Orphan Sync Cleanup into Canonical Schema Startup

**Files:**
- Modify: `bridge/schema.py`
- Modify: `tests/test_sync_audit.py`

**Interfaces:**
- Consumes:
  - existing `schema.py::_run_startup_database_cleanup(db) -> None`.
- Produces:
  - canonical startup cleanup that deletes orphan `sync_bindings` without creating a migration or committing internally.

- [ ] **Step 1: Add a RED direct-startup-cleanup regression**

Add this test to `SyncAuditHardeningTests` in `tests/test_sync_audit.py`:

```python
    def test_canonical_startup_cleanup_removes_orphan_sync_binding(self):
        valid = rt.create_session(
            self.db,
            "valid-chat",
            rt.DEFAULT_MODEL,
            session_id="valid-session",
        )
        rt.ensure_sync_binding(
            self.db,
            "valid-chat",
            valid["session_id"],
        )
        self.db.execute(
            "INSERT INTO sync_bindings("
            "chat_id,session_id,sync_id"
            ") VALUES(?,?,?)",
            (
                "orphan-chat",
                "missing-session",
                "stb-orphan",
            ),
        )
        self.db.commit()

        rt._run_startup_database_cleanup(
            self.db,
        )
        self.db.commit()

        self.assertIsNone(
            self.db.execute(
                "SELECT 1 FROM sync_bindings "
                "WHERE chat_id='orphan-chat' "
                "AND session_id='missing-session'"
            ).fetchone()
        )
        self.assertIsNotNone(
            self.db.execute(
                "SELECT 1 FROM sync_bindings "
                "WHERE chat_id='valid-chat' "
                "AND session_id='valid-session'"
            ).fetchone()
        )
```

This calls the canonical schema cleanup helper directly, bypassing the late `sync_safety.py::initialize_database_schema` wrapper.

- [ ] **Step 2: Run the direct cleanup test and verify RED**

Run:

```bash
python -m unittest   tests.test_sync_audit.SyncAuditHardeningTests.test_canonical_startup_cleanup_removes_orphan_sync_binding -v
```

Expected: FAIL because the orphan row remains.

- [ ] **Step 3: Add orphan cleanup to schema.py**

At the end of `_run_startup_database_cleanup`, before the function returns, add:

```python
    db.execute(
        "DELETE FROM sync_bindings "
        "WHERE NOT EXISTS ("
        "SELECT 1 FROM sessions "
        "WHERE sessions.chat_id=sync_bindings.chat_id "
        "AND sessions.session_id=sync_bindings.session_id"
        ")"
    )
```

Do not call `db.commit()` inside `_run_startup_database_cleanup`.

Do not change `SCHEMA_MIGRATIONS`.

- [ ] **Step 4: Strengthen the existing no-structural-DDL startup test**

Replace the final structural assertion in `test_sync_startup_cleanup_removes_orphan_without_structural_ddl` with:

```python
        sync_structural = [
            sql
            for sql in traced
            if sql.lstrip().upper().startswith(
                (
                    "CREATE ",
                    "ALTER ",
                    "DROP ",
                )
            )
            and "sync_" in sql.casefold()
        ]
        self.assertEqual(
            sync_structural,
            [],
            sync_structural,
        )
```

The test still invokes canonical public `initialize_database_schema`, which is late-wrapped until Task 4, but now verifies the recurring cleanup itself requires no structural Sync DDL.

- [ ] **Step 5: Pin migration versions**

Add:

```python
    def test_phase6e_does_not_add_schema_migration(self):
        self.assertEqual(
            tuple(
                migration.version
                for migration in rt.SCHEMA_MIGRATIONS
            ),
            (1, 2, 3, 4),
        )
```

- [ ] **Step 6: Run focused schema/Sync cleanup tests**

Run:

```bash
python -m unittest   tests.test_sync_audit.SyncAuditHardeningTests.test_canonical_startup_cleanup_removes_orphan_sync_binding   tests.test_sync_audit.SyncAuditHardeningTests.test_sync_startup_cleanup_removes_orphan_without_structural_ddl   tests.test_sync_audit.SyncAuditHardeningTests.test_session_delete_cascades_sync_binding_cleanup   tests.test_sync_audit.SyncAuditHardeningTests.test_phase6e_does_not_add_schema_migration -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add   bridge/schema.py   tests/test_sync_audit.py
git commit -m "refactor: own Sync orphan cleanup in schema startup"
```

---

### Task 3: Compose Poll Safety in sync_api.py Without Cutting Over Yet

**Files:**
- Modify: `bridge/sync_api.py`
- Modify: `tests/test_sync_phase3.py`
- Modify: `tests/test_sync_audit.py`

**Interfaces:**
- Consumes:
  - `SyncPollSafetyAdapter` from Task 1;
  - canonical schema cleanup from Task 2.
- Produces:
  - `_SYNC_POLL_SAFETY: SyncPollSafetyAdapter` composed in `sync_api.py`.
- Transition invariant:
  - public runtime `phase3_sync_poll` must still resolve to `sync_safety.py` throughout Task 3 so hardening runs exactly once.

- [ ] **Step 1: Add the architectural RED for explicit composition**

Add to `tests/test_sync_phase3.py`:

```python
class SyncPollOwnershipTests(unittest.TestCase):
    def test_sync_api_composes_poll_safety_adapter(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "sync_api.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "SyncPollSafetyAdapter as _SyncPollSafetyAdapter",
            source,
        )
        self.assertIn(
            "_SYNC_POLL_SAFETY = _SyncPollSafetyAdapter(",
            source,
        )

    def test_phase6e_transition_keeps_sync_safety_public_owner(self):
        self.assertEqual(
            Path(
                rt.phase3_sync_poll.__code__.co_filename
            ).name,
            "sync_safety.py",
        )
```

- [ ] **Step 2: Run the composition assertion and verify RED**

Run:

```bash
python -m unittest   tests.test_sync_phase3.SyncPollOwnershipTests.test_sync_api_composes_poll_safety_adapter -v
```

Expected: FAIL because `sync_api.py` does not yet compose the adapter.

- [ ] **Step 3: Import the adapter into sync_api.py**

Near the existing `SyncService` import, add:

```python
from bridge.sync_poll_safety import (
    SyncPollSafetyAdapter as _SyncPollSafetyAdapter,
)
```

- [ ] **Step 4: Compose the adapter with call-time collaborators**

Insert immediately before `def compatibility_sync_service()`. At that point `_phase3_disable`, `phase3_sync_now`, `phase3_toggle_realtime`, and all required globals are already defined:

```python
_SYNC_POLL_SAFETY = _SyncPollSafetyAdapter(
    sync_now=(
        lambda db, chat_id, session_id:
        phase3_sync_now(
            db,
            chat_id,
            session_id,
        )
    ),
    chat_lock=(
        lambda chat_id:
        chat_job_lock(chat_id)
    ),
    disable_realtime=(
        lambda db, chat_id, session_id, error:
        _phase3_disable(
            db,
            chat_id,
            session_id,
            error,
        )
    ),
    expected_errors=(
        SillyTavernApiError,
        ValueError,
    ),
    sync_interval=(
        lambda:
        PHASE3_SYNC_INTERVAL_SECONDS
    ),
    now=(
        lambda:
        time.time()
    ),
    log_warning=(
        lambda message, *args, **kwargs:
        logging.warning(
            message,
            *args,
            **kwargs,
        )
    ),
)
```

Do **not** replace the existing public `phase3_sync_poll` body in Task 3.

Do **not** delete `sync_safety.py` in Task 3.

- [ ] **Step 5: Add a transition-state direct-adapter patchability test**

Add to `SyncAuditHardeningTests`:

```python
    def test_direct_poll_adapter_uses_final_runtime_sync_now(self):
        self._binding()
        calls = []

        original = rt.phase3_sync_now
        rt.phase3_sync_now = (
            lambda _db, chat_id, session_id:
            calls.append(
                (chat_id, session_id)
            )
        )
        try:
            rt._SYNC_POLL_SAFETY.poll(
                self.db,
            )
        finally:
            rt.phase3_sync_now = original

        self.assertEqual(
            calls,
            [("chat", "session")],
        )
```

This test proves call-time collaborator lookup survives the shared compatibility namespace.

- [ ] **Step 6: Run Task 3 focused tests**

Run:

```bash
python -m unittest   tests.test_sync_poll_safety   tests.test_sync_audit.SyncAuditHardeningTests.test_direct_poll_adapter_uses_final_runtime_sync_now   tests.test_sync_phase3.SyncPollOwnershipTests -v
```

Expected: PASS, including the transition assertion that public runtime ownership is still `sync_safety.py`.

- [ ] **Step 7: Commit Task 3**

```bash
git add   bridge/sync_api.py   tests/test_sync_phase3.py   tests/test_sync_audit.py
git commit -m "refactor: compose Live Sync polling safety"
```

---

### Task 4: Atomically Retire sync_safety.py and Cut Over Canonical Ownership

**Files:**
- Modify: `bridge/sync_api.py`
- Delete: `bridge/sync_safety.py`
- Modify: `bridge/runtime_loader.py`
- Modify: `tests/test_sync_audit.py`
- Modify: `tests/test_sync_phase3.py`
- Modify: `tests/test_runtime_loader.py`

**Interfaces:**
- Consumes:
  - `_SYNC_POLL_SAFETY` from Task 3;
  - canonical orphan cleanup from Task 2.
- Produces:
  - public `sync_api.py::phase3_sync_poll(db) -> None` delegating to `_SYNC_POLL_SAFETY.poll(db)`;
  - public `schema.py::initialize_database_schema` with no late wrapper;
  - no `sync_safety.py` runtime module;
  - no Sync safety override allowlist;
  - no `_ORIGINAL_SYNC_INITIALIZE_DATABASE_SCHEMA`;
  - no `_ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL`.

- [ ] **Step 1: Add final runtime ownership RED tests**

In `tests/test_runtime_loader.py`, add:

```python
    def test_sync_schema_public_owner_is_schema(self):
        self.assertEqual(
            Path(
                rt.initialize_database_schema
                .__code__.co_filename
            ).name,
            "schema.py",
        )

    def test_sync_poll_public_owner_is_sync_api(self):
        self.assertEqual(
            Path(
                rt.phase3_sync_poll
                .__code__.co_filename
            ).name,
            "sync_api.py",
        )

    def test_sync_safety_is_not_a_runtime_module(self):
        modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn(
            "sync_safety.py",
            modules,
        )

    def test_runtime_report_has_no_sync_safety_entry(self):
        modules = {
            item["module"]
            for item in rt.RUNTIME_LOAD_REPORT
        }
        self.assertNotIn(
            "sync_safety.py",
            modules,
        )

    def test_sync_safety_functions_have_no_override_allowlist(self):
        for stage in DEFAULT_RUNTIME_STAGES:
            for filename, names in (
                stage.allowed_public_callable_overrides
            ):
                for name in (
                    "initialize_database_schema",
                    "phase3_sync_poll",
                ):
                    self.assertNotIn(
                        name,
                        names,
                        msg=(
                            f"{filename} still overrides "
                            f"{name}"
                        ),
                    )
```

- [ ] **Step 2: Add final source-boundary RED tests**

Extend `SyncPollOwnershipTests` in `tests/test_sync_phase3.py`:

```python
    def test_sync_safety_module_is_retired(self):
        path = (
            Path(__file__).parents[1]
            / "bridge"
            / "sync_safety.py"
        )
        self.assertFalse(path.exists())

    def test_no_sync_safety_original_captures_remain(self):
        root = (
            Path(__file__).parents[1]
            / "bridge"
        )
        offenders = []
        for path in root.glob("*.py"):
            source = path.read_text(
                encoding="utf-8"
            )
            if (
                "_ORIGINAL_SYNC_INITIALIZE_DATABASE_SCHEMA"
                in source
                or "_ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL"
                in source
            ):
                offenders.append(path.name)

        self.assertEqual(offenders, [])
```

Delete the Task 3 transition assertion:

```text
test_phase6e_transition_keeps_sync_safety_public_owner
```

after the final cutover.

- [ ] **Step 3: Run final architecture tests and verify RED**

Run:

```bash
python -m unittest   tests.test_runtime_loader.RuntimeLoaderTests.test_sync_schema_public_owner_is_schema   tests.test_runtime_loader.RuntimeLoaderTests.test_sync_poll_public_owner_is_sync_api   tests.test_runtime_loader.RuntimeLoaderTests.test_sync_safety_is_not_a_runtime_module   tests.test_runtime_loader.RuntimeLoaderTests.test_runtime_report_has_no_sync_safety_entry   tests.test_runtime_loader.RuntimeLoaderTests.test_sync_safety_functions_have_no_override_allowlist   tests.test_sync_phase3.SyncPollOwnershipTests.test_sync_safety_module_is_retired   tests.test_sync_phase3.SyncPollOwnershipTests.test_no_sync_safety_original_captures_remain -v
```

Expected: FAIL because `sync_safety.py` still owns both public functions.

- [ ] **Step 4: Cut public phase3_sync_poll over to the adapter**

Replace the complete current raw `sync_api.py::phase3_sync_poll` body with:

```python
def phase3_sync_poll(
    db: sqlite3.Connection,
) -> None:
    _SYNC_POLL_SAFETY.poll(db)
```

Do not alter `phase3_sync_now`.

Do not alter `compatibility_sync_service` beyond continuing to pass:

```python
poll_backend=phase3_sync_poll
```

- [ ] **Step 5: Delete sync_safety.py**

Delete:

```text
bridge/sync_safety.py
```

No code should be copied from it during this step; Tasks 1–3 already provide both replacements.

- [ ] **Step 6: Remove sync_safety.py from runtime_loader.py**

Change:

```python
RuntimeStage(
    "safety_overrides",
    (
        "sync_safety.py",
        "scene_state.py",
        "director_goals.py",
        "memory_curator.py",
    ),
    (
        (
            "sync_safety.py",
            (
                "initialize_database_schema",
                "phase3_sync_poll",
            ),
        ),
    ),
),
```

to:

```python
RuntimeStage(
    "safety_overrides",
    (
        "scene_state.py",
        "director_goals.py",
        "memory_curator.py",
    ),
),
```

Do not alter the other safety modules.

- [ ] **Step 7: Migrate old Sync audit tests away from _ORIGINAL_* patching**

In `tests/test_sync_audit.py`, replace each pattern:

```python
original = rt._ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL
rt._ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL = fake_sync
try:
    rt.phase3_sync_poll(self.db)
finally:
    rt._ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL = original
```

with:

```python
original = rt.phase3_sync_now
rt.phase3_sync_now = fake_sync
try:
    rt.phase3_sync_poll(self.db)
finally:
    rt.phase3_sync_now = original
```

Apply this exact boundary migration to:

```text
test_phase3_realtime_sync_skips_chat_with_active_job_lock
test_phase3_poll_scans_past_32_locked_candidates
test_phase3_bounded_poll_prioritizes_oldest_binding
test_phase3_unexpected_failures_disable_after_five_attempts
```

For `test_sync_lock_is_released_when_job_query_fails`, remove the dependency on deleted `rt._try_sync_chat_lock`. The equivalent lock-release/query-failure behavior is now pinned by:

```text
tests.test_sync_poll_safety.SyncPollSafetyAdapterTests.test_durable_job_query_failure_releases_lock_and_skips
```

Delete the obsolete runtime-private-helper test instead of inventing a new public helper.

- [ ] **Step 8: Strengthen final public integration behavior**

Add this test to `SyncAuditHardeningTests`:

```python
    def test_public_poll_uses_hardened_adapter_after_cutover(self):
        self._many_bindings()
        calls = []
        original = rt.phase3_sync_now

        rt.phase3_sync_now = (
            lambda _db, chat_id, session_id:
            calls.append(
                (chat_id, session_id)
            )
        )
        locks = [
            rt.chat_job_lock(
                f"a{index:02d}"
            )
            for index in range(32)
        ]
        for lock in locks:
            lock.acquire()
        try:
            rt.phase3_sync_poll(self.db)
        finally:
            for lock in locks:
                lock.release()
            rt.phase3_sync_now = original

        self.assertEqual(
            calls,
            [("z-eligible", "s32")],
        )
```

This proves the final public `sync_api.py::phase3_sync_poll` path retains the hardened scan-past-32 behavior after deleting the late wrapper.

- [ ] **Step 9: Run focused Phase 6E suites**

Run:

```bash
python -m unittest   tests.test_sync_poll_safety   tests.test_sync_audit   tests.test_sync_phase3   tests.test_sync_service   tests.test_runtime_loader   tests.test_scheduler_safety_adapters -v
```

Expected: PASS.

- [ ] **Step 10: Run architecture/source guards**

Run:

```bash
python - <<'PY'
from pathlib import Path
import bridge.runtime as rt
from bridge.runtime_loader import (
    DEFAULT_RUNTIME_STAGES,
)

root = Path("bridge")

assert not (
    root / "sync_safety.py"
).exists()

for path in root.glob("*.py"):
    source = path.read_text(
        encoding="utf-8"
    )
    assert (
        "_ORIGINAL_SYNC_INITIALIZE_DATABASE_SCHEMA"
        not in source
    )
    assert (
        "_ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL"
        not in source
    )

assert (
    Path(
        rt.initialize_database_schema
        .__code__.co_filename
    ).name
    == "schema.py"
)
assert (
    Path(
        rt.phase3_sync_poll
        .__code__.co_filename
    ).name
    == "sync_api.py"
)

loaded = {
    module
    for stage in DEFAULT_RUNTIME_STAGES
    for module in stage.modules
}
assert "sync_safety.py" not in loaded

for stage in DEFAULT_RUNTIME_STAGES:
    for filename, names in (
        stage.allowed_public_callable_overrides
    ):
        assert (
            "initialize_database_schema"
            not in names
        ), (filename, names)
        assert (
            "phase3_sync_poll"
            not in names
        ), (filename, names)

report_modules = {
    item["module"]
    for item in rt.RUNTIME_LOAD_REPORT
}
assert "sync_safety.py" not in report_modules

print(
    "Phase 6E Sync safety ownership verified"
)
PY
```

Expected:

```text
Phase 6E Sync safety ownership verified
```

- [ ] **Step 11: Commit Task 4**

```bash
git add   bridge/sync_api.py   bridge/runtime_loader.py   tests/test_sync_audit.py   tests/test_sync_phase3.py   tests/test_runtime_loader.py
git rm bridge/sync_safety.py
git commit -m "refactor: retire Sync safety runtime overrides"
```

---

### Task 5: Exact-Head Verification and PR Readiness

**Files:**
- Modify: `docs/superpowers/plans/2026-09-20-phase-6e-sync-safety.md`
- No production file changes unless verification finds a defect. Any defect returns to its owning task's RED -> GREEN loop.

**Interfaces:**
- Consumes: completed Phase 6E branch.
- Produces: exact-head verification evidence and a Draft PR against upstream `main`.
- Does not merge the PR.

- [ ] **Step 1: Run compile verification**

Run:

```bash
python -m compileall -q   bridge   tests   sillytavern_telegram_bridge.py
```

Expected: exit 0.

- [ ] **Step 2: Run full unittest discovery**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 3: Run full pytest**

Run:

```bash
python -m pytest -q
```

Expected: all tests PASS.

- [ ] **Step 4: Validate installed dependencies**

Run:

```bash
python -m pip check
```

Expected:

```text
No broken requirements found.
```

- [ ] **Step 5: Audit locked dependencies where available**

Run:

```bash
python -m pip_audit -r requirements.lock
```

Expected: no known vulnerabilities.

If local `pip-audit` is unavailable, do not install unrelated tooling solely for this command; exact-head GitHub Actions dependency audit is authoritative.

- [ ] **Step 6: Verify Phase 6E non-scope equivalence**

Compare these final files against baseline:

```text
daeeb2ea20ef4ab20a39e63ca330de52e4ef21b1
```

Required:

```text
bridge/sync_service.py
```

must have no Phase 6E diff.

Compare final:

```text
bridge/sync_api.py::phase3_sync_now
```

against baseline. Its function body must be byte-for-byte identical.

Compare all realtime worker functions against baseline:

```text
_phase3_worker_loop
start_phase3_sync_worker
stop_phase3_sync_worker
```

Their bodies must be byte-for-byte identical.

Verify `SCHEMA_MIGRATIONS` versions remain exactly:

```text
1, 2, 3, 4
```

- [ ] **Step 7: Verify exact hardened behavior source invariants**

Run:

```bash
python - <<'PY'
from pathlib import Path

source = Path(
    "bridge/sync_poll_safety.py"
).read_text(encoding="utf-8")

assert "LIMIT 32" not in source
assert (
    "ORDER BY last_checked_at ASC,chat_id,session_id"
    in source
)
assert "attempt_limit: int = 32" in source
assert (
    "unexpected Phase 3 binding failure"
    in source
)
assert "count >= 5" in source
assert "60.0" in source

schema = Path(
    "bridge/schema.py"
).read_text(encoding="utf-8")
assert (
    "DELETE FROM sync_bindings "
    in schema
)
assert (
    "WHERE NOT EXISTS ("
    in schema
)

print(
    "Phase 6E behavior invariants verified"
)
PY
```

Expected:

```text
Phase 6E behavior invariants verified
```

- [ ] **Step 8: Record execution evidence in this plan**

Append an `## Execution Evidence` section containing:

- Task-level RED commit SHAs and exact expected failures;
- matching GREEN commit SHAs;
- any execution rulings and cost-if-wrong notes;
- final implementation head SHA;
- unittest result;
- pytest result;
- `pip check` result;
- dependency audit result;
- ownership/source guard result;
- `SyncService` non-scope equivalence;
- `phase3_sync_now` equivalence;
- worker lifecycle equivalence;
- schema migration-version equivalence;
- current upstream `main` SHA;
- upstream drift/reconciliation decision.

Do not claim exact-head CI success until the workflow has completed on that exact SHA.

- [ ] **Step 9: Commit verification documentation**

Run:

```bash
git add   docs/superpowers/plans/2026-09-20-phase-6e-sync-safety.md
git commit -m "docs: record Phase 6E verification"
```

- [ ] **Step 10: Open or update a Draft PR**

Target:

```text
base: cepeter/SillyTavern-Telegram-Bridge:main
head: punzer4-code:refactor/phase-6e-sync-safety
```

Title:

```text
refactor: retire Sync safety runtime overrides
```

PR body must state:

- canonical schema startup now owns orphan Sync cleanup;
- `SyncPollSafetyAdapter` owns realtime polling hardening;
- `sync_api.py` owns public `phase3_sync_poll`;
- `sync_safety.py` is deleted;
- fairness/attempt-limit behavior is preserved;
- durable-job exclusion and lock-release behavior are preserved;
- expected/unexpected backoff and fifth-failure disable behavior are preserved;
- `SyncService`, `phase3_sync_now`, worker lifecycle, and migration versions are intentionally unchanged;
- RED -> GREEN evidence and exact verification results.

Keep the PR Draft until exact-head CI succeeds and review is clear.

- [ ] **Step 11: Check upstream drift**

Compare the feature branch merge base with current upstream `main`.

If upstream has not changed, continue.

If upstream changed only unrelated files, document that result.

If upstream changed any Phase 6E implementation/test file, inspect and reconcile it, then rerun affected focused tests and full verification.

- [ ] **Step 12: Inspect GitHub Actions on the exact final head**

Required successful steps:

- checkout;
- Python 3.11 setup;
- packaging tools;
- locked runtime dependencies;
- `pip check`;
- development test tooling;
- compile;
- unittest discovery;
- pytest;
- pip-audit installation;
- locked dependency audit.

Expected: workflow conclusion `success` on the exact PR head SHA.

- [ ] **Step 13: Review the whole branch and PR feedback**

Before Ready-for-review, verify:

- no Critical or Important findings remain;
- no unresolved review threads;
- no unaddressed review comments;
- PR is mergeable;
- exact PR head equals the green CI SHA.

If no independent reviewer/subagent capability exists in the harness, perform the Superpowers fallback whole-branch self-review and explicitly record that limitation in the PR body.

- [ ] **Step 14: Mark the PR Ready for review**

Only after Steps 12–13 pass.

Do not merge the PR. Merge remains a separate user decision.

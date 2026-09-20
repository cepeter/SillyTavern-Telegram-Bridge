# Phase 6E — Sync Safety Extraction Design

**Date:** 2026-09-20  
**Status:** Proposed — awaiting written-spec review  
**Repository:** `cepeter/SillyTavern-Telegram-Bridge`  
**Baseline:** `daeeb2ea20ef4ab20a39e63ca330de52e4ef21b1` (merged Phase 6D)  
**Master architecture:** `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`

## 1. Purpose

Phase 6E removes the final Sync-related runtime overrides from `bridge/sync_safety.py` while preserving all existing hardening behavior.

The two current overrides have different natural owners:

1. `initialize_database_schema` hardens startup lifecycle by deleting orphaned `sync_bindings`;
2. `phase3_sync_poll` hardens realtime polling with fairness, chat-job exclusion, durable-job exclusion, retry/backoff, and disable-after-failure behavior.

Phase 6E separates those responsibilities instead of recreating one broad safety wrapper.

The target is:

- canonical startup cleanup in `schema.py`;
- ordinary polling-safety policy in a new `bridge/sync_poll_safety.py`;
- canonical public `phase3_sync_poll` ownership in `sync_api.py`;
- deletion of `bridge/sync_safety.py`;
- removal of all Sync public override allowlist entries from `runtime_loader.py`.

Phase 6E changes ownership and composition only. It must not change user-visible Sync behavior.

## 2. Current state

After Phase 6D, `sync_safety.py` is the last Sync runtime-override module.

It captures:

```python
_ORIGINAL_SYNC_INITIALIZE_DATABASE_SCHEMA = initialize_database_schema
_ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL = phase3_sync_now
```

and replaces:

```text
initialize_database_schema
phase3_sync_poll
```

The module currently also owns:

- `_SYNC_POLL_ATTEMPT_LIMIT = 32`;
- `_try_sync_chat_lock(...)`;
- ordered realtime-binding selection;
- scanning past unavailable/locked candidates;
- durable-job exclusion;
- retry/backoff state updates;
- disable-after-terminal/fifth-failure behavior;
- guaranteed lock release.

The underlying native `sync_api.py::phase3_sync_poll` is materially weaker than the late override and cannot simply become canonical without migrating the hardening first.

## 3. Selected approach

Use two explicit destinations according to responsibility.

### 3.1 Startup lifecycle cleanup

Move the defensive orphan-binding cleanup directly into:

```text
schema.py::_run_startup_database_cleanup
```

No adapter is needed here because startup cleanup is already the canonical lifecycle boundary.

Final startup flow:

```text
initialize_database_schema
    |
    +-- _run_migrations
    |
    +-- _run_startup_database_cleanup
    |      |
    |      +-- existing retention cleanup
    |      +-- delete orphan sync_bindings
    |
    +-- commit
```

### 3.2 Realtime polling safety

Create:

```text
bridge/sync_poll_safety.py
```

with a focused ordinary-import `SyncPollSafetyAdapter`.

Final polling flow:

```text
SyncService.poll
    |
    v
sync_api.py::phase3_sync_poll
    |
    v
_SYNC_POLL_SAFETY.poll
    |
    +-- ordered eligible binding scan
    +-- chat-lock exclusion
    +-- durable-job exclusion
    +-- 32 acquired-attempt limit
    +-- sync_now
    +-- retry/backoff/disable policy
    +-- lock release
```

`sync_api.py` remains the canonical public owner of `phase3_sync_poll`.

## 4. Rejected alternatives

### 4.1 One broad SyncSafetyAdapter for startup and polling

Rejected because startup schema lifecycle and realtime polling are unrelated responsibilities.

A single adapter would remove the late override mechanically but preserve the conceptual coupling that Phase 6 is intended to eliminate.

### 4.2 Fold all polling safety directly into sync_api.py

Rejected because `sync_api.py` already owns API protocol, single-session synchronization, worker lifecycle, and compatibility composition.

The polling safety policy is cohesive and independently testable. Keeping it in a focused ordinary-import module prevents further growth of `sync_api.py` while still leaving public ownership there.

### 4.3 Keep sync_safety.py as an ordinary helper

Rejected because the file's purpose is specifically late-loaded lifecycle/sync hardening. Once both overrides are extracted, keeping the file would preserve an obsolete compatibility boundary.

## 5. Startup cleanup ownership

The current `schema.py::initialize_database_schema` already performs:

```python
_run_migrations(db, SCHEMA_MIGRATIONS)
_run_startup_database_cleanup(db)
db.commit()
```

Phase 6E adds this defensive statement to `_run_startup_database_cleanup`:

```sql
DELETE FROM sync_bindings
WHERE NOT EXISTS (
    SELECT 1
    FROM sessions
    WHERE sessions.chat_id=sync_bindings.chat_id
      AND sessions.session_id=sync_bindings.session_id
)
```

This is recurring startup maintenance, not structural schema evolution.

Therefore Phase 6E must not:

- add a new schema migration;
- modify migration version numbers;
- add request-time DDL;
- add another commit inside the cleanup helper.

The existing `sessions_delete_sync_binding` trigger remains the primary lifecycle mechanism. Startup orphan cleanup remains a defensive repair path.

## 6. Polling safety component

Create:

```python
@dataclass(frozen=True)
class SyncPollSafetyAdapter:
    sync_now: Callable[[sqlite3.Connection, str, str], str]
    chat_lock: Callable[[str], object]
    disable_realtime: Callable[[sqlite3.Connection, str, str, str], None]
    expected_errors: tuple[type[Exception], ...]
    sync_interval: Callable[[], float]
    now: Callable[[], float]
    log_warning: Callable[..., None]
    attempt_limit: int = 32

    def poll(self, db: sqlite3.Connection) -> None:
        ...
```

Exact annotations may be more precise during implementation, but the dependency direction is fixed.

The adapter owns:

- polling candidate selection;
- fairness ordering;
- chat-job exclusion;
- durable-job exclusion;
- attempt counting;
- expected-error retry/disable logic;
- unexpected-error retry/disable logic;
- guaranteed lock release.

The adapter must not import:

- `bridge.runtime`;
- `sync_api.py`;
- `sync_safety.py`;
- Telegram/UI modules;
- recovery modules.

## 7. Lock acquisition boundary

The existing `_try_sync_chat_lock` behavior moves into the ordinary polling-safety implementation.

It may be implemented as a private adapter method or module helper, but it remains part of the polling-safety boundary.

For each candidate:

1. obtain `chat_job_lock(str(chat_id))`;
2. attempt `lock.acquire(blocking=False)`;
3. if acquisition fails, return/skip without counting an attempt;
4. after acquisition, inspect `jobs` for:

```sql
state IN ('queued','scheduled','running')
```

5. if durable work exists, release the lock and skip without counting an attempt;
6. if the durable-job query raises, release the lock, log the current warning, and skip without failing the poll;
7. otherwise return the held lock to the caller;
8. the caller must release it in a `finally` block.

Current warning text must remain:

```text
Could not inspect durable jobs before sync for chat %s
```

with exception information.

## 8. Candidate query and fairness

The hardened polling query must remain:

```sql
SELECT chat_id,session_id,realtime_failures
FROM sync_bindings
WHERE realtime_enabled=1
  AND realtime_next_retry_at<=?
ORDER BY last_checked_at ASC,chat_id,session_id
```

There must be no SQL `LIMIT 32`.

The adapter scans candidates until:

- no more eligible rows remain; or
- 32 candidates have successfully acquired the chat lock and passed the durable-job exclusion check.

Locked/busy candidates do not consume the attempt budget.

This preserves the existing ability to scan past more than 32 unavailable candidates and still reach an eligible binding.

## 9. Attempt execution flow

For each candidate that passes safety acquisition:

```text
held chat lock
    |
    v
attempted += 1
    |
    v
sync_now(db, chat_id, session_id)
    |
    +-- success
    |
    +-- expected error
    |
    +-- unexpected error
    |
    v
finally: release chat lock
```

The attempt limit applies to actual calls that reach the synchronized execution section, not rows scanned.

The adapter must call the injected single-session Sync backend exactly once for each counted attempt.

## 10. Expected-error policy

Expected errors remain:

```text
SillyTavernApiError
ValueError
```

For an expected exception:

```python
count = int(failures or 0) + 1
```

If:

```text
not exc.transient
OR
count >= 5
```

then Phase 6E must:

1. write `realtime_failures=count`;
2. commit;
3. call `disable_realtime(db, chat_id, session_id, str(exc))`.

Persisting the failure count before disable is required.

If the expected exception is transient and `count < 5`:

```python
delay = min(
    60.0,
    sync_interval() * (2 ** min(count, 5)),
)
```

then persist:

```text
realtime_failures=count
realtime_next_retry_at=now() + delay
last_error=str(exc)[:1000]
```

and commit.

The adapter must preserve the current exact backoff formula and threshold.

## 11. Unexpected-error policy

For any other exception from the single-session Sync backend:

1. log:

```text
Phase 3 binding failed for session %s
```

with exception information;

2. set:

```python
count = int(failures or 0) + 1
```

3. if `count >= 5`:
   - persist `realtime_failures=count`;
   - commit;
   - call `disable_realtime(..., "unexpected Phase 3 binding failure")`;

4. otherwise use the same hardened exponential delay formula:

```python
delay = min(
    60.0,
    sync_interval() * (2 ** min(count, 5)),
)
```

and persist:

```text
realtime_failures=count
realtime_next_retry_at=now() + delay
last_error="unexpected Phase 3 binding failure"
```

Then commit.

This deliberately preserves the late `sync_safety.py` behavior rather than the weaker pre-override implementation in `sync_api.py`.

## 12. Write/transaction semantics

Phase 6E preserves current polling write boundaries.

Each retry/failure-state update continues to:

- execute its update;
- commit before returning to the poll loop.

Terminal failure count is persisted before `disable_realtime`.

Phase 6E does not redesign these updates around repositories or larger transactions.

Any future transaction cleanup belongs to a separate migration slice.

## 13. Composition in sync_api.py

The current raw public poll implementation becomes private, or is replaced directly by explicit composition after its behavior has been covered.

The intended final shape is:

```python
_SYNC_POLL_SAFETY = SyncPollSafetyAdapter(
    sync_now=lambda db, chat_id, session_id: phase3_sync_now(
        db,
        chat_id,
        session_id,
    ),
    chat_lock=lambda chat_id: chat_job_lock(chat_id),
    disable_realtime=lambda db, chat_id, session_id, error: _phase3_disable(
        db,
        chat_id,
        session_id,
        error,
    ),
    expected_errors=(SillyTavernApiError, ValueError),
    sync_interval=lambda: PHASE3_SYNC_INTERVAL_SECONDS,
    now=time.time,
    log_warning=lambda message, *args, **kwargs: logging.warning(
        message,
        *args,
        **kwargs,
    ),
)


def phase3_sync_poll(db: sqlite3.Connection) -> None:
    _SYNC_POLL_SAFETY.poll(db)
```

Exact formatting may differ.

Call-time lambdas are preferred where the shared compatibility runtime still patches final collaborators after module execution.

The composition must not capture a new `_ORIGINAL_*` function.

## 14. SyncService and worker lifecycle

`SyncService` remains structurally unchanged.

Its compatibility composition remains:

```python
poll_backend=phase3_sync_poll
```

Therefore:

```text
_phase3_worker_loop
    |
    v
SyncService.poll
    |
    v
phase3_sync_poll
    |
    v
SyncPollSafetyAdapter
```

Phase 6E does not change:

- worker start/stop behavior;
- worker database reconnect behavior;
- worker rollback behavior;
- API configuration checks;
- realtime callback routing;
- `SyncService` public methods.

## 15. Runtime-loader retirement for Sync safety

Delete:

```text
bridge/sync_safety.py
```

Remove it from:

```text
RuntimeStage("safety_overrides", ...)
```

and remove:

```python
(
    "sync_safety.py",
    (
        "initialize_database_schema",
        "phase3_sync_poll",
    ),
)
```

from the public override allowlist.

After Phase 6E:

- `initialize_database_schema` is owned by `schema.py`;
- `phase3_sync_poll` is owned by `sync_api.py`;
- no Sync public callable is replaced by runtime load order;
- no Sync module remains in the runtime override allowlist.

## 16. Testing strategy

Implementation follows strict RED -> GREEN.

### 16.1 Ordinary polling-safety unit tests

Create:

```text
tests/test_sync_poll_safety.py
```

Import `SyncPollSafetyAdapter` directly without importing `bridge.runtime`.

Cover:

- candidates are ordered by oldest `last_checked_at`, then chat/session;
- locked chats are skipped;
- locked chats do not consume the attempt limit;
- polling scans past 32 locked candidates to an eligible candidate;
- queued durable jobs block Sync;
- scheduled durable jobs block Sync;
- running durable jobs block Sync;
- durable-job inspection failure releases the lock;
- durable-job inspection failure logs and skips;
- successful Sync releases the lock;
- expected transient error releases the lock;
- unexpected error releases the lock;
- expected non-transient error disables immediately;
- expected fifth failure persists count 5 before disabling;
- expected transient attempts 1–4 use the exact exponential backoff;
- retry error text is truncated to 1000 characters;
- unexpected failures 1–4 use the exact exponential backoff;
- unexpected fifth failure persists count 5 before disabling;
- unexpected disable reason is exactly `unexpected Phase 3 binding failure`;
- only acquired eligible candidates count toward `attempt_limit`;
- `sync_now` is invoked once per counted attempt;
- adapter has no forbidden runtime/sync/UI imports.

### 16.2 Sync integration tests

Migrate/strengthen the existing `tests/test_sync_audit.py` coverage.

Verify through the public runtime path:

- active chat lock skips realtime Sync;
- polling scans past 32 locked candidates;
- bounded poll prioritizes oldest binding;
- unexpected fifth failure disables;
- expected auth/non-transient failure disables;
- `SyncService.poll` still delegates through the canonical poll path;
- worker loop still uses injected `SyncService`.

Existing tests that patch `_ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL` must be migrated to patch the explicit adapter/backend boundary instead.

After cutover there must be no test dependency on `_ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL`.

### 16.3 Schema/startup tests

Preserve and strengthen:

```text
test_sync_startup_cleanup_removes_orphan_without_structural_ddl
```

The final test must prove:

- orphan `sync_bindings` are deleted by canonical `initialize_database_schema`;
- no new structural Sync DDL is emitted during recurring cleanup;
- no extra migration version is required;
- valid bindings remain intact.

### 16.4 Runtime architecture tests

Extend `tests/test_runtime_loader.py` to verify:

- `sync_safety.py` is absent from every runtime stage;
- `sync_safety.py` is absent from `RUNTIME_LOAD_REPORT`;
- no override allowlist contains `initialize_database_schema`;
- no override allowlist contains `phase3_sync_poll`;
- public `initialize_database_schema.__code__.co_filename` resolves to `schema.py`;
- public `phase3_sync_poll.__code__.co_filename` resolves to `sync_api.py`.

### 16.5 Source-boundary tests

Verify:

- `bridge/sync_safety.py` no longer exists;
- no `_ORIGINAL_SYNC_INITIALIZE_DATABASE_SCHEMA` remains;
- no `_ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL` remains;
- `sync_poll_safety.py` does not import `bridge.runtime`;
- `sync_poll_safety.py` does not import `bridge.sync_api`;
- `sync_poll_safety.py` does not import `bridge.sync_safety`;
- `sync_poll_safety.py` does not import Telegram/UI/recovery modules;
- no new runtime override allowlist entry is introduced.

## 17. Cutover sequencing

The transition must avoid a committed state where both the new polling adapter and late `sync_safety.py` apply hardening twice.

Required sequencing:

1. add ordinary polling-safety tests;
2. implement `SyncPollSafetyAdapter`;
3. test it directly;
4. add canonical startup-cleanup coverage;
5. move orphan cleanup into `schema.py`;
6. compose the adapter in `sync_api.py` in a transition-safe manner;
7. add final ownership/retirement RED tests;
8. atomically cut public `phase3_sync_poll` over to the adapter and delete `sync_safety.py`;
9. remove its runtime-loader module/allowlist;
10. migrate all tests away from `_ORIGINAL_*` patching;
11. run focused Sync/schema/runtime tests;
12. run full CI on the exact final head.

No task should leave both the explicit adapter and late runtime wrapper executing the same policy twice.

## 18. Out of scope

Phase 6E does not change:

- `SyncService` public interface;
- `phase3_sync_now`;
- conflict detection;
- checkpoint semantics;
- Sync snapshot import;
- realtime worker lifecycle;
- worker reconnect behavior;
- API authentication/client behavior;
- retry threshold of 5;
- attempt limit of 32;
- exponential backoff formula;
- maximum delay of 60 seconds;
- `chat_job_lock` implementation;
- job persistence architecture;
- schema migration versions;
- `persona_sync.py::load_personas`;
- `recovery.py`;
- Scene State;
- Director Goals;
- Memory Curator;
- general Phase 7 runtime-loader retirement.

No Phase 7 work is pre-authorized by this spec.

## 19. Acceptance criteria

Phase 6E is complete when all are true:

1. orphan Sync binding cleanup lives in canonical startup cleanup in `schema.py`;
2. no new schema migration or request-time DDL is introduced;
3. `SyncPollSafetyAdapter` exists as an ordinary-import component;
4. polling scans candidates in the existing fairness order;
5. there is no SQL `LIMIT 32` in the hardened poll query;
6. only acquired, durable-job-free candidates consume the 32-attempt budget;
7. locked candidates are skipped without consuming the budget;
8. durable queued/scheduled/running jobs block Sync;
9. durable-job inspection failure releases the lock and skips safely;
10. every acquired lock is released on every execution path;
11. expected non-transient failures disable immediately;
12. expected transient failures preserve exact hardened backoff semantics;
13. the fifth expected failure is persisted before disable;
14. unexpected failures preserve exact hardened backoff semantics;
15. the fifth unexpected failure is persisted before disable;
16. unexpected disable reason remains exactly `unexpected Phase 3 binding failure`;
17. `SyncService` remains structurally unchanged;
18. realtime worker behavior remains unchanged;
19. public `initialize_database_schema` is owned by `schema.py`;
20. public `phase3_sync_poll` is owned by `sync_api.py`;
21. `sync_safety.py` is deleted;
22. `_ORIGINAL_SYNC_INITIALIZE_DATABASE_SCHEMA` no longer exists;
23. `_ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL` no longer exists;
24. runtime loader contains no `sync_safety.py`;
25. no override allowlist contains either Sync safety public function;
26. no new public runtime override, `_ORIGINAL_*` capture, service locator, or load-order dependency is introduced;
27. full repository CI passes on the exact final head.

## 20. Resulting migration state

After Phase 6E, there are no Sync-related public runtime overrides.

The remaining known override debt is outside Sync, primarily:

- `persona_sync.py::load_personas`;
- `recovery.py` recovery/UI overrides.

That leaves the codebase closer to the Phase 7 prerequisite that the public override allowlist becomes empty before retiring the compatibility runtime loader.

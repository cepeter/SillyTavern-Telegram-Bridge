# Phase 6A — Scheduler / Durable-Job Safety Adapter Design

Date: 2026-09-20
Status: Proposed — awaiting written-spec review
Repository: `cepeter/SillyTavern-Telegram-Bridge`
Baseline: `e2bcc5fc54bee4bcf0dbb2a088aa9af930485bf5` (merged Phase 5E JobService)
Master design: `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`
Predecessor: `docs/superpowers/specs/2026-09-20-job-service-design.md`

## 1. Purpose

Phase 6A removes the scheduler/durable-job safety layer's dependence on late shared-namespace overrides while preserving the reliability behavior that layer currently supplies.

Today `bridge/scheduler_safety.py` is executed late by `runtime_loader.py` and replaces three public callables:

- `db_connect`
- `recover_jobs`
- `submit_durable_chat_job`

It also captures `_ORIGINAL_DB_CONNECT`.

Phase 5E created the explicit application boundary needed to retire that arrangement: `JobService` now owns durable enqueue, admission, lifecycle transitions, actor lookup, and recovery orchestration, and it already accepts explicit `recover_backend`, `submit_chat`, and `prepare_worker` collaborators.

Phase 6A therefore makes the remaining database/scheduler safety behavior ordinary, explicit infrastructure instead of runtime replacement behavior.

The governing rule is:

> Preserve every current durability and contention guarantee, but make the canonical implementation explicit and normally imported rather than installed by load order.

## 2. Scope

Phase 6A is intentionally limited to the scheduler/durable-job safety boundary.

It will:

1. make `bridge/scheduler_safety.py` an ordinary-import module rather than a runtime stage;
2. remove the `scheduler_safety.py` public-callable override allowlist;
3. eliminate `_ORIGINAL_DB_CONNECT`;
4. make one-time-per-database-path initialization plus lightweight subsequent connections part of the canonical database connection path;
5. make bounded durable-job recovery part of the canonical recovery repository primitive;
6. keep transient worker database startup requeue protection as an explicit `JobService.prepare_worker` collaborator;
7. keep the Phase 5E compatibility submission helper as a thin JobService delegate, without a later replacement;
8. add architecture and regression tests proving the safety behavior no longer depends on runtime execution order.

Phase 6A will not redesign scheduling, executors, job state semantics, database schema, business workers, or operation recovery.

## 3. Behavior that must not change

The following are binding invariants.

1. The first connection for a database path performs database-level initialization before that path is considered ready.
2. Database-level initialization keeps the existing ordering: auto-vacuum setup before WAL negotiation, optional vector-extension loading, then schema initialization/migrations.
3. Subsequent worker connections for the same path do not renegotiate WAL, auto-vacuum, extensions, or schema.
4. Every connection still receives the existing connection-local busy timeout, synchronous, temp-store, cache, foreign-key, and mmap pragmas appropriate to its role.
5. Different database paths are initialized independently.
6. Concurrent first-open attempts for the same database path cannot run database-level initialization twice.
7. Startup recovery requeues `running` and `scheduled` durable jobs.
8. Recovery returns queued work in creation order.
9. Recovery remains bounded to 128 rows per pass, including startup recovery.
10. Non-startup backlog recovery remains bounded to 128 rows.
11. A transient SQLite `locked` or `busy` error during worker execution may requeue a job only while that durable row is still `queued` or `scheduled`.
12. The worker requeue attempt uses the same database path as the originating job connection.
13. Worker requeue retry delays remain bounded to the current sequence: immediate, 250 ms, then 1 second.
14. Non-transient worker exceptions are not converted into requeue events.
15. A job that has already transitioned to `running` is not moved backwards by the worker-boot guard.
16. Rejected background admission still leaves the durable job queued.
17. Job lifecycle ownership remains in `JobService`.
18. Per-chat FIFO behavior, semaphore capacity, executor selection, backlog dispatch, and shutdown admission remain unchanged.
19. Existing operation-idempotency and native-edit recovery semantics remain unchanged.
20. No new public-callable runtime override, `_ORIGINAL_*` capture, runtime stage, or global current-service locator is introduced.

## 4. Current architecture problem

The current safety stage works because `runtime_loader.py` executes `scheduler_safety.py` after `database.py` and `main.py`.

Conceptually:

```text
database.py defines db_connect / recover_jobs
main.py defines submit_durable_chat_job
            |
            v
runtime_loader.py executes scheduler_safety.py late
            |
            +-- captures _ORIGINAL_DB_CONNECT
            +-- replaces db_connect
            +-- replaces recover_jobs
            +-- replaces submit_durable_chat_job
```

The behavior is reliable, but ownership is implicit. Reading `database.py` alone does not reveal the production `db_connect` implementation, and correctness depends on stage order.

That conflicts with the master migration target: important execution behavior must be ordinary composition, not hidden replacement.

## 5. Considered approaches

### 5.1 Selected: canonical database behavior + ordinary safety collaborators

Move the connection/recovery guarantees into their canonical infrastructure boundaries and retain only the worker guard as an explicit collaborator of JobService.

Shape:

```text
database.py
  |
  +-- canonical db_connect
  |     |
  |     +-- one-time-per-path initialization gate
  |     +-- lightweight subsequent connection
  |
  +-- canonical recover_jobs (bounded)
  |
main composition
  |
  +-- JobService(recover_backend=recover_jobs,
                 prepare_worker=durable_worker_guard.prepare,
                 submit_chat=BackgroundRuntime.submit_chat)
```

`scheduler_safety.py` becomes an ordinary-import utility containing explicit safety objects/helpers. It no longer executes inside `DEFAULT_RUNTIME_STAGES`.

This is selected because it removes the override chain without adding another facade over the same behavior.

### 5.2 Rejected: keep database.py unchanged and add a new wrapper service

A new database wrapper could preserve the current logic and be injected everywhere. However, many compatibility-runtime paths still rely on the canonical `db_connect` symbol. Migrating all of them in Phase 6A would broaden the PR into a database-context rewrite and increase regression risk.

### 5.3 Rejected: remove all Phase 6 safety overrides in one PR

Combining scheduler, Persona integrity, memory stale-guard, sync safety, and recovery overrides would merge unrelated failure domains. The master design explicitly permits Phase 6 to be decomposed. Phase 6A therefore removes only the scheduler/durable-job override cluster.

## 6. Canonical database connection design

The production `db_connect` defined by `database.py` becomes authoritative; no later module replaces it.

The current connection work is separated conceptually into two operations:

- **initialized open** — creates the SQLite handle, applies connection-local pragmas, applies database-level setup, loads the optional vector extension, and runs schema initialization;
- **lightweight open** — creates the SQLite handle and applies only connection-local pragmas.

An ordinary-import connection gate tracks which normalized database paths have completed initialized open.

Conceptual interface:

```python
class DatabaseConnectionGate:
    def __init__(self, initialize, open_lightweight):
        ...

    def connect(self, database_path: Path):
        ...
```

Required semantics:

1. Normalize each path with `expanduser().resolve()`.
2. Fast-path already-ready paths to lightweight open.
3. Serialize the first initialization per process with a lock.
4. Re-check readiness after acquiring the lock.
5. Mark a path ready only after initialized open succeeds.
6. If initialized open raises, leave the path unready so a later call can retry.
7. Keep readiness per path, not only for the default database.

The gate is process-local. Phase 6A does not create a cross-process migration lock or alter SQLite's own locking behavior.

## 7. Canonical recovery repository design

The duplicate `recover_jobs` implementation in the late safety module is removed.

The canonical repository primitive in `database.py` adopts the production behavior currently supplied by the safety override:

```text
if recover_running:
    running/scheduled -> queued

SELECT queued jobs
ORDER BY created_at
LIMIT 128
commit
return rows
```

The row shape remains exactly what `JobService.recover` already consumes:

```text
(job_id, chat_id, session_id, telegram_message_id, kind, payload_json)
```

No JSON decoding, worker resolution, or submission logic moves into the repository. Those remain JobService responsibilities.

## 8. Durable worker guard design

The transient worker database failure guard becomes an ordinary collaborator instead of hidden behavior installed by `submit_durable_chat_job` replacement.

Preferred shape:

```python
class DurableWorkerGuard:
    def __init__(self, open_requeue_connection, *, delays=(0.0, 0.25, 1.0)):
        ...

    def prepare(self, db, job_id, worker):
        ...
```

`prepare` captures the actual database path from `PRAGMA database_list` and returns a wrapped worker.

If the worker raises:

- if the exception is not `sqlite3.OperationalError`, re-raise without requeue work;
- if its message contains neither `locked` nor `busy`, re-raise without requeue work;
- otherwise attempt the existing bounded requeue sequence using a lightweight connection to the captured database path;
- update only rows whose state is `queued` or `scheduled`;
- store the bounded `last_error` text and update timestamp;
- close each requeue connection;
- always re-raise the original worker exception.

The state predicate is essential: once `JobService.start` has moved the row to `running`, the safety wrapper must not undo that lifecycle decision.

The guard remains injected through the existing Phase 5E `JobService.prepare_worker` seam. No JobService public API change is required.

## 9. Submission compatibility

The canonical compatibility helper `submit_durable_chat_job` remains only for direct legacy callers/tests.

Its behavior is:

```text
compatibility helper
      |
      v
JobService.submit
      |
      +-- prepare_worker collaborator
      +-- BackgroundRuntime admission
      +-- scheduled transition on acceptance
```

Phase 6A removes the late `scheduler_safety.py` replacement of this helper. There must be one helper definition/owner at runtime, not a core definition plus a safety replacement.

Production update handling continues to call `services.jobs.submit` directly as established by Phase 5E.

## 10. Runtime-loader changes

`runtime_loader.py` removes `scheduler_safety.py` from `DEFAULT_RUNTIME_STAGES.safety_overrides`.

It also removes this allowlist entry:

```text
scheduler_safety.py:
  db_connect
  recover_jobs
  submit_durable_chat_job
```

`scheduler_safety.py` is imported normally only where its explicit collaborators are needed.

The remaining Phase 6B+ safety stages are unchanged.

After Phase 6A, the runtime override report must contain no `scheduler_safety.py` entry.

## 11. Composition

The production composition root keeps the Phase 5E JobService contract.

Conceptually:

```python
worker_guard = DurableWorkerGuard(open_worker_database)

jobs = JobService(
    enqueue_backend=enqueue_job,
    actor_backend=job_actor_id,
    schedule_backend=mark_job_scheduled,
    start_backend=mark_job_running,
    finish_backend=finish_job,
    recover_backend=recover_jobs,
    submit_chat=background.submit_chat,
    prepare_worker=worker_guard.prepare,
)
```

`BridgeServices.db_factory` continues to represent the canonical database connection callable.

Phase 6A does not add another service-container field unless implementation proves one is required for testability. The preferred design is to keep connection safety as infrastructure behind the existing `db_factory` boundary.

## 12. Error handling

### Database initialization failure

If first-open initialization fails, propagate the error and do not mark the path initialized. A later attempt may retry.

### Recovery persistence failure

Recovery SQL errors continue to propagate to the caller. JobService does not reinterpret repository failures.

### Worker transient SQLite failure

Attempt bounded requeue, log each unsuccessful requeue attempt, then re-raise the original worker error.

If all requeue attempts fail, log that the durable job remains recoverable on restart.

### Non-transient worker failure

Do not perform the scheduler-safety requeue path. Existing worker/JobService failure handling remains authoritative.

## 13. Testing strategy

Use strict RED -> GREEN TDD.

### 13.1 Runtime architecture tests

Add/adjust tests proving:

- `scheduler_safety.py` is not in any `DEFAULT_RUNTIME_STAGES` module list;
- no runtime override allowlist mentions `scheduler_safety.py`;
- the runtime load report contains no scheduler-safety override entry;
- `scheduler_safety.py` can be imported as an ordinary Python module;
- no `_ORIGINAL_DB_CONNECT` symbol remains.

### 13.2 Connection-gate unit tests

Test with fake initialized/lightweight openers:

- first open uses initialized path;
- second open for the same path uses lightweight path;
- different paths initialize independently;
- initialization failure does not poison readiness;
- simultaneous first opens do not initialize the same path twice.

### 13.3 Database integration tests

Using temporary SQLite files, prove:

- schema is available after first connection;
- subsequent connections use the same database without rerunning database-level setup;
- worker/local pragmas remain applied;
- multiple temporary database paths are independently initialized.

Tests should avoid asserting implementation details that are not architectural guarantees.

### 13.4 Recovery tests

Create more than 128 durable rows and prove:

- startup recovery resets `running`/`scheduled` rows to `queued`;
- the returned batch contains at most 128 rows;
- ordering is oldest-first;
- non-startup recovery is also bounded;
- a second pass can continue draining remaining rows.

### 13.5 Worker-guard tests

With fake/lightweight requeue connections, prove:

- locked and busy errors trigger requeue attempts;
- the captured originating database path is used;
- retries follow the bounded delay sequence;
- non-transient OperationalError does not requeue;
- non-SQLite exceptions do not requeue;
- running jobs are not moved backwards;
- the original worker exception is always re-raised;
- requeue connections are closed.

### 13.6 JobService regression tests

Retain and extend Phase 5E tests proving:

- accepted submit schedules once;
- rejected submit stays queued;
- `prepare_worker` is invoked before background admission;
- recovered submission uses the same guarded admission path;
- worker DB startup failure remains recoverable.

### 13.7 Full regression gate

The exact final branch head must pass the complete repository CI suite before the PR is marked ready.

## 14. Source-boundary guards

Phase 6A should add focused source assertions so future changes cannot silently recreate the architecture being removed.

At minimum:

- `runtime_loader.py` must not list `scheduler_safety.py`;
- no scheduler runtime override allowlist entry exists;
- `scheduler_safety.py` contains no `_ORIGINAL_DB_CONNECT`;
- `scheduler_safety.py` does not define a replacement `submit_durable_chat_job`;
- JobService remains ordinary-importable and outside runtime stages.

These guards should be narrow enough not to freeze unrelated implementation details.

## 15. Migration order

Implementation should be staged so every commit has a clear claim.

1. Add RED architecture/safety tests describing the ordinary-import target.
2. Convert scheduler safety helpers into ordinary explicit collaborators.
3. Make canonical `database.py` connection behavior preserve one-time initialization plus lightweight later opens.
4. Make canonical `recover_jobs` preserve the 128-row production bound.
5. Compose the durable worker guard explicitly into JobService.
6. Remove the late submission override.
7. Remove `scheduler_safety.py` from runtime stages and its allowlist.
8. Run focused tests, source review, and exact-head full CI.
9. Open/mark the PR ready only after exact-head verification.

No Phase 6B work is included in these commits.

## 16. Out of scope

Phase 6A does not:

- redesign `BackgroundRuntime`;
- change generation or utility executor sizes;
- change semaphore limits;
- change per-chat FIFO or chat locks;
- change durable job states or schema;
- change `processed_updates`;
- move worker business logic into JobService;
- redesign failed-turn retry;
- change operation phases or callback idempotency;
- change native edit local-commit recovery;
- remove `recovery.py` overrides;
- remove `sync_safety.py` overrides;
- remove `state_integrity.py` overrides;
- redesign Persona or Memory safety;
- retire `runtime_loader.py`;
- introduce an ORM;
- add cross-process database initialization coordination.

## 17. Acceptance criteria

Phase 6A is complete when all of the following are true:

- `scheduler_safety.py` is ordinary-imported and absent from `DEFAULT_RUNTIME_STAGES`;
- the runtime override allowlist has no scheduler-safety entry;
- `_ORIGINAL_DB_CONNECT` is gone;
- production `db_connect` is canonical and preserves one-time-per-path initialization plus lightweight subsequent opens;
- production `recover_jobs` is canonical and bounded to 128 rows per pass;
- JobService receives worker requeue safety through its explicit `prepare_worker` collaborator;
- no late replacement of `submit_durable_chat_job` remains;
- transient worker SQLite busy/locked recovery behavior is preserved;
- startup and backlog durable-job recovery semantics are preserved;
- BackgroundRuntime queue/order/shutdown behavior is unchanged;
- no new runtime override, runtime stage, `_ORIGINAL_*` capture, or service locator is introduced;
- focused architecture/database/job tests pass;
- the complete exact-head CI suite passes.

## 18. Follow-on boundary

After Phase 6A merges, Phase 6 should continue with another independently designed and reviewed slice. The likely next candidate is Persona/state-integrity safety, followed by sync safety and then the more entangled recovery overrides.

Phase 6A does not pre-authorize or pre-design those later slices.

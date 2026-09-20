# Phase 6C — Hindsight Stale-Memory Guard Design

**Date:** 2026-09-20  
**Status:** Proposed — awaiting written-spec review  
**Repository:** `cepeter/SillyTavern-Telegram-Bridge`  
**Baseline:** `9d623ee2edaba8e38169dde0f508a86a21eb9afd` (merged Phase 6B)  
**Master architecture:** `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`

## 1. Purpose

Phase 6C removes the remaining Hindsight-specific late overrides from `bridge/state_integrity.py` and replaces them with an ordinary explicit stale-memory guard around the canonical Hindsight backend.

The phase must preserve the current safety guarantees:

1. queued Hindsight retains cannot overwrite newer transcript state;
2. queued retains cannot revive session memory after a successful purge;
3. deleted sessions are not retained later by background work;
4. Hindsight purge remains fail-closed;
5. local purge invalidation state changes only after successful remote purge;
6. Hindsight document mappings are cleared after successful purge;
7. per-session retain/purge operations remain serialized;
8. post-retain extension hooks keep their current semantics;
9. application workflows continue to use `MemoryService`;
10. Live Sync behavior is not changed in Phase 6C.

The migration is successful only if those guarantees survive without `_ORIGINAL_RETAIN_SESSION_MEMORY_WORKER`, `_ORIGINAL_PURGE_HINDSIGHT_SESSION`, or late replacement of `retain_session_memory` / `purge_hindsight_session`.

## 2. Current state

After Phase 6B, `state_integrity.py` still captures three earlier callables:

- `_ORIGINAL_APPLY_SYNC_SNAPSHOT`;
- `_ORIGINAL_RETAIN_SESSION_MEMORY_WORKER`;
- `_ORIGINAL_PURGE_HINDSIGHT_SESSION`.

Its Hindsight layer currently owns:

- `_hindsight_epoch_key`;
- `_hindsight_memory_epoch`;
- `_hindsight_conversation_snapshot`;
- a replacement `_retain_session_memory`;
- a replacement `retain_session_memory`;
- a replacement `purge_hindsight_session`.

Those functions are executed later than `memory.py`, so behavior still depends on shared-namespace execution order.

`memory.py` already owns the raw Hindsight implementation:

- per-session lock registry;
- Hindsight client creation and closing;
- deterministic document IDs;
- remote document deletion;
- `_retain_with_client`;
- raw `_retain_session_memory`;
- raw `retain_session_memory`;
- raw `purge_hindsight_session`;
- Hindsight document mapping persistence.

`MemoryService` already owns the application-level memory workflow and delegates retain/purge to injected collaborators.

## 3. Selected approach

Introduce an ordinary-import module:

`bridge/hindsight_integrity.py`

with a focused `HindsightStaleGuard`.

The canonical shape becomes:

```text
MemoryService
    |
    v
memory.py public retain/purge
    |
    v
HindsightStaleGuard
    |
    v
raw Hindsight backend in memory.py
```

The guard owns stale-snapshot and purge-invalidation policy. `memory.py` continues to own Hindsight-specific infrastructure and persistence mechanics.

This matches the master Phase 6 target:

```text
MemoryBackend
    |
    v
StaleGuardMemoryBackend
    |
    v
HindsightMemoryBackend
```

without introducing a new service or changing `MemoryService`'s public interface.

## 4. Rejected alternatives

### 4.1 Put stale protection directly into MemoryService

Rejected because `MemoryService` is an application-service boundary and should not become Hindsight-specific. It would also leave direct compatibility callers with weaker safety unless every call was forced through the service.

### 4.2 Fold all stale logic directly into memory.py without a guard

Rejected because it would remove the runtime override but not establish the explicit decorator/adaptor boundary required by Phase 6. The safety policy would remain mixed with Hindsight I/O.

### 4.3 Keep state_integrity.py and only reduce its responsibilities

Rejected because correctness would still depend on late execution order and captured `_ORIGINAL_*` functions, which directly conflicts with the migration invariants.

## 5. Component boundary

`HindsightStaleGuard` is ordinary-import infrastructure. It must not import:

- `bridge.runtime`;
- `memory.py`;
- Telegram/UI modules;
- `state_integrity.py`.

Dependencies are injected.

Conceptual interface:

```python
@dataclass(frozen=True)
class HindsightStaleGuard:
    open_db: Callable[[], sqlite3.Connection]
    session_lock: Callable[[str, str], AbstractContextManager[object]]
    memory_enabled: Callable[[sqlite3.Connection, str], bool]
    submit_background: Callable[..., object]
    retain_backend: Callable[..., None]
    purge_backend: Callable[..., int]
    read_epoch: Callable[[sqlite3.Connection, str, str], int]
    write_successful_purge_state: Callable[[sqlite3.Connection, str, str], None]
    snapshot: Callable[[sqlite3.Connection, str, str], tuple[str, str]]
    run_post_retain_hooks: Callable[..., None]

    def retain(
        self,
        db,
        chat_id,
        session,
        fields,
    ) -> None: ...

    def purge(
        self,
        db,
        chat_id,
        session_id,
    ) -> int: ...
```

Exact private helper names may differ, but the boundary must remain explicit and ordinary-import safe.

## 6. Canonical ownership in memory.py

`memory.py` remains the canonical runtime owner of:

- `retain_session_memory`;
- `purge_hindsight_session`;
- Hindsight client lifecycle;
- deterministic Hindsight document IDs;
- `_retain_with_client`;
- remote purge implementation;
- document-mapping persistence;
- per-session Hindsight lock.

The existing raw implementations should become private backend functions where needed, for example:

```python
def _retain_session_memory_backend(...): ...
def _purge_hindsight_session_backend(...): ...
```

Then `memory.py` composes one guard:

```python
_HINDSIGHT_STALE_GUARD = _HindsightStaleGuard(
    ...
)
```

and exposes stable public delegates:

```python
def retain_session_memory(...):
    return _HINDSIGHT_STALE_GUARD.retain(...)

def purge_hindsight_session(...):
    return _HINDSIGHT_STALE_GUARD.purge(...)
```

`compatibility_memory_service()` continues to bind those final public functions at call time.

No new `BridgeServices` field is required.

## 7. Retain data flow

### 7.1 Foreground retain

When `retain_session_memory(db, chat_id, session, fields)` is called:

1. check current memory mode;
2. if enabled, capture a bounded transcript snapshot;
3. calculate a deterministic transcript fingerprint;
4. read the current persisted purge epoch;
5. queue the guarded background worker with:
   - chat ID;
   - session copy;
   - character name;
   - snapshot content;
   - snapshot fingerprint;
   - snapshot epoch;
6. run post-retain hooks with the current extension-registry semantics.

The post-retain hook call remains outside the memory-enabled branch so memory-off behavior stays unchanged: Hindsight retain is skipped but independent post-retain listeners may still run.

### 7.2 Background guarded retain

The background worker:

1. derives the session ID;
2. acquires the existing per-session Hindsight `RLock`;
3. opens a fresh DB connection;
4. verifies the session still exists;
5. reads current purge epoch;
6. rebuilds the current bounded transcript and fingerprint;
7. closes the DB connection;
8. returns normally without backend I/O if:
   - session no longer exists;
   - snapshot epoch differs from current epoch;
   - snapshot hash differs from current transcript hash;
   - effective payload is empty;
9. otherwise invokes the raw Hindsight retain backend.

Stale work is not an exception. It is an expected skip.

## 8. Snapshot semantics

The transcript snapshot algorithm is preserved exactly:

- select `role, content, created_at`;
- filter by `chat_id` and `session_id`;
- order by `created_at DESC, rowid DESC`;
- limit by `HINDSIGHT_RETAIN_MAX_MESSAGES`;
- reverse to chronological order;
- serialize to JSON with role/content/UTC timestamp;
- hash only role/content pairs using SHA-256 and compact JSON separators.

The fingerprint intentionally ignores timestamps. A transcript edit changes the fingerprint; irrelevant timestamp representation changes do not.

## 9. Purge data flow

When `purge_hindsight_session(db, chat_id, session_id)` is called:

1. acquire the existing per-session Hindsight `RLock`;
2. invoke the raw fail-closed remote Hindsight purge backend;
3. if the backend raises, propagate the existing failure and perform no local invalidation change;
4. after backend success, in the existing bounded write-transaction helper:
   - delete rows from `hindsight_documents` for the session;
   - increment the persisted session purge epoch;
   - commit;
5. return the raw backend's deleted-document count.

This ordering is a hard invariant:

```text
remote purge success
    BEFORE
mapping delete + epoch increment
```

A failed remote purge must not:

- clear local document mappings;
- increment the epoch;
- make queued stale retains look invalidated when the remote memory still exists.

## 10. Epoch persistence

The epoch key remains:

```text
hindsight_epoch:<chat_id>:<session_id>
```

Epoch reads remain defensive:

- missing value -> 0;
- malformed value -> 0;
- negative value -> clamped to 0.

A successful purge increments the current epoch by exactly one.

The epoch remains persisted in the existing `meta` table. Phase 6C introduces no schema migration.

## 11. Locking

The existing `hindsight_session_lock(chat_id, session_id)` remains the serialization primitive.

Both guarded background retain and purge acquire the same per-session `RLock`.

This preserves the critical ordering:

- a retain cannot pass its stale check concurrently with a purge of the same session;
- a purge cannot interleave between stale validation and raw retain dispatch for that session.

The lock remains per-session, so unrelated sessions can proceed concurrently.

## 12. Post-retain extension hooks

Current extension behavior must be preserved.

After Phase 6C:

- `retain_session_memory` still invokes `run_post_retain_hooks`;
- hooks still run when Hindsight memory mode is off;
- one failing hook still does not block later hooks;
- hook failures are still isolated by `extension_registry.py`;
- Memory Curator and Scene State integration behavior remains unchanged.

The guard may receive `run_post_retain_hooks` as an injected collaborator rather than import the registry directly.

## 13. Error-handling semantics

### Stale retain

Session deletion, epoch mismatch, transcript mismatch, and empty payload are normal skips.

No retry or user-visible failure is introduced.

### Hindsight retain backend failure

Existing raw backend behavior remains unchanged: failure is logged and does not crash the foreground caller.

### Remote purge failure

The existing fail-closed behavior is preserved:

```text
RuntimeError("Hindsight session memory cleanup failed")
```

Local epoch and document mappings remain unchanged.

### Local invalidation failure after successful remote purge

The DB failure propagates from the local transaction boundary. Phase 6C does not invent compensating remote writes.

This case is rare but must remain visible because remote memory has already been removed while local bookkeeping failed.

## 14. state_integrity.py after Phase 6C

Remove all Hindsight-specific captures and definitions:

- `_ORIGINAL_RETAIN_SESSION_MEMORY_WORKER`;
- `_ORIGINAL_PURGE_HINDSIGHT_SESSION`;
- `_hindsight_epoch_key`;
- `_hindsight_memory_epoch`;
- `_hindsight_conversation_snapshot`;
- replacement `_retain_session_memory`;
- replacement `retain_session_memory`;
- replacement `purge_hindsight_session`.

Leave Live Sync behavior unchanged:

- `_ORIGINAL_APPLY_SYNC_SNAPSHOT`;
- replacement `apply_sync_snapshot`.

The file should become a Live Sync integrity shim only.

## 15. Runtime loader

The `state_integrity.py` allowlist changes from:

```python
(
    "retain_session_memory",
    "purge_hindsight_session",
    "apply_sync_snapshot",
)
```

to:

```python
(
    "apply_sync_snapshot",
)
```

After Phase 6C:

- `retain_session_memory.__code__.co_filename` resolves to `memory.py`;
- `purge_hindsight_session.__code__.co_filename` resolves to `memory.py`;
- the runtime load report contains no Hindsight override from `state_integrity.py`.

No new runtime stage or allowlist entry is introduced.

## 16. MemoryService

`MemoryService` does not change structurally.

Its existing collaborators remain:

- recall context;
- summary generation/state;
- retain session;
- purge session memory.

Startup and compatibility composition continue to inject the canonical public `memory.py` retain/purge functions.

Phase 6C therefore moves infrastructure safety ownership without altering application-service semantics.

## 17. Testing strategy

Implementation is strict RED -> GREEN.

### 17.1 New ordinary unit tests

Create `tests/test_hindsight_integrity.py`.

The tests must import `HindsightStaleGuard` directly and must not import `bridge.runtime`.

Cover:

- current snapshot reaches retain backend;
- transcript mismatch skips retain;
- epoch mismatch skips retain;
- missing session skips retain;
- empty snapshot skips backend;
- retain and purge serialize on the same session lock;
- successful purge calls backend before local invalidation;
- successful purge preserves backend return count;
- failed purge does not run local invalidation;
- purge exception propagates;
- post-retain hooks run when memory is off;
- post-retain hooks run after normal scheduling.

### 17.2 Native Hindsight integration tests

Create or migrate into `tests/test_memory_native_backend.py`.

Cover:

- canonical public retain schedules guarded worker;
- canonical public purge clears mapping after remote success;
- successful purge advances epoch;
- failed remote purge preserves epoch and mappings;
- stale queued retain after transcript edit is rejected;
- stale queued retain after purge is rejected;
- deleted session is not retained;
- document ID and mapping semantics are unchanged;
- Memory Curator post-retain behavior remains active.

### 17.3 Runtime architecture tests

Extend `tests/test_runtime_loader.py`:

- Hindsight retain owner is `memory.py`;
- Hindsight purge owner is `memory.py`;
- `state_integrity.py` allowlist has only `apply_sync_snapshot`;
- runtime report has no retain/purge override.

### 17.4 Source guards

Add assertions that:

- `state_integrity.py` contains no Hindsight original captures;
- `state_integrity.py` does not define retain/purge or Hindsight epoch/snapshot helpers;
- `hindsight_integrity.py` does not import `bridge.runtime`;
- no new `_ORIGINAL_*` Hindsight capture appears elsewhere.

### 17.5 Existing tests

Migrate Hindsight stale-retain and purge-invalidation coverage out of `tests/test_state_integrity.py`.

Keep the Live Sync test there for Phase 6D.

Keep existing `MemoryService`, Memory Curator, extension-registry, memory deletion, and recovery tests green.

## 18. Migration order

1. add failing ordinary guard unit tests;
2. implement `HindsightStaleGuard`;
3. add failing native/runtime ownership tests;
4. split raw Hindsight retain/purge backends in `memory.py`;
5. compose the explicit guard in `memory.py`;
6. route canonical public retain/purge through the guard;
7. migrate stale-memory integration tests;
8. remove Hindsight captures/replacements from `state_integrity.py`;
9. shrink runtime-loader allowlist;
10. run focused suites and architecture/source guards;
11. run full exact-head CI;
12. mark the PR Ready only after exact-head success and review.

## 19. Out of scope

Phase 6C does not change:

- Live Sync `apply_sync_snapshot` behavior;
- `sync_safety.py`;
- sync backoff/conflict handling;
- Hindsight recall behavior;
- memory search UI;
- explicit `remember_fact` semantics;
- Memory Curator algorithm;
- Scene State behavior;
- summary generation;
- document ID format;
- Hindsight tags;
- bank ID format;
- Hindsight API/client configuration;
- database schema;
- `MemoryService` public interface;
- runtime loader retirement;
- recovery overrides.

No Phase 6D or later work is pre-authorized by this spec.

## 20. Acceptance criteria

Phase 6C is complete when all are true:

1. `HindsightStaleGuard` exists in an ordinary-import module;
2. `memory.py` is the canonical public owner of retain and purge;
3. stale transcript retains are rejected;
4. stale post-purge retains are rejected;
5. deleted sessions are not retained;
6. retain/purge remain serialized per session;
7. failed remote purge preserves local epoch and mappings;
8. successful purge clears mappings and increments epoch;
9. post-retain hook semantics are preserved;
10. `MemoryService` behavior and interface are unchanged;
11. `state_integrity.py` has no Hindsight captures or replacements;
12. its allowlist contains only `apply_sync_snapshot`;
13. Live Sync code is unchanged;
14. no new runtime override, `_ORIGINAL_*`, runtime stage, or load-order dependency is introduced;
15. full repository CI passes on the exact final head.

## 21. Next phase

After Phase 6C merges, the next architectural slice is Phase 6D: extract Live Sync integrity/safety behavior and retire the remaining `state_integrity.py` public override.

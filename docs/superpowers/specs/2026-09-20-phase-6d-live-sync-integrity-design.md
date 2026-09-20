# Phase 6D — Live Sync Integrity Adapter Design

**Date:** 2026-09-20  
**Status:** Proposed — awaiting written-spec review  
**Repository:** `cepeter/SillyTavern-Telegram-Bridge`  
**Baseline:** `e23d9750610bd2b6987d3a59933ff574f1b0523f` (merged Phase 6C cleanup)  
**Master architecture:** `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`

## 1. Purpose

Phase 6D removes the final public override from `bridge/state_integrity.py` by replacing the late-loaded `apply_sync_snapshot` wrapper with an ordinary explicit integrity adapter composed by `sync_core.py`.

The phase must preserve current Live Sync import semantics exactly:

1. validated remote snapshots continue to be imported by the existing canonical sync backend;
2. an explicitly empty `persona` field clears `persona_id`;
3. an absent `persona` field leaves the existing persona unchanged;
4. an explicitly empty `world_info` field clears `world_file`;
5. an absent `world_info` field leaves the existing world assignment unchanged;
6. valid non-empty persona/world values remain handled by the raw snapshot backend;
7. generation settings, messages, response variants, and transcript hash behavior remain unchanged;
8. the final imported session is reloaded before Hindsight refresh;
9. post-import Hindsight refresh remains best-effort and failure-isolated;
10. the imported transcript hash returned by the raw backend is returned unchanged;
11. `SyncService` public structure and application workflow remain unchanged;
12. `sync_safety.py` remains completely out of scope.

Phase 6D succeeds when `state_integrity.py` is deleted, `sync_core.py` is the canonical public owner of `apply_sync_snapshot`, and no runtime override or `_ORIGINAL_APPLY_SYNC_SNAPSHOT` chain remains.

## 2. Current state

After Phase 6C, `state_integrity.py` contains only:

- `_ORIGINAL_APPLY_SYNC_SNAPSHOT = apply_sync_snapshot`;
- a replacement `apply_sync_snapshot(...)`.

The replacement currently:

1. calls the original `sync_core.py` implementation;
2. explicitly clears `persona_id` when `persona` is present but empty;
3. explicitly clears `world_file` when `world_info` is present but empty;
4. reloads the final session;
5. attempts `retain_session_memory` using final character-card fields;
6. logs and isolates failures in the post-import memory refresh;
7. returns the original imported hash.

The behavior is correct, but ownership still depends on shared-namespace load order and a captured `_ORIGINAL_*` function.

The canonical raw implementation lives in `sync_core.py` and already owns:

- validated snapshot metadata application;
- valid non-empty Persona updates;
- valid non-empty world-file updates;
- generation settings import;
- transcript replacement;
- response-variant replacement;
- commit behavior;
- transcript hash generation.

`sync_api.py::phase3_sync_now` calls `apply_sync_snapshot` on the remote-wins path.

`SyncService` already owns the application-level Sync workflow through its injected `sync_now_backend=phase3_sync_now`.

## 3. Selected approach

Introduce an ordinary-import module:

`bridge/sync_integrity.py`

with a focused `SyncSnapshotIntegrityAdapter`.

The target shape is:

```text
SyncService
    |
    v
phase3_sync_now
    |
    v
sync_core.py::apply_sync_snapshot
    |
    v
SyncSnapshotIntegrityAdapter
    |
    v
_apply_sync_snapshot_backend
```

`sync_core.py` remains the canonical public snapshot owner. The existing raw implementation becomes private, and the public `apply_sync_snapshot` delegates through the adapter.

This preserves the existing Sync workflow while replacing load-order composition with an explicit ordinary Python boundary.

## 4. Rejected alternatives

### 4.1 Fold explicit-clear and memory-refresh behavior directly into sync_core.py

Rejected because it would remove the late override but mix integrity policy and best-effort post-import side effects into the raw snapshot persistence implementation.

Phase 6 is specifically replacing hardening overrides with explicit adapters/decorators. A focused adapter makes that responsibility independently testable and keeps the raw backend narrow.

### 4.2 Move snapshot integrity into SyncService

Rejected because `apply_sync_snapshot` is an internal backend operation inside `phase3_sync_now`, not an application-level use case exposed by `SyncService`.

Adding a snapshot-import method to `SyncService` would unnecessarily restructure the Sync workflow and create more migration risk than required for Phase 6D.

### 4.3 Keep state_integrity.py as an ordinary helper

Rejected because the file's only remaining purpose is the late override itself. Keeping it after the override is removed would preserve an obsolete compatibility layer with no distinct architectural responsibility.

## 5. Component boundary

Create:

`bridge/sync_integrity.py`

with:

```python
@dataclass(frozen=True)
class SyncSnapshotIntegrityAdapter:
    apply_backend: Callable[..., str]
    update_session: Callable[..., None]
    load_session: Callable[..., dict[str, str]]
    retain_memory: Callable[..., None]
    card_fields: Callable[[str], dict[str, str]]
    default_model: str

    def apply(
        self,
        db,
        chat_id,
        session,
        metadata,
        messages,
        variants,
    ) -> str:
        ...
```

Exact type annotations may be more precise in implementation, but the dependency direction is fixed.

The adapter must not import:

- `bridge.runtime`;
- `state_integrity.py`;
- `sync_core.py`;
- `sync_api.py`;
- `sync_safety.py`;
- Telegram/UI modules.

All behavior-specific collaborators are injected.

## 6. Canonical ownership in sync_core.py

`sync_core.py` remains the canonical runtime owner of `apply_sync_snapshot`.

Rename the existing raw implementation to a private backend, for example:

```python
def _apply_sync_snapshot_backend(
    db,
    chat_id,
    session,
    metadata,
    messages,
    variants,
) -> str:
    ...
```

Its behavior must remain statement-equivalent to the current merged implementation.

Then compose one adapter:

```python
_SYNC_SNAPSHOT_INTEGRITY = _SyncSnapshotIntegrityAdapter(
    apply_backend=_apply_sync_snapshot_backend,
    update_session=update_session,
    load_session=load_session,
    retain_memory=retain_session_memory,
    card_fields=card_fields_from_file,
    default_model=DEFAULT_MODEL,
)
```

and expose the stable public delegate:

```python
def apply_sync_snapshot(
    db,
    chat_id,
    session,
    metadata,
    messages,
    variants,
) -> str:
    return _SYNC_SNAPSHOT_INTEGRITY.apply(
        db,
        chat_id,
        session,
        metadata,
        messages,
        variants,
    )
```

If runtime tests patch final shared-runtime collaborators, composition may use narrow call-time lambdas where required to preserve current testability. Such indirection must remain local to `sync_core.py`; no new service locator is introduced.

## 7. Raw snapshot backend semantics

The raw backend remains responsible for the current validated import behavior:

- title import;
- safe character-file update;
- model import;
- valid non-empty Persona update;
- valid non-empty world-info update;
- author note;
- system prompt;
- response language normalization;
- generation settings import;
- response-variant replacement;
- transcript replacement;
- commit;
- transcript hash calculation.

Phase 6D must not change validation rules or serialization formats.

In particular:

- non-empty Persona values continue to require `get_persona(persona_id)`;
- world entries continue to require `safe_world_path`;
- stored world files continue to use `encode_world_files`;
- generation-setting parsing behavior is unchanged;
- transcript order and response-variant reconstruction are unchanged.

## 8. Explicit-clear semantics

The adapter handles only cases the raw backend intentionally ignores because an empty value cannot be distinguished from "do not update" there.

### Persona

If:

```python
"persona" in metadata
```

and:

```python
not str(metadata.get("persona") or "").strip()
```

then:

```python
persona_id=""
```

must be written after the raw backend succeeds.

If `persona` is absent, the adapter must not modify `persona_id`.

If `persona` is non-empty, the adapter must not duplicate the raw backend's Persona validation/update logic.

### World info

If:

```python
"world_info" in metadata
```

then normalize exactly as today:

```python
raw_worlds = metadata.get("world_info")
candidates = (
    raw_worlds
    if isinstance(raw_worlds, list)
    else ([raw_worlds] if raw_worlds else [])
)
```

If `candidates` is empty, write:

```python
world_file=""
```

If `world_info` is absent, do not modify `world_file`.

If candidates are non-empty, do not duplicate the raw backend's safe-world filtering or encoding behavior.

### Combined update

When one or both explicit clears are required, they should be collected into one update mapping and applied with one `update_session` call, preserving current behavior.

## 9. Import data flow

The final remote-wins flow remains:

```text
phase3_sync_now
    |
    v
apply_sync_snapshot
    |
    v
SyncSnapshotIntegrityAdapter.apply
    |
    +-- _apply_sync_snapshot_backend
    |      |
    |      +-- validated metadata updates
    |      +-- generation settings
    |      +-- messages
    |      +-- response variants
    |      +-- commit
    |      +-- imported hash
    |
    +-- explicit metadata clears
    |
    +-- reload final session
    |
    +-- best-effort Hindsight refresh
    |
    v
return imported hash
```

`sync_api.py::phase3_sync_now` remains structurally unchanged.

After import it still:

1. receives the imported hash;
2. writes sync state with direction `sillytavern_api_to_bridge`;
3. resets realtime failures;
4. returns the existing user-visible result.

No conflict/checkpoint/backoff logic moves into the adapter.

## 10. Post-import memory refresh

After the raw backend and explicit clears succeed:

1. reload the session with `load_session(db, chat_id, session_id, default_model)`;
2. calculate card fields from the final session's `character_file`;
3. call `retain_session_memory` using that reloaded session and card fields.

The adapter must use the final stored session, not the pre-import `session` argument, because the raw backend may change character/model/Persona/world metadata.

The memory refresh remains best-effort and occurs after the snapshot import is already committed.

## 11. Error handling

### Raw snapshot failure

Any exception from the raw backend propagates.

No explicit-clear update or memory refresh runs after raw backend failure.

### Explicit-clear update failure

Any exception from `update_session` propagates.

This is part of applying the requested remote state and therefore is not optional.

### Session reload / card-fields / memory-retain failure

These failures are isolated together by the existing best-effort boundary.

The adapter logs:

```text
Could not refresh Hindsight after Live Sync import
```

with exception information and still returns the imported hash.

Phase 6D does not change memory retry behavior.

### Imported hash

The adapter returns exactly the hash returned by `apply_backend`.

It does not recompute or normalize the hash.

## 12. SyncService

`SyncService` remains structurally unchanged.

Its existing responsibilities remain:

- status;
- sync-now;
- realtime toggling;
- polling;
- expected-error handling around sync-now;
- realtime disable behavior.

Its existing `sync_now_backend=phase3_sync_now` composition is already the correct application boundary.

Phase 6D introduces no snapshot method, no new service dependency, and no new public API.

## 13. Runtime loader

Delete `state_integrity.py` from the `safety_overrides` runtime stage.

Remove its override allowlist entry entirely.

Before:

```python
RuntimeStage(
    "safety_overrides",
    (
        "sync_safety.py",
        "state_integrity.py",
        "scene_state.py",
        "director_goals.py",
        "memory_curator.py",
    ),
    (
        ("sync_safety.py", (...)),
        ("state_integrity.py", ("apply_sync_snapshot",)),
    ),
)
```

After:

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
        ("sync_safety.py", (...)),
    ),
)
```

No replacement runtime stage or override entry is introduced.

## 14. state_integrity.py retirement

Delete:

`bridge/state_integrity.py`

after its Live Sync regression coverage has been migrated.

After Phase 6D, no production code should reference:

- `state_integrity.py`;
- `_ORIGINAL_APPLY_SYNC_SNAPSHOT`.

This completes the Phase 6C/6D retirement of the state-integrity compatibility layer.

## 15. Testing strategy

Implementation is strict RED -> GREEN.

### 15.1 Ordinary adapter unit tests

Create:

`tests/test_sync_integrity.py`

The tests must import `SyncSnapshotIntegrityAdapter` directly and must not import `bridge.runtime`.

Cover:

- explicit empty Persona clears `persona_id`;
- absent Persona leaves Persona untouched;
- explicit empty `world_info` clears `world_file`;
- absent `world_info` leaves world assignment untouched;
- both clears can be applied together;
- non-empty Persona/world metadata does not trigger duplicate clear logic;
- raw backend is called exactly once;
- raw backend receives original arguments;
- raw backend hash is returned unchanged;
- raw backend errors propagate;
- explicit-clear update errors propagate;
- reload occurs after successful raw import;
- memory retention uses the reloaded final session;
- card fields use the reloaded session's final character file;
- reload/card/retain failure is logged and isolated;
- refresh failure does not change the imported hash;
- module has no forbidden runtime/state/sync imports.

### 15.2 Native Sync integration tests

Extend or create focused tests around `sync_core.py` / `sync_api.py`.

Cover:

- canonical public `apply_sync_snapshot` uses the integrity adapter;
- explicit Persona clear survives a real snapshot import;
- explicit world clear survives a real snapshot import;
- absent Persona/world preserve existing assignments;
- transcript hash remains identical to raw backend semantics;
- post-import memory refresh occurs after import;
- remote-wins `phase3_sync_now` still records imported hash and direction correctly.

The existing `test_live_sync_explicitly_clears_persona_and_world_and_refreshes_memory` behavior must be migrated out of `tests/test_state_integrity.py`.

### 15.3 Runtime architecture tests

Extend `tests/test_runtime_loader.py`:

- public `apply_sync_snapshot.__code__.co_filename` resolves to `sync_core.py`;
- no runtime stage contains `state_integrity.py`;
- no runtime load report entry exists for `state_integrity.py`;
- no override allowlist references `apply_sync_snapshot`.

### 15.4 Source-boundary tests

Add assertions that:

- `bridge/state_integrity.py` no longer exists;
- `sync_integrity.py` does not import `bridge.runtime`;
- `sync_integrity.py` does not import `sync_core.py`, `sync_api.py`, or `sync_safety.py`;
- no `_ORIGINAL_APPLY_SYNC_SNAPSHOT` appears in production source;
- no new public runtime override was added.

### 15.5 Existing regression suites

Keep green:

- SyncService tests;
- Phase 3 sync tests;
- conflict/checkpoint tests;
- realtime polling/backoff tests;
- MemoryService/Hindsight tests;
- Persona tests;
- runtime-loader tests;
- composition tests;
- full repository tests.

## 16. Migration order

1. add failing ordinary adapter unit tests;
2. implement `SyncSnapshotIntegrityAdapter`;
3. add failing canonical-owner/native integration tests;
4. rename raw `sync_core.py` snapshot implementation privately;
5. compose the adapter in `sync_core.py`;
6. route the public `apply_sync_snapshot` through the adapter;
7. migrate the existing Live Sync integrity regression;
8. remove `state_integrity.py`;
9. remove its runtime-loader module entry and override allowlist;
10. run focused Sync/runtime tests;
11. run source/architecture guards;
12. run full exact-head CI;
13. open/update PR and review exact final head.

## 17. Out of scope

Phase 6D does not change:

- `sync_safety.py`;
- `initialize_database_schema` safety override;
- `phase3_sync_poll` safety override;
- sync polling fairness;
- chat job-lock coordination;
- realtime backoff;
- failure-count policy;
- conflict detection;
- checkpoint semantics;
- sync binding schema;
- SillyTavern API client behavior;
- `SyncService` interface;
- Persona storage;
- Hindsight stale-memory locking;
- recovery overrides;
- `persona_sync.py::load_personas`;
- runtime-loader retirement as a whole.

No Phase 6E or Phase 7 work is pre-authorized by this spec.

## 18. Acceptance criteria

Phase 6D is complete when all are true:

1. `SyncSnapshotIntegrityAdapter` exists in an ordinary-import module;
2. `sync_core.py` is the canonical public owner of `apply_sync_snapshot`;
3. explicit empty Persona clears remain preserved;
4. absent Persona leaves existing Persona unchanged;
5. explicit empty world-info clears remain preserved;
6. absent world-info leaves existing world assignment unchanged;
7. valid non-empty Persona/world imports remain raw-backend behavior;
8. transcript/message/variant/generation-setting behavior is unchanged;
9. raw backend errors propagate;
10. explicit-clear errors propagate;
11. post-import memory refresh remains best-effort;
12. memory refresh uses the final reloaded session;
13. raw imported hash is returned unchanged;
14. `SyncService` remains structurally unchanged;
15. `state_integrity.py` is deleted;
16. `_ORIGINAL_APPLY_SYNC_SNAPSHOT` no longer exists;
17. runtime loader contains no `state_integrity.py` module or allowlist;
18. `sync_safety.py` is unchanged;
19. no new runtime override, `_ORIGINAL_*` capture, runtime stage, service locator, or load-order dependency is introduced;
20. full repository CI passes on the exact final head.

## 19. Next phase

After Phase 6D merges, the next architectural slice is Phase 6E: extract Sync safety behavior currently implemented by `sync_safety.py`, specifically the schema-lifecycle cleanup and hardened realtime poll path, into ordinary explicit collaborators before the broader Phase 7 runtime-loader retirement.

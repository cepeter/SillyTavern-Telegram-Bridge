# Phase 6B — Persona Integrity Adapter Design

Date: 2026-09-20
Status: Proposed — awaiting written-spec review
Repository: `cepeter/SillyTavern-Telegram-Bridge`
Baseline: `314b2137b36164608c0d427583564b0fcb1660d1` (merged Phase 6A)
Master design: `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`
Predecessor: `docs/superpowers/specs/2026-09-20-phase-6a-scheduler-safety-adapter-design.md`

## 1. Purpose

Phase 6B removes the Persona portion of the late `state_integrity.py` override layer and replaces it with an ordinary, explicit Persona-store decorator.

Today `state_integrity.py` late-replaces two public Persona storage functions:

- `upsert_native_persona`
- `delete_native_persona`

and captures:

- `_ORIGINAL_UPSERT_NATIVE_PERSONA`
- `_ORIGINAL_DELETE_NATIVE_PERSONA`

That layer provides two important guarantees:

1. native Persona writes are process-serialized;
2. bridge logical Persona IDs cannot silently collide with an already-created native avatar stem.

It also currently contains `_choose_native_avatar`, which is not merely hardening: the native Persona storage implementation in `persona_sync.py` calls that helper at runtime to allocate a stable avatar filename while preserving the source avatar extension.

Phase 6B makes those responsibilities explicit and normally imported without changing Persona user-visible behavior.

The governing rule is:

> Keep the native Persona storage workflow and its safety guarantees intact, but remove its dependence on late replacement and shared-namespace execution order.

## 2. Scope

Phase 6B is intentionally Persona-only.

It will:

1. introduce an ordinary-import `IntegrityCheckedPersonaStore` collaborator;
2. move logical-ID collision checking out of `state_integrity.py`;
3. keep direct Persona upsert/delete operations serialized even when called outside `PersonaService`;
4. move native avatar allocation into the canonical native Persona storage implementation in `persona_sync.py`;
5. make `persona_sync.py` the canonical runtime owner of `upsert_native_persona` and `delete_native_persona`;
6. remove the Persona-specific `_ORIGINAL_*` captures from `state_integrity.py`;
7. remove `upsert_native_persona` and `delete_native_persona` from the `state_integrity.py` runtime override allowlist;
8. update tests so Persona integrity behavior is exercised through the explicit store adapter rather than by mutating `_ORIGINAL_*` globals.

Phase 6B will not migrate Hindsight stale-retain protection, Hindsight purge invalidation, Live Sync snapshot hardening, Sync poll safety, or the `persona_sync.py` `load_personas` native-adapter override.

## 3. Behavior that must not change

The following are binding invariants.

1. Native Persona upserts remain process-serialized.
2. Native Persona deletion remains serialized with upserts.
3. Two bridge logical IDs must not map to the same `bridge-<logical-id>.*` native avatar stem.
4. Updating an existing explicit native avatar filename remains allowed.
5. Creating a bridge Persona preserves the source native avatar file extension where valid.
6. Existing native avatar media are not rewritten merely because Persona metadata changes.
7. New Persona avatar bytes remain an exact copy of the chosen source avatar bytes.
8. Unsupported source avatar suffixes still fall back to `.png`.
9. Native Persona names and descriptions keep their current validation bounds.
10. Native catalog capacity limits remain unchanged.
11. The optimistic settings-hash check still rejects concurrent settings changes before save.
12. Native settings backup behavior, file permissions, retention, and checksum verification remain unchanged.
13. Native settings save/readback verification remains unchanged.
14. Failed new-avatar saves still remove an orphan avatar when safe under the current rules.
15. Deleting a Persona removes native metadata but preserves the avatar media file.
16. Native Persona cache invalidation remains unchanged after successful upsert/delete.
17. `PersonaService.create_and_select` still rolls back a just-created unused Persona if session selection fails.
18. `PersonaService.delete_if_unused` still rejects referenced Personas.
19. Persona create/select/update/delete workflows remain serialized across the service's multi-step use case.
20. Direct compatibility callers of `upsert_native_persona` and `delete_native_persona` also remain serialized.
21. No new public-callable runtime override, `_ORIGINAL_*` capture, runtime stage, global service locator, or shared-namespace ordering dependency is introduced.

## 4. Current architecture problem

The current Persona path is split across `persona_sync.py` and a later `state_integrity.py` replacement.

Conceptually:

```text
persona_sync.py
  |
  +-- defines upsert_native_persona
  +-- defines delete_native_persona
  +-- upsert calls _choose_native_avatar
               ^
               |
state_integrity.py executes later
  |
  +-- captures _ORIGINAL_UPSERT_NATIVE_PERSONA
  +-- captures _ORIGINAL_DELETE_NATIVE_PERSONA
  +-- defines _choose_native_avatar
  +-- replaces upsert_native_persona
  +-- replaces delete_native_persona
```

This creates two undesirable properties:

- the production owner of Persona writes cannot be understood from `persona_sync.py` alone;
- `persona_sync.py` relies on a helper that does not exist until a later runtime stage executes.

The latter is an especially strong execution-order dependency and is directly contrary to the migration target.

## 5. Considered approaches

### 5.1 Selected: explicit integrity decorator over canonical native storage

Introduce an ordinary-import module, `bridge/persona_integrity.py`, with an explicit store decorator.

Conceptual shape:

```text
PersonaService
      |
      v
IntegrityCheckedPersonaStore
      |
      v
native Persona storage in persona_sync.py
```

The native storage implementation keeps file/API backup, optimistic concurrency, avatar allocation, save, and readback verification.

The integrity decorator owns:

- serialization for direct upsert/delete calls;
- logical-ID collision protection.

This is selected because it matches the master Phase 6 target, isolates policy from storage mechanics, remains easy to fake in tests, and lets Phase 7 later replace the shared runtime without redesigning the Persona safety contract again.

### 5.2 Rejected: move all safety into PersonaService

`PersonaService` already holds a lock around multi-step create/select/update/delete use cases. However, raw compatibility functions remain callable and have existing regression coverage requiring direct upsert/delete serialization.

Moving safety only into the service would make those direct call paths weaker than today and would blur the distinction between use-case coordination and storage safety.

### 5.3 Rejected: fold checks directly into persona_sync.py public functions

This would remove the late override but would leave integrity policy fused into native storage implementation details.

It is simpler in the short term but gives Phase 7 no explicit PersonaStore boundary and does not follow the master target example of `PersonaStore -> IntegrityCheckedPersonaStore -> SillyTavernPersonaStore`.

## 6. IntegrityCheckedPersonaStore design

Add an ordinary-import type with a narrow callable interface.

Conceptually:

```python
@dataclass(frozen=True)
class IntegrityCheckedPersonaStore:
    load_personas: Callable[..., dict[str, dict[str, object]]]
    upsert_backend: Callable[..., str]
    delete_backend: Callable[..., bool]
    valid_avatar: Callable[[object], str]
    edit_lock: Callable[[], AbstractContextManager[object]]

    def upsert(
        self,
        identifier: str,
        name: str,
        description: str,
        *,
        client=None,
    ) -> str:
        ...

    def delete(
        self,
        identifier: str,
        *,
        client=None,
    ) -> bool:
        ...
```

The adapter is ordinary-importable and has no dependency on:

- `bridge.runtime`;
- `persona_sync.py`;
- Telegram/UI modules;
- shared runtime globals.

### Logical-ID collision semantics

Before a logical-ID upsert:

1. convert the identifier to text;
2. if it is already a valid native avatar filename, treat it as an explicit existing-avatar update and do not apply bridge stem collision rejection;
3. otherwise compute `bridge-<identifier>`;
4. force-refresh native Persona metadata;
5. reject if any native Persona avatar stem equals that expected stem.

The error remains:

```text
Persona ID already exists
```

This preserves the current retry/idempotency behavior in `PersonaService`, which checks for an exact matching existing Persona before deciding whether it needs to call the store.

### Serialization semantics

Both `upsert` and `delete` execute their backend call while holding the injected Persona edit lock.

This deliberately coexists with the broader `PersonaService` lock.

The two lock layers have different scopes:

- `PersonaService` serializes a complete use case such as load/check -> persist -> session update;
- `IntegrityCheckedPersonaStore` protects direct storage calls that bypass the service.

The current production lock is already used in nested service/store flows; Phase 6B preserves that locking model rather than weakening it.

## 7. Canonical native Persona storage design

`persona_sync.py` remains the owner of native SillyTavern Persona persistence.

The existing storage workflow stays intact:

```text
validate input
    |
load current settings
    |
copy settings
    |
allocate avatar
    |
apply metadata
    |
optimistic hash re-check
    |
backup
    |
ensure avatar file
    |
save settings
    |
readback verify
    |
invalidate cache
```

The public functions become stable delegates over the integrity adapter rather than being replaced later.

Recommended internal shape:

```python
def _upsert_native_persona_storage(...):
    ...

def _delete_native_persona_storage(...):
    ...

_PERSONA_STORE = _IntegrityCheckedPersonaStore(
    load_personas=load_native_personas,
    upsert_backend=_upsert_native_persona_storage,
    delete_backend=_delete_native_persona_storage,
    valid_avatar=_valid_native_avatar,
    edit_lock=lambda: PERSONA_EDIT_LOCK,
)

def upsert_native_persona(...):
    return _PERSONA_STORE.upsert(...)

def delete_native_persona(...):
    return _PERSONA_STORE.delete(...)
```

The exact private names may vary during implementation, but there must be only one public owner at runtime and no later replacement.

## 8. Native avatar allocation ownership

`_choose_native_avatar` moves from `state_integrity.py` into `persona_sync.py`.

That helper belongs to native storage because it decides the native media filename and extension used by the storage workflow.

It preserves the current algorithm:

1. reuse an explicitly mapped valid native avatar when present;
2. if exactly one existing native Persona has the requested display name, reuse its avatar;
3. otherwise take the extension from `settings["user_avatar"]` when valid;
4. fall back to `.png` for unsupported suffixes;
5. try `bridge-<persona-id><suffix>`;
6. if occupied, generate bounded deterministic SHA-256 suffix candidates;
7. fail after the existing bounded attempt count.

Moving this helper is not a behavior change; it removes a hidden dependency from the canonical native store.

## 9. PersonaService composition

`PersonaService` keeps its existing application-service interface.

Production composition continues to inject:

- `load_personas`;
- `default_persona_id`;
- canonical public `upsert_native_persona`;
- canonical public `delete_native_persona`;
- session update repository;
- reference counter;
- `PERSONA_EDIT_LOCK` for whole-use-case serialization.

No new `BridgeServices` field is required.

The service remains the owner of:

- create-and-select workflow;
- update workflow;
- selection/disable workflow;
- rollback of newly created unused Persona after failed selection;
- reference-aware deletion.

The integrity adapter remains the owner of direct-store serialization and bridge logical-ID uniqueness.

## 10. Runtime-loader changes

`state_integrity.py` remains in the `safety_overrides` stage for Phase 6C/6D work.

Its allowlist shrinks from:

```text
upsert_native_persona
delete_native_persona
retain_session_memory
purge_hindsight_session
apply_sync_snapshot
```

to:

```text
retain_session_memory
purge_hindsight_session
apply_sync_snapshot
```

After Phase 6B:

- `upsert_native_persona` resolves to `persona_sync.py`;
- `delete_native_persona` resolves to `persona_sync.py`;
- the runtime override report has no Persona function replacement from `state_integrity.py`;
- `state_integrity.py` still owns only the Memory/Live-Sync safety functions that remain in later Phase 6 slices.

Phase 6B does not remove `state_integrity.py` itself.

## 11. state_integrity.py after Phase 6B

Remove from `state_integrity.py`:

- `_ORIGINAL_UPSERT_NATIVE_PERSONA`;
- `_ORIGINAL_DELETE_NATIVE_PERSONA`;
- `_native_persona_id_is_taken`;
- `_choose_native_avatar`;
- replacement `upsert_native_persona`;
- replacement `delete_native_persona`.

Leave the Hindsight and Live Sync sections unchanged:

- `_ORIGINAL_APPLY_SYNC_SNAPSHOT`;
- `_ORIGINAL_RETAIN_SESSION_MEMORY_WORKER`;
- `_ORIGINAL_PURGE_HINDSIGHT_SESSION`;
- stale snapshot/epoch logic;
- post-retain hooks;
- sync metadata clearing and memory refresh.

This strict separation prevents Phase 6B from turning into a mixed Persona/Memory/Sync migration.

## 12. Error handling

### Logical ID collision

Raise `ValueError("Persona ID already exists")` before calling the native persistence backend.

### Native storage validation

Keep current validation and exceptions from `persona_sync.py`.

### Concurrent settings modification

Keep the optimistic hash comparison and current retry-style `ValueError`.

### Backup/save/readback failure

Preserve current backup and avatar cleanup behavior. The integrity decorator does not reinterpret backend exceptions.

### Locking failure

There is no new retry layer. Lock acquisition retains existing Python lock semantics and exceptions propagate naturally if an injected test lock fails.

## 13. Testing strategy

Use strict RED -> GREEN TDD.

### 13.1 Ordinary adapter unit tests

Add focused tests for `IntegrityCheckedPersonaStore` proving:

- two concurrent upserts cannot execute their backends simultaneously;
- upsert and delete cannot execute their backends simultaneously;
- a bridge logical ID whose expected avatar stem already exists is rejected;
- an explicit existing native avatar identifier is allowed through;
- backend return values are preserved;
- the optional client argument is passed through;
- backend exceptions propagate unchanged.

These tests should not import `bridge.runtime`.

### 13.2 Native storage integration tests

Migrate current Persona cases from `tests/test_state_integrity.py` to the explicit storage boundary.

Preserve coverage for:

- source `.webp` extension preservation;
- exact source avatar bytes;
- duplicate bridge logical ID rejection;
- unrelated native settings preservation;
- delete preserving avatar media;
- optimistic settings conflict rejection;
- readback verification;
- cache invalidation.

Tests should stop patching `_ORIGINAL_UPSERT_NATIVE_PERSONA` and `_ORIGINAL_DELETE_NATIVE_PERSONA`.

### 13.3 PersonaService regression tests

Keep existing service tests for:

- create-and-select;
- idempotent matching retry;
- duplicate logical ID behavior;
- update;
- select/disable;
- reference-aware delete;
- failed-selection cleanup;
- failed-selection adoption protection.

Add no storage-specific logic to the service tests.

### 13.4 Runtime architecture tests

Prove:

- `state_integrity.py` no longer allowlists `upsert_native_persona`;
- `state_integrity.py` no longer allowlists `delete_native_persona`;
- `rt.upsert_native_persona.__code__.co_filename` is `persona_sync.py`;
- `rt.delete_native_persona.__code__.co_filename` is `persona_sync.py`;
- no Persona override appears in the state-integrity runtime report.

### 13.5 Source-boundary guards

Add narrow guards proving:

- `state_integrity.py` contains no `_ORIGINAL_UPSERT_NATIVE_PERSONA`;
- `state_integrity.py` contains no `_ORIGINAL_DELETE_NATIVE_PERSONA`;
- `state_integrity.py` does not define `upsert_native_persona`;
- `state_integrity.py` does not define `delete_native_persona`;
- `persona_integrity.py` does not import `bridge.runtime`;
- `persona_sync.py` contains the canonical avatar-allocation helper;
- no new Persona runtime override entry is introduced elsewhere.

### 13.6 Full regression gate

The exact final branch head must pass the complete repository CI suite before the PR is marked Ready for review.

## 14. Migration order

Implementation should be split into reviewable RED -> GREEN commits.

1. Add failing ordinary-import adapter tests.
2. Implement `IntegrityCheckedPersonaStore`.
3. Add failing native-storage ownership/architecture tests.
4. Move avatar allocation into `persona_sync.py` and introduce canonical storage delegates.
5. Rewire native Persona public functions through the integrity adapter.
6. Migrate concurrency tests away from `_ORIGINAL_*` mutation.
7. Remove Persona overrides and captures from `state_integrity.py`.
8. Shrink the runtime-loader allowlist.
9. Run focused tests, source-boundary review, and full exact-head CI.
10. Open/mark the PR Ready only after exact-head verification.

No Phase 6C Memory work or Phase 6D Sync work is included.

## 15. Out of scope

Phase 6B does not:

- change Persona UI or callback behavior;
- change Persona ID syntax;
- change Persona name/description bounds;
- change native settings file format;
- change backup retention;
- delete Persona avatar media during metadata deletion;
- change SillyTavern API routes;
- remove the `persona_sync.py` `load_personas` override;
- redesign `PersonaService`;
- migrate Hindsight retention or purge safety;
- migrate Live Sync snapshot safety;
- change sync polling/backoff;
- remove `state_integrity.py`;
- remove `runtime_loader.py`;
- change test-fixture compatibility globals unrelated to Persona;
- add an ORM or new persistence layer.

## 16. Acceptance criteria

Phase 6B is complete when all of the following are true:

- `IntegrityCheckedPersonaStore` is ordinary-importable;
- native Persona persistence has one canonical public owner in `persona_sync.py`;
- native avatar allocation no longer depends on a later runtime stage;
- direct Persona upsert/delete calls remain serialized;
- logical bridge Persona ID uniqueness is preserved;
- source avatar extension preservation is preserved;
- native settings backup, optimistic concurrency, save/readback, cleanup, and cache behavior are preserved;
- `PersonaService` behavior and whole-use-case serialization remain unchanged;
- `state_integrity.py` no longer captures or replaces Persona upsert/delete functions;
- its runtime override allowlist contains only the remaining Memory/Sync functions;
- no new runtime override, `_ORIGINAL_*` chain, service locator, or runtime-order dependency is introduced;
- focused Persona adapter/storage/service/runtime tests pass;
- the complete exact-head CI suite passes.

## 17. Follow-on boundary

After Phase 6B merges, the recommended next slice is Phase 6C: Hindsight stale-memory guard extraction.

That phase should own:

- guarded queued retention;
- transcript fingerprint validation;
- purge epoch invalidation;
- Hindsight document mapping cleanup.

Live Sync `apply_sync_snapshot` and `sync_safety.py` should remain separate for a later Phase 6D design.

Phase 6B does not pre-authorize those later migrations.

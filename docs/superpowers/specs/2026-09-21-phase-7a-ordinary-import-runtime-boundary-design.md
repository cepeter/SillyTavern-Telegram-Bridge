# Phase 7A — Ordinary-Import Runtime Boundary Design

Date: 2026-09-21  
Baseline upstream `main`: `ef23d33950f5e83d74c6770867343380ff5d2dbf`  
Feature branch: `refactor/phase-7a-ordinary-import-runtime-boundary`

## Status

Approved in-chat architecture and cutover design. This document defines the Phase 7A boundary only. Implementation planning and code changes follow after explicit review of this written spec.

## Context

Phase 6H completed retirement of the recovery compatibility layer:

- `bridge/recovery.py` is deleted.
- `recovery_overrides` no longer exists.
- no public-callable override allowlist remains for recovery.

The master architecture roadmap now enters Phase 7: retire compatibility runtime loading.

The current runtime still uses `bridge/runtime.py` as a compatibility facade over `bridge/runtime_loader.py`, which executes repository source files into one shared namespace. Many legacy modules still rely on names injected by earlier files rather than ordinary Python imports.

Phase 7A is the first strangler step away from that model. It does not attempt a big-bang conversion. Instead, it establishes a repeatable ordinary-import island and proves that modules can permanently leave `DEFAULT_RUNTIME_STAGES` without behavior changes.

## Goal

Create the first permanent ordinary-import runtime island and shrink the shared `exec()` runtime.

At the end of Phase 7A:

- `performance.py` is imported normally and no longer exec-loaded.
- `native_cache.py` is imported normally and no longer exec-loaded.
- `schema.py` is imported normally and no longer exec-loaded.
- a new immutable `runtime_defaults.py` provides the retention constant currently coupled through `common.py`.
- `runtime.py` explicitly re-exports required public names from normal modules for compatibility.
- no migrated production module imports `bridge.runtime`.
- the legacy loader remains for not-yet-migrated modules only.
- import order no longer affects the migrated tranche.

## Non-goals

Phase 7A does not:

- delete `runtime_loader.py`;
- delete or fully normalize `runtime.py`;
- make `common.py` an ordinary-only module;
- migrate `database.py`;
- migrate command, callback, generation, memory, group, media, Persona, or Sync UI fragments;
- redesign `BridgeServices`;
- change database schema contents or migration versions;
- change startup cleanup behavior;
- change background executor, lock, cache, or semaphore semantics;
- change extension registry semantics;
- alter user-visible behavior;
- introduce a replacement global dependency dictionary or service locator.

## Architectural choice

Use an incremental ordinary-import island.

The runtime evolves from:

```text
runtime.py
  |
  └── runtime_loader.py
        |
        └── exec(source, shared_namespace)
              ├── common.py
              ├── performance.py
              ├── native_cache.py
              ├── schema.py
              ├── database.py
              ├── ...
              └── main.py
```

to:

```text
ordinary Python modules
├── runtime_defaults.py
├── performance.py
├── native_cache.py
├── schema.py
├── migrations.py
├── repositories.py
├── composition.py
└── service modules

runtime.py
├── explicit compatibility re-exports
└── load_runtime_namespace(...)
      ├── common.py
      ├── database.py
      ├── memory.py
      ├── ...
      └── main.py
```

The ordinary-import world grows while the exec-loaded world shrinks.

## Rejected approach: mechanical `importlib` replacement

Replacing `exec()` with `importlib` across the existing stage list does not solve the dependency problem. Many current runtime files are not independently importable because they rely on globals injected by earlier files.

A mechanical conversion would either fail broadly or recreate the shared namespace through a different mechanism.

## Rejected approach: big-bang runtime rewrite

Converting the complete runtime graph to ordinary imports in one PR would touch startup, generation, command routing, callbacks, media, Sync, Persona, and extension features simultaneously.

The regression surface and rollback cost are too high for the current reliability-sensitive architecture.

## First ordinary-import tranche

Phase 7A migrates:

- `bridge/performance.py`
- `bridge/native_cache.py`
- `bridge/schema.py`

and introduces:

- `bridge/runtime_defaults.py`

### Why `performance.py`

It already imports its own dependencies and owns no cross-module runtime state beyond its own functions.

### Why `native_cache.py`

It is already structurally self-contained and is a useful correctness case because it owns mutable module-level cache state.

Normal import ownership ensures only one cache instance exists.

### Why `schema.py`

It is close to ordinary-module form and already imports `sqlite3`, `time`, and migration primitives explicitly.

Its one problematic dependency is `PROCESSED_UPDATE_RETENTION_SECONDS`, currently imported from `common.py`.

That coupling is corrected as part of Phase 7A.

## `common.py` remains legacy-loaded

`common.py` remains in `DEFAULT_RUNTIME_STAGES` during 7A.

This is deliberate because `common.py` currently owns more than constants and helpers. It also constructs mutable process state such as:

- thread pools;
- semaphores;
- locks;
- tracked future state;
- background execution state.

Normally importing `bridge.common` while also exec-loading its source into `bridge.runtime` risks duplicate process state.

Phase 7A therefore does not solve ordinary-import dependencies by importing `common.py` wholesale from migrated modules.

## Immutable runtime defaults

Introduce:

```text
bridge/runtime_defaults.py
```

with immutable constants that need to be shared safely between the ordinary and legacy worlds.

Phase 7A moves only:

```python
PROCESSED_UPDATE_RETENTION_SECONDS = 30 * 86400
```

to this module.

Then:

```python
# common.py
from bridge.runtime_defaults import (
    PROCESSED_UPDATE_RETENTION_SECONDS,
)
```

and:

```python
# schema.py
from bridge.runtime_defaults import (
    PROCESSED_UPDATE_RETENTION_SECONDS
    as _PROCESSED_UPDATE_RETENTION_SECONDS,
)
```

`runtime_defaults.py` is never added to `DEFAULT_RUNTIME_STAGES`. It is ordinary-only from its creation.

This extraction must remain intentionally narrow. Phase 7A does not move unrelated `common.py` configuration.

## Dependency rules for migrated modules

Every migrated module must import successfully in a fresh interpreter without importing `bridge.runtime` first.

A migrated production module may depend on:

- Python standard library modules;
- ordinary bridge modules;
- explicitly injected collaborators when appropriate.

A migrated production module must not depend on:

- `bridge.runtime`;
- shared runtime globals supplied by loader order;
- a new compatibility dictionary;
- a new service locator;
- `globals().get(...)` to discover another module's public dependency.

The only exception to the last rule is independently justified module-local state preservation, not cross-module dependency discovery.

## Runtime facade semantics

`bridge/runtime.py` remains a compatibility facade during Phase 7.

For migrated modules, the facade explicitly imports public names from their canonical ordinary module.

Conceptually:

```python
from bridge.performance import (
    performance_enabled,
    perf_span,
    timed_call,
)
from bridge.native_cache import (
    cached_json,
    cached_png_metadata,
    cached_text,
)
from bridge.schema import (
    SCHEMA_MIGRATIONS,
    initialize_database_schema,
)
```

The exact exported symbol set should preserve the names currently available through `bridge.runtime` that are used by production code or tests.

After those explicit imports exist, `performance.py`, `native_cache.py`, and `schema.py` must be removed from `DEFAULT_RUNTIME_STAGES` so the loader cannot create duplicate function objects or mutable state.

## Duplicate-state prohibition

A migrated module must have one Python module instance as its canonical state owner.

This is especially important for `native_cache.py`, which owns:

- `_NATIVE_CACHE_LOCK`
- `_NATIVE_CACHE`
- `_TEXT_CACHE`

The following state is prohibited:

```text
bridge.native_cache module state
        +
exec(native_cache.py, bridge.runtime namespace)
```

because it would create two independent cache objects and locks.

Removing the migrated file from the runtime stage is therefore a correctness requirement, not cosmetic cleanup.

The same principle applies to future migrated modules containing locks, registries, clients, counters, workers, or caches.

## Runtime-loader boundary

Before Phase 7A, the core stage begins conceptually:

```text
common.py
performance.py
native_cache.py
cards.py
schema.py
database.py
...
```

After Phase 7A it begins conceptually:

```text
common.py
cards.py
database.py
...
```

The existing order of all remaining legacy modules stays unchanged.

Phase 7A must not reorder unrelated runtime files.

## Cutover sequence

Implementation follows four TDD slices.

### Slice A — import independence

Add fresh-process tests for:

```text
import bridge.performance
import bridge.native_cache
import bridge.schema
import bridge.runtime_defaults
```

No test may import `bridge.runtime` first.

Introduce `runtime_defaults.py`, move the one retention constant source of truth there, and update `common.py` and `schema.py` to import it.

Expected result:

- all four modules import independently;
- no duplicate `common.py` process state is created to satisfy `schema.py`.

### Slice B — remove exec ownership

Remove:

- `performance.py`
- `native_cache.py`
- `schema.py`

from the core `RuntimeStage`.

Do not add `runtime_defaults.py` to any runtime stage.

Add source/runtime tests that assert the three migrated files are absent from every `DEFAULT_RUNTIME_STAGES` module list.

### Slice C — explicit compatibility facade

Update `runtime.py` to import and expose the required public names from the ordinary modules.

Tests must verify object identity, for example:

```python
import bridge.performance as performance
import bridge.runtime as rt

assert rt.perf_span is performance.perf_span
```

Equivalent identity checks should cover representative public names from `native_cache.py` and `schema.py`.

Name existence alone is insufficient; object identity proves the runtime facade is not exposing an exec-created duplicate.

### Slice D — import-order independence

Use subprocess tests for both orders:

```python
import bridge.schema
import bridge.runtime
```

and:

```python
import bridge.runtime
import bridge.schema
```

The facade must expose the same normal-module function objects in either order.

Equivalent checks should cover `performance.py` and `native_cache.py`.

For `native_cache.py`, the test must prove `bridge.runtime` sees the same function/module state rather than a duplicate exec-owned cache implementation.

## Import-island tests

Phase 7A adds permanent guards for the migrated tranche.

Required categories:

### Fresh-process import tests

Each target ordinary module imports successfully in a clean interpreter without `bridge.runtime`.

### Runtime-stage absence tests

Assert:

```text
performance.py  not in DEFAULT_RUNTIME_STAGES
native_cache.py not in DEFAULT_RUNTIME_STAGES
schema.py       not in DEFAULT_RUNTIME_STAGES
runtime_defaults.py not in DEFAULT_RUNTIME_STAGES
```

### Facade identity tests

Verify representative names are object-identical between canonical modules and `bridge.runtime`.

At minimum:

- `rt.perf_span is bridge.performance.perf_span`
- `rt.cached_json is bridge.native_cache.cached_json`
- `rt.initialize_database_schema is bridge.schema.initialize_database_schema`

Additional public names should be covered when needed for existing runtime consumers.

### Import-order tests

Verify canonical object identity when:

1. ordinary module imports before runtime;
2. runtime imports before ordinary module.

### Production dependency guard

Migrated production files must not import:

```text
bridge.runtime
```

### Permanent stage-retirement guard

Once a file leaves `DEFAULT_RUNTIME_STAGES`, tests must prevent it from being reintroduced.

For Phase 7A the guard may name the three migrated files explicitly. Later Phase 7 work may generalize this invariant.

## Schema behavior preservation

Phase 7A does not change schema semantics.

The following remain unchanged:

- migration ledger behavior;
- `SCHEMA_MIGRATIONS` contents;
- migration version numbers;
- all DDL;
- startup cleanup queries;
- cleanup timing;
- retention period;
- transaction/commit behavior of `initialize_database_schema`.

Tests must verify both:

```text
empty database -> current expected schema
existing/initialized database -> no new migration version or behavior change
```

No new database migration is introduced by Phase 7A.

## Performance behavior preservation

`performance.py` behavior remains byte/semantically equivalent:

- `SILLYTAVERN_PERF_LOG` interpretation is unchanged;
- disabled spans remain low-overhead;
- timing/log field formatting is unchanged;
- `timed_call` behavior is unchanged.

Phase 7A changes ownership/loading only.

## Native-cache behavior preservation

`native_cache.py` behavior remains unchanged:

- cache keys remain based on resolved path, mtime, and size;
- returned JSON/PNG metadata remains deep-copied;
- text caching behavior is unchanged;
- cache locks remain module-local;
- no cache reset or eviction redesign is introduced.

The significant architecture change is that only the canonical `bridge.native_cache` module owns this state.

## Error handling

Import migration must not add silent fallback behavior.

If a migrated module is missing a required dependency:

- fail through normal Python import/name resolution during development/tests;
- add the legitimate explicit dependency;
- do not fall back to `bridge.runtime`;
- do not look up a dependency dynamically from a shared namespace.

If a target module proves to require broad unresolved shared state, leave it in the legacy stage rather than weakening the ordinary-import boundary.

## Production scope boundary

Production changes in Phase 7A should be limited to:

- new `bridge/runtime_defaults.py`;
- `bridge/common.py` retention constant import;
- `bridge/schema.py` retention constant import;
- `bridge/runtime.py` explicit compatibility imports;
- `bridge/runtime_loader.py` removal of the three migrated files from stages.

`performance.py` and `native_cache.py` should not need behavioral edits unless tests uncover a genuine standalone-import defect.

No other production module should require architectural modification for Phase 7A.

## Regression boundary

Phase 7A must preserve:

- database schema and migrations;
- DB transaction semantics;
- startup cleanup;
- background executor lifecycle;
- native cache semantics;
- performance logging semantics;
- service composition;
- extension registry behavior;
- command routing;
- callback routing;
- generation;
- Sync behavior;
- Persona behavior;
- memory behavior;
- scheduler/job durability.

The PR contains no intended user-visible behavior change.

## Verification requirements

Before Phase 7A is ready for review:

- fresh-process ordinary-import tests pass;
- runtime-stage absence guards pass;
- facade object-identity tests pass;
- import-order tests pass;
- schema migration/regression tests pass;
- native-cache tests pass;
- performance tests pass;
- full `unittest` suite passes;
- full `pytest` suite passes;
- Python compilation passes;
- `pip check` passes;
- dependency audit passes;
- exact branch-head GitHub Actions succeeds;
- branch is not behind current upstream `main`, or any drift is explicitly reviewed;
- whole-branch review finds no Critical or Important issue.

## Relationship to later Phase 7 work

Phase 7A establishes the permanent migration rule:

```text
once a file leaves DEFAULT_RUNTIME_STAGES,
it becomes a normal Python module permanently.
```

After Phase 7A, the architecture has two explicit worlds.

### Normal-import world

- `runtime_defaults.py`
- `performance.py`
- `native_cache.py`
- `schema.py`
- `migrations.py`
- `repositories.py`
- `composition.py`
- already extracted service/adaptor modules

### Legacy shared-namespace world

- `common.py`
- `database.py`
- `memory.py`
- `generation.py`
- commands and routing
- remaining feature fragments
- `main.py`

Likely later sequencing, subject to fresh dependency inventory after 7A:

1. database/import-safe persistence foundation;
2. low-level Telegram/config/card helpers;
3. memory/group/Sync feature modules;
4. generation and command/UI routing;
5. final `main.py` and runtime-facade cutover;
6. delete `runtime_loader.py`.

Phase 7A does not precommit the exact boundaries of those later subphases.

## End state

```text
bridge/runtime_defaults.py
└── ordinary-only immutable defaults

bridge/performance.py
└── ordinary-only

bridge/native_cache.py
└── ordinary-only, sole cache-state owner

bridge/schema.py
└── ordinary-only

bridge/runtime.py
├── explicit re-exports from ordinary modules
└── temporary loader for remaining legacy files

bridge/runtime_loader.py
└── still present, but no longer loads:
    ├── performance.py
    ├── native_cache.py
    └── schema.py
```

## Acceptance criteria

Phase 7A is complete when all of the following are true:

1. `performance.py`, `native_cache.py`, and `schema.py` import independently of `bridge.runtime`.
2. `runtime_defaults.py` is ordinary-only and contains the shared retention constant.
3. `common.py` remains legacy-loaded and does not need to be imported normally to satisfy `schema.py`.
4. the three migrated files are absent from all runtime stages.
5. `runtime_defaults.py` is absent from all runtime stages.
6. `bridge.runtime` re-exports representative public names from canonical ordinary modules.
7. facade names are object-identical to canonical module functions.
8. both import orders expose the same canonical function identities.
9. no migrated production module imports `bridge.runtime`.
10. native cache state has a single owner.
11. schema contents, migration versions, cleanup behavior, and retention values are unchanged.
12. no user-visible behavior changes.
13. complete repository CI passes.

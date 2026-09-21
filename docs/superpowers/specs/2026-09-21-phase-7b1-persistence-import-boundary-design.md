# Phase 7B1 — Persistence Foundation Ordinary-Import Boundary Design

Date: 2026-09-21  
Baseline upstream `main`: `8adc4e47524602016b9e884162ef12850b999b28`  
Feature branch: `refactor/phase-7b1-persistence-import-boundary`

## Status

Approved in-chat architecture and cutover design. This document defines Phase 7B1 only. Implementation planning and code changes follow after explicit review of this written spec.

## Context

Phase 7A established the first permanent ordinary-import runtime island:

- `performance.py` is ordinary-only;
- `native_cache.py` is ordinary-only;
- `schema.py` is ordinary-only;
- `runtime_defaults.py` is ordinary-only;
- `bridge.runtime` explicitly re-exports canonical objects from those modules;
- the migrated files no longer appear in `DEFAULT_RUNTIME_STAGES`.

The remaining core runtime stage begins:

```text
common.py
cards.py
database.py
memory.py
rag.py
groups.py
telegram.py
...
main.py
```

`database.py` is the next high-leverage boundary. It is consumed across persistence, JobService durability, Sync, model settings, operation recovery, and startup composition, but it still depends on names injected by the shared exec namespace.

## Goal

Make `bridge.database` a permanent ordinary Python module and the sole owner of database process state while preserving the existing public persistence API through `bridge.runtime`.

At the end of Phase 7B1:

- `bridge.config` is an ordinary-only canonical owner for the narrow startup/default configuration required by persistence;
- `bridge.database` imports independently of `bridge.runtime` and `bridge.common`;
- all database stdlib and bridge dependencies are explicit;
- database lock/gate state belongs only to `bridge.database`;
- obsolete exec-era schema-ready fixture state is removed;
- `bridge.runtime` explicitly re-exports the public `database.py` API from the canonical ordinary module;
- `database.py` is removed from `DEFAULT_RUNTIME_STAGES`;
- remaining legacy modules may explicitly import `bridge.database` when they need a database-owned collaborator;
- import order no longer affects the persistence tranche;
- persistence, JobService, Sync, operation durability, and schema behavior remain unchanged.

## Non-goals

Phase 7B1 does not:

- split `database.py` into repositories or submodules;
- redesign persistence APIs;
- redesign `JobService`;
- redesign `BridgeConfig`;
- migrate `cards.py`, `memory.py`, `rag.py`, `groups.py`, or `main.py` wholesale;
- change SQLite transaction semantics;
- change schema contents or migration versions;
- change job state transitions;
- change Sync binding or transcript-hash formats;
- change generation-setting or preset semantics;
- move all configuration out of `common.py`;
- introduce runtime configuration reload;
- delete `common.py`;
- delete `runtime_loader.py`;
- introduce a new service locator, global dependency dictionary, or runtime namespace replacement.

## Architectural choice

Use the same incremental strangler pattern proven in Phase 7A.

The persistence dependency graph changes from:

```text
runtime.py
  |
  └── exec common.py
        ├── stdlib names
        ├── DB_FILE
        ├── DEFAULT_MODEL
        ├── GENERATION_DEFAULTS
        ├── REASONING_LEVELS
        └── PENDING_SETTINGS_TTL_SECONDS
              |
              v
        exec database.py
              ├── DB locks
              ├── connection gate
              ├── schema initialization
              └── public persistence functions
```

to:

```text
bridge.config
  ├── common.py compatibility re-exports
  └── database.py
        ├── explicit stdlib imports
        ├── bridge.schema
        └── bridge.scheduler_safety

bridge.database
  ├── sole DB lock owner
  ├── sole connection-gate owner
  └── canonical public persistence API
        |
        v
bridge.runtime explicit compatibility re-exports
```

The remaining legacy runtime continues operating around this ordinary-import persistence island.

## Rejected approach: import `bridge.common` from `database.py`

Normally importing `bridge.common` while its source is still exec-loaded into `bridge.runtime` would create duplicate process state.

`common.py` owns background executors, locks, semaphores, local context state, and other mutable runtime infrastructure. Importing it merely to obtain database configuration would reproduce the duplicate-state problem Phase 7A deliberately avoided.

## Rejected approach: inject a new database-config argument throughout the public API

`database.py` currently exposes roughly forty public helper functions. Adding a configuration argument or injected configuration object to that surface would cause broad call-site churn without improving the Phase 7 boundary.

Phase 7B1 instead introduces one narrow canonical config module and keeps the persistence call signatures unchanged.

## Rejected approach: migrate all of `common.py` with `database.py`

`common.py` owns unrelated application concerns including logging setup, background executors, Telegram/runtime settings, character/world paths, provider configuration, RAG constants, and process lifecycle state.

Migrating that full module in Phase 7B1 would mix persistence migration with several other architectural concerns and substantially increase regression risk.

## Canonical configuration owner

Create:

```text
bridge/config.py
```

as the ordinary-only canonical owner for exactly the configuration required to make `database.py` independent:

```text
BRIDGE_HOME
DB_FILE
DEFAULT_MODEL
DEFAULT_MAX_TOKENS
PENDING_SETTINGS_TTL_SECONDS
REASONING_LEVELS
GENERATION_DEFAULTS
```

### Values and semantics

The values remain identical to current `common.py` behavior:

```python
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

No unrelated `common.py` configuration moves in this phase.

## Relationship to `composition.BridgeConfig`

`bridge.config` and `composition.BridgeConfig` serve different purposes.

`bridge.config` is the canonical module-level source for stable startup/default values still needed by ordinary modules and the compatibility runtime.

`composition.BridgeConfig` remains the validated immutable startup snapshot passed through explicit service composition.

Phase 7B1 does not merge or redesign those concepts.

## `common.py` compatibility behavior

`common.py` imports and re-exports the configuration names now owned by `bridge.config`.

For mutable dictionaries, identity is preserved:

```python
common.GENERATION_DEFAULTS is config.GENERATION_DEFAULTS
common.REASONING_LEVELS is config.REASONING_LEVELS
```

This keeps the remaining legacy runtime on the same startup/default objects.

Rebinding compatibility names in `bridge.runtime` is not an architectural configuration mechanism.

For example:

```python
rt.DB_FILE = some_path
```

is not required to affect canonical `bridge.database` behavior after Phase 7B1.

Tests and new production code must target the canonical owner instead.

## Dynamic config access from `database.py`

`database.py` imports the module:

```python
from bridge import config as _config
```

rather than binding configuration values by value.

Persistence code uses:

```text
_config.DB_FILE
_config.DEFAULT_MODEL
_config.GENERATION_DEFAULTS
_config.REASONING_LEVELS
_config.PENDING_SETTINGS_TTL_SECONDS
```

where those values are needed.

This preserves a canonical patch point for tests while removing shared-runtime mutation as dependency injection.

## Explicit `database.py` imports

`database.py` becomes a normal module with explicit imports for all dependencies.

Required stdlib imports include:

```text
hashlib
json
logging
re
sqlite3
threading
time
pathlib.Path
contextlib.contextmanager
```

Required bridge dependencies include:

```text
bridge.config
bridge.schema.initialize_database_schema
bridge.scheduler_safety.DatabaseConnectionGate
```

No dependency may arrive implicitly from `common.py` or `bridge.runtime`.

## Database process-state ownership

Phase 7B1 makes `bridge.database` the only owner of database process state.

### Write lock

Replace the exec-preservation idiom:

```python
_DB_WRITE_LOCK = globals().get("_DB_WRITE_LOCK") or threading.RLock()
```

with ordinary module ownership:

```python
_DB_WRITE_LOCK = threading.RLock()
```

The lock is created once when `bridge.database` is imported.

### Connection gate

`_DB_CONNECTION_GATE` remains the canonical initialization/readiness gate and exists only in `bridge.database`.

Runtime loading must not instantiate a second gate.

### Cache/mmap constants

Database connection tuning constants remain private implementation details of `bridge.database`:

```text
_DB_PRIMARY_CACHE_KIB
_DB_WORKER_CACHE_KIB
_DB_PRIMARY_MMAP_BYTES
_DB_WORKER_MMAP_BYTES
```

They are not part of the runtime compatibility contract.

## Delete obsolete schema-ready fixture compatibility state

The following names are currently documented as deprecated test-fixture compatibility and are ignored by production `db_connect()`:

```text
_DB_SCHEMA_LOCK
_DB_SCHEMA_READY
_DB_SCHEMA_READY_PATHS
```

Phase 7B1 deletes them.

Tests must stop mutating them.

Database isolation should instead use:

- explicit temporary database paths;
- canonical `bridge.config.DB_FILE` patching when testing default-path behavior;
- the real `bridge.database._DB_CONNECTION_GATE` only when a test genuinely needs to inspect or reset canonical connection readiness.

The migration must not recreate dead state in `bridge.runtime`.

## Database-path semantics

The existing explicit path argument remains authoritative:

```python
database.db_connect(database_path)
```

When no path is provided, `database.py` resolves the default through:

```python
_config.DB_FILE
```

using the canonical path helper.

`run_database_maintenance()` must also resolve its target through the canonical database-path mechanism rather than directly consuming an injected global `DB_FILE`.

The same default-path source applies where relevant to initialized and lightweight connection creation.

## Runtime facade semantics

`bridge.runtime` remains a compatibility facade during Phase 7.

After `bridge.database` is independently importable, `runtime.py` explicitly imports and re-exports the current public persistence functions from the canonical ordinary module.

The current public function surface includes:

```text
run_write_txn
write_transaction
optimize_database
run_database_maintenance
db_connect
get_meta
set_meta
record_failed_turn
latest_failed_turn
clear_failed_turn
committed_assistant_for_message
bind_panel_session
panel_session_for_message
panel_owner_for_message
operation_phase
set_operation_phase
begin_operation
operation_was_applied
record_operation
enqueue_job
job_actor_id
mark_job_scheduled
mark_job_running
finish_job
recover_jobs
task_model_key
task_model_for_session
set_task_model
model_target_selection_key
set_model_target_selection
get_model_target_selection
clear_model_target_selection
get_generation_settings
update_generation_settings
preset_names
save_generation_preset
load_generation_preset
delete_generation_preset
format_generation_settings
parse_generation_setting
sync_transcript_hash
ensure_sync_binding
```

The exact final export list should match the public function definitions present at implementation time.

Exports must be explicit.

Do not use:

- wildcard imports;
- reflection;
- `globals().update(...)`;
- a generated compatibility dictionary.

## Private compatibility boundary

Phase 7B1 does not preserve private database implementation names through `bridge.runtime`.

Examples include:

```text
_DB_WRITE_LOCK
_DB_CONNECTION_GATE
_DB_PRIMARY_CACHE_KIB
_DB_WORKER_CACHE_KIB
_DB_PRIMARY_MMAP_BYTES
_DB_WORKER_MMAP_BYTES
_apply_connection_pragmas
_load_optional_vector_extension
_database_path
_open_initialized_database
_lightweight_db_connect
```

Tests that legitimately inspect those details must import:

```python
import bridge.database as database
```

This is an intentional cleanup of exec-era test coupling.

## Remaining private production dependency

`main.py` currently constructs `DurableWorkerGuard` from the private name `_lightweight_db_connect` received through loader order.

Phase 7B1 does not add that private helper to the public runtime facade.

Instead, `main.py` explicitly imports canonical database ownership, conceptually:

```python
from bridge import database as _database
```

and uses:

```python
_DURABLE_WORKER_GUARD = _DurableWorkerGuard(
    _database._lightweight_db_connect
)
```

This is the intended migration pattern for remaining legacy files: they may explicitly import a module that has left the runtime loader instead of receiving its names from exec order.

No other `main.py` architecture is migrated in Phase 7B1.

## Duplicate-state prohibition

Once `database.py` becomes ordinary-only, its source must never also be executed into `bridge.runtime`.

The following state is prohibited:

```text
bridge.database._DB_WRITE_LOCK
bridge.database._DB_CONNECTION_GATE
        +
exec(database.py, bridge.runtime namespace)
```

That would create multiple lock/gate instances and could break transaction serialization, connection initialization, or durable-worker behavior.

Removing `database.py` from `DEFAULT_RUNTIME_STAGES` is therefore a correctness requirement.

## Runtime-loader boundary

Before Phase 7B1 the core stage begins:

```text
common.py
cards.py
database.py
memory.py
rag.py
...
```

After Phase 7B1 it begins:

```text
common.py
cards.py
memory.py
rag.py
...
```

`config.py` is ordinary-only from inception and is never added to any runtime stage.

The relative order of all remaining runtime files stays unchanged.

## Cutover sequence

Implementation follows four TDD slices.

### Slice A — canonical config extraction

Add tests proving:

```text
import bridge.config
```

does not import:

```text
bridge.runtime
bridge.common
bridge.database
```

Verify the extracted values match current behavior.

Move exactly the approved configuration values from `common.py` to `config.py`.

Make `common.py` import and re-export them.

Verify mutable-object identity for `GENERATION_DEFAULTS` and `REASONING_LEVELS`.

Do not modify runtime-loader ownership in this slice.

### Slice B — independent database module

Add fresh-process tests proving:

```text
import bridge.database
```

works without importing:

```text
bridge.runtime
bridge.common
```

Add all explicit stdlib/bridge imports.

Use `bridge.config` dynamically.

Import `initialize_database_schema` explicitly from `bridge.schema`.

Replace exec-preserved write-lock ownership with module-local ownership.

Delete the obsolete `_DB_SCHEMA_*` compatibility state.

Retarget test fixtures to canonical configuration/path/gate ownership.

At the end of this slice, `database.py` may still remain in the loader temporarily, but it must already be independently importable.

### Slice C — facade cutover and loader retirement

Update `runtime.py` to explicitly re-export the public database API.

Update `main.py` so the durable-worker guard explicitly references canonical `bridge.database._lightweight_db_connect`.

Remove `database.py` from `DEFAULT_RUNTIME_STAGES`.

Do not add `config.py` to any stage.

Do not reorder any remaining legacy file.

### Slice D — order/state independence proof

Add permanent tests for:

- facade object identity;
- both import orders;
- one write-lock owner;
- one connection-gate owner;
- config default-path behavior;
- loader-stage retirement;
- absence of obsolete `_DB_SCHEMA_*` compatibility state;
- absence of `bridge.runtime` or `bridge.common` imports from canonical config/database modules.

## Architecture-specific test requirements

### Standalone config import

A clean subprocess must verify:

```python
import bridge.config
```

without loading `bridge.runtime`, `bridge.common`, or `bridge.database`.

### Standalone database import

A clean subprocess must verify:

```python
import bridge.database
```

without loading `bridge.runtime` or `bridge.common`.

### Runtime-stage retirement

Tests must permanently assert that neither:

```text
config.py
database.py
```

appears in any `DEFAULT_RUNTIME_STAGES`.

### Facade identity

Every database public function exposed through `bridge.runtime` must be the exact canonical object from `bridge.database`.

At minimum representative checks include:

```python
assert rt.db_connect is database.db_connect
assert rt.run_write_txn is database.run_write_txn
assert rt.enqueue_job is database.enqueue_job
assert rt.ensure_sync_binding is database.ensure_sync_binding
```

The implementation plan should favor complete public-function coverage rather than a small sample where practical.

### Import-order independence

Both:

```python
import bridge.database
import bridge.runtime
```

and:

```python
import bridge.runtime
import bridge.database
```

must expose the same canonical persistence function objects.

### Single write-lock ownership

Tests must prove that calls through runtime facade functions resolve `_DB_WRITE_LOCK` from `bridge.database`, not from a duplicate runtime namespace.

### Single connection-gate ownership

No second `DatabaseConnectionGate` instance may be created through runtime loading.

Canonical state lives at:

```text
bridge.database._DB_CONNECTION_GATE
```

### Private compatibility retirement

Tests must no longer rely on private DB state through `bridge.runtime`.

In particular, remove or retarget use of:

```text
rt._DB_SCHEMA_LOCK
rt._DB_SCHEMA_READY
rt._DB_CONNECTION_GATE
rt._DB_PRIMARY_CACHE_KIB
rt._DB_WORKER_CACHE_KIB
```

### Config-path behavior

Tests must prove:

```python
database.db_connect()
database.run_database_maintenance()
```

honor canonical `bridge.config.DB_FILE` when the default path is used.

Explicit:

```python
database.db_connect(path)
```

must continue to override the default path.

## Error handling

Phase 7B1 must not introduce fallback dependency discovery.

If `database.py` lacks a dependency, add the legitimate explicit import.

Do not add:

```python
globals().get(...)
getattr(runtime, ...)
try:
    import bridge.common
```

to preserve shared-namespace behavior.

No second write lock or connection gate may be created as an error fallback.

Existing operational handling remains unchanged, including:

- SQLite busy/locked handling;
- rollback semantics;
- optional `sqlite-vec` loading;
- maintenance timeout behavior;
- WAL negotiation and connection pragmas;
- durable job transition error behavior.

## Behavior preservation

Phase 7B1 must preserve:

- database initialization;
- schema initialization;
- schema migration versions;
- SQLite WAL behavior;
- connection pragmas;
- write serialization;
- connection readiness;
- explicit-path connection behavior;
- repository transactions;
- operation durability;
- JobService enqueue/recovery/transitions;
- failed-turn persistence;
- panel-session persistence;
- task-model selection;
- model-target selection;
- generation settings;
- generation presets;
- Sync binding identity/state;
- transcript hashing;
- database maintenance behavior.

No user-visible Telegram or SillyTavern behavior is intended to change.

## Regression suites

Existing suites remain authoritative, especially:

```text
tests/test_database_optimization.py
tests/test_sqlite_contention.py
tests/test_repository_transactions.py
tests/test_job_service.py
tests/test_job_service_workers.py
tests/test_migrations.py
tests/test_sync_audit.py
tests/test_task_model_routing.py
tests/test_operation_recovery.py
```

Additional import-island tests should extend the permanent Phase 7 architecture guards rather than replace these behavioral suites.

## Production scope boundary

Expected production changes are limited to:

- new `bridge/config.py`;
- `bridge/common.py` narrow config imports/re-exports;
- `bridge/database.py` explicit imports, canonical config access, state ownership, and obsolete fixture-state removal;
- `bridge/runtime.py` explicit public database re-exports;
- `bridge/runtime_loader.py` removal of `database.py`;
- `bridge/main.py` explicit canonical dependency for `_lightweight_db_connect`.

Production changes outside those files require explicit justification against the approved Phase 7B1 boundary.

Test changes may retarget exec-era private state references to canonical owners.

## Verification requirements

Before Phase 7B1 is ready for review:

- standalone `bridge.config` import tests pass;
- standalone `bridge.database` import tests pass;
- config/database loader-retirement guards pass;
- complete database facade identity tests pass;
- import-order tests pass;
- single-lock and single-gate ownership tests pass;
- obsolete `_DB_SCHEMA_*` state is absent;
- config-path/default-path tests pass;
- persistence/reliability regression suites pass;
- full `unittest` suite passes;
- full `pytest` suite passes;
- Python compilation passes;
- `pip check` passes;
- dependency audit passes;
- exact branch-head GitHub Actions succeeds;
- branch is not behind current upstream `main`, or drift is explicitly reviewed;
- whole-branch review finds no Critical or Important issue.

## Relationship to later Phase 7 work

Phase 7B1 establishes a normal persistence foundation for later runtime migration.

Expected architecture after Phase 7B1:

```text
NORMAL IMPORT WORLD
├── config.py
├── runtime_defaults.py
├── performance.py
├── native_cache.py
├── schema.py
├── database.py
├── migrations.py
├── repositories.py
├── composition.py
└── service modules

LEGACY EXEC WORLD
├── common.py
├── cards.py
├── memory.py
├── rag.py
├── groups.py
├── telegram.py
├── ...
└── main.py
      └── explicitly depends on bridge.database
```

Likely later sequencing, subject to fresh dependency inventory after 7B1:

1. cards and low-level config/helper boundary;
2. memory/group/Sync feature modules;
3. generation plus command/callback/UI routing;
4. final main/runtime-facade cutover;
5. delete `runtime_loader.py`.

Phase 7B1 does not precommit exact boundaries for those later phases.

## End state

```text
bridge/config.py
└── ordinary-only canonical startup/default values

bridge/database.py
├── ordinary-only
├── sole _DB_WRITE_LOCK owner
├── sole _DB_CONNECTION_GATE owner
├── explicit schema/config/scheduler dependencies
└── canonical public persistence API

bridge/runtime.py
├── explicit config compatibility names as needed by legacy runtime
├── explicit performance/cache/schema facade
├── explicit database public facade
└── temporary exec loader for remaining legacy files

bridge/runtime_loader.py
└── no config.py
    no performance.py
    no native_cache.py
    no schema.py
    no database.py
```

## Acceptance criteria

Phase 7B1 is complete when all of the following are true:

1. `bridge.config` imports independently of `bridge.runtime`, `bridge.common`, and `bridge.database`.
2. `bridge.database` imports independently of `bridge.runtime` and `bridge.common`.
3. `common.py` re-exports the approved config values from `bridge.config`.
4. mutable config dictionaries preserve canonical object identity between config and common compatibility usage.
5. all database stdlib/bridge dependencies are explicit.
6. `_DB_WRITE_LOCK` is ordinary module-owned and no `globals().get(...)` preservation remains.
7. `_DB_CONNECTION_GATE` exists only as canonical `bridge.database` state.
8. obsolete `_DB_SCHEMA_LOCK`, `_DB_SCHEMA_READY`, and `_DB_SCHEMA_READY_PATHS` compatibility state is deleted.
9. default database paths resolve through canonical `bridge.config.DB_FILE`.
10. explicit `db_connect(path)` behavior is unchanged.
11. all public database functions required through `bridge.runtime` are exact canonical object re-exports.
12. private database implementation names are not preserved as runtime facade debt.
13. `main.py` explicitly obtains `_lightweight_db_connect` from canonical database ownership.
14. `database.py` is absent from all runtime stages.
15. `config.py` is absent from all runtime stages.
16. remaining runtime module order is unchanged.
17. both database/runtime import orders resolve to the same persistence function objects.
18. database lock/gate state has one owner.
19. schema, transactions, jobs, Sync persistence, generation settings, operation durability, and maintenance behavior remain unchanged.
20. no migrated production module imports `bridge.runtime` or `bridge.common` to recover hidden dependencies.
21. complete repository CI passes.

## Permanent migration invariant

Once `database.py` leaves `DEFAULT_RUNTIME_STAGES`, persistence state belongs only to `bridge.database` and the file must never return to shared-namespace execution.

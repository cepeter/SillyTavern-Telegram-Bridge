# Composition Root and Runtime Context — Design

Date: 2026-09-19  
Status: written-spec review pending  
Parent architecture: `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`  
Migration phase: Phase 4  
Repository: `cepeter/SillyTavern-Telegram-Bridge`  
Baseline while writing: upstream `main` at `d636b0671dd0deed2cf1054d66be3a7e42777052`

## 1. Purpose

Phase 4 introduces one explicit startup composition boundary and one small runtime context for the bridge's top-level process and worker dependencies.

Phases 1–3 established deterministic extension policy, versioned schema ownership, SQL-only repositories, and explicit transaction ownership. The remaining startup/runtime debt is global discovery: `bridge.runtime` still executes staged source files into one shared namespace, while `main.py` and its worker entry points obtain configuration, database creation, background execution, Telegram operations, and lifecycle behavior through globals and long positional argument bundles.

Phase 4 does **not** remove the compatibility runtime. Instead, it creates the dependency shape that later phases can migrate toward incrementally.

The measurable Phase 4 result is:

> Startup constructs configuration and root infrastructure dependencies once. Top-level workers receive that context explicitly rather than rediscovering those dependencies from module globals or carrying repeated token/API/model argument bundles.

## 2. External Auditor Context

A user-supplied external audit dated 2026-09-19 reports:

- 0 SAST issues after false-positive classification
- 0 detected secrets
- 0 infrastructure issues
- 0 vulnerable dependencies across 43 scanned packages
- 74 antipattern findings
- 57 complex-function findings
- 288 missing-docstring findings
- 32 duplicate-code groups
- 39 dead-code findings

The architecture-relevant findings include:

- repeated complex-function signals for `bridge/main.py`
- complex-function signals for `groups.py`, `message_commands.py`, and `runtime_loader.py`
- excessive-parameter findings in command-routing functions
- many "unused parameter" findings in compatibility/routing functions

The report's maintainability artifact paths repeatedly reference commit `0dc7ae4f4a69dd28a4588e45b2154463f2f26b30`, which predates the current merged Phase 3 baseline. Therefore this report is treated as directional evidence rather than an exact current-tree defect inventory.

Phase 4 responds only to findings aligned with its architectural purpose:

- reduce startup/worker argument sprawl
- reduce dependency discovery in `main.py`
- make modified startup/worker functions smaller and easier to test
- avoid deleting compatibility parameters merely because a static analyzer labels them unused

Phase 4 does **not** become a general static-analysis cleanup project.

## 3. Goals

Phase 4 must:

- introduce immutable explicit startup configuration
- introduce a small explicit runtime/service context
- construct that context once at startup
- migrate `main()` and `run_check()` to use the explicit configuration
- migrate top-level background worker entry points to receive the context
- migrate durable recovered-job dispatch to propagate the same context
- migrate backlog dispatcher construction to close over the same context
- make database creation injectable at the worker boundary
- make root Telegram request/text operations injectable at the migrated boundary
- make background job submission injectable at the migrated boundary
- eliminate repeated `token, api_key, model` bundles from migrated worker signatures
- eliminate repeated `os.environ` rediscovery from migrated startup/worker code after configuration construction
- preserve all current Telegram/job/recovery behavior
- preserve Phase 3 explicit transaction behavior
- preserve current runtime-loader ordering and override behavior
- create a dependency shape that Phase 5 can later replace with application services
- keep new composition types ordinary-importable and independently testable

## 4. Non-goals

Phase 4 does not:

- remove `runtime_loader.py`
- remove `exec()` from the compatibility runtime
- change `DEFAULT_RUNTIME_STAGES`
- migrate every runtime function to ordinary imports
- add `GroupDirectorService`
- add `MemoryService`
- add `PersonaService`
- add `SyncService`
- add `JobService`
- introduce a generic IoC/DI framework
- thread the runtime context through every feature function
- convert repositories into classes
- move model-generation orchestration into the composition root
- redesign provider resolution
- redesign durable-job storage
- redesign scheduler semantics
- redesign Telegram transport
- remove all global constants from `common.py`
- remove all environment access across the repository
- mass-delete auditor "unused parameter" findings
- perform broad docstring cleanup
- deduplicate UI strings
- refactor unrelated complex functions
- perform general dead-code cleanup
- change schema migrations

Those concerns remain Phase 5+, compatibility-retirement work, or separate bounded cleanup tasks.

## 5. Current Problem

### 5.1 Startup configuration is rediscovered

`main()` currently:

1. loads the environment
2. refreshes sync configuration
3. enforces runtime permissions
4. reads Telegram token from `os.environ`
5. reads generic LLM API key from `os.environ`
6. reads the selected model from `os.environ`
7. validates runtime configuration
8. loads the default character card
9. later calls `allowed_users()`

`run_check()` repeats part of the same discovery path.

This makes startup behavior harder to test without mutating process-global environment state.

### 5.2 Worker signatures carry startup arguments repeatedly

Top-level workers currently include signatures such as:

```python
process_message_job(
    token,
    api_key,
    model,
    fields,
    chat_id,
    text,
    message_id,
    queued_session_id=None,
    job_id=None,
)
```

Recovered-job dispatch and backlog dispatchers repeatedly pass these values through multiple layers.

The parameters represent two different categories mixed together:

- startup/runtime dependencies
- per-job input

Phase 4 separates them.

### 5.3 Database construction is globally discovered

Worker entry points call `db_connect()` directly.

Tests that need a different database currently rely on compatibility-runtime globals such as `DB_FILE`.

Phase 4 gives workers an injected database factory.

### 5.4 Runtime compatibility obscures dependency boundaries

The compatibility loader intentionally makes all staged names available in one namespace.

This was useful for incremental migration, but it makes code such as `main.py` appear to have no dependencies even though it relies on many.

Phase 4 makes only the startup/worker dependency boundary explicit. Feature-level global discovery remains temporarily until Phase 5.

## 6. Chosen Approach

Use an **incremental composition root** with three ordinary-importable data types:

- `BridgeConfig`
- `TelegramRuntime`
- `BackgroundRuntime`
- aggregated by `BridgeServices`

These types live in a new ordinary-import module:

`bridge/composition.py`

The module is not a runtime-loader stage.

The composition root is still `main.py` during Phase 4 because `main.py` is the one place where the compatibility runtime can safely supply transitional implementations.

The flow becomes:

```text
load .env / refresh legacy config
        |
        v
load_bridge_config(...)
        |
        v
build_bridge_services(...)
        |
        v
validate startup
        |
        +--> run_check(services)
        |
        v
run_bridge(services, fields)
        |
        +--> workers(services, job data)
        |
        +--> recovery(services, job data)
```

This creates one explicit dependency handoff without pretending Phase 5 services already exist.

## 7. Why Not a Generic Service Locator

`BridgeServices` must not become a replacement global namespace.

It may contain only root-level infrastructure dependencies used directly by migrated startup/worker code.

It must not accumulate arbitrary feature functions such as:

- `process_message`
- `group_director_plan`
- `curate_memory_now`
- `refresh_scene_state_now`
- command handlers
- panel renderers
- repository functions

Those remain compatibility-runtime workflow functions until Phase 5 extracts focused application services.

A field may be added to `BridgeServices` only when:

1. the migrated startup/worker layer directly depends on it, and
2. it represents configuration or infrastructure/lifecycle behavior rather than domain orchestration.

## 8. BridgeConfig

### 8.1 Shape

Conceptually:

```python
@dataclass(frozen=True)
class BridgeConfig:
    bot_token: str = field(repr=False)
    api_key: str = field(repr=False)
    default_model: str
    default_character_file: str
    card_file: Path
    db_file: Path
    allowed_users: frozenset[str]
```

Fields containing credentials use `repr=False`.

The exact field list may include another startup value only when `main.py` or a migrated worker directly needs it.

### 8.2 Construction

Provide a pure function:

```python
def load_bridge_config(
    environ: Mapping[str, str],
    *,
    character_dir: Path,
    db_file: Path,
) -> BridgeConfig:
    ...
```

It reads from the supplied mapping, not directly from `os.environ`.

Required environment keys/inputs include current semantics for:

- `SILLYTAVERN_TELEGRAM_BOT_TOKEN`
- `LLM_API_KEY`
- `SILLYTAVERN_MODEL`
- `SILLYTAVERN_DEFAULT_CHARACTER`
- `SILLYTAVERN_TELEGRAM_ALLOWED_USERS`

`card_file` is derived from `character_dir / default_character_file`.

`allowed_users` is parsed once into an immutable `frozenset[str]`.

### 8.3 Validation

Configuration validation is explicit and deterministic.

`validate_bridge_config(config)` must reject:

- missing default character name
- missing model
- missing Telegram token
- configured default card path that does not exist

Provider-specific credential validation remains in the current provider logic because Phase 4 does not redesign provider configuration.

Therefore startup performs:

```text
load_bridge_config
validate_bridge_config
validate_startup_credential(config.default_model)
```

The generic `api_key` remains available for current compatibility workflows, even though provider-specific credentials may be resolved elsewhere.

### 8.4 Environment mutation

`load_bridge_config()` must never mutate its supplied mapping or `os.environ`.

The existing `load_env_file()` remains responsible for loading `.env` before config construction.

## 9. TelegramRuntime

Use a small frozen dataclass rather than a broad object hierarchy:

```python
@dataclass(frozen=True)
class TelegramRuntime:
    request: Callable[..., object]
    send_text: Callable[..., object]
```

Only operations directly needed by the migrated startup/worker layer belong here.

Feature-specific Telegram functions such as panel rendering, callback routing, TTS, media download, and response rendering do not move into this object during Phase 4.

This object allows startup polling and worker failure delivery to be tested without monkeypatching the shared runtime namespace.

## 10. BackgroundRuntime

Use:

```python
@dataclass(frozen=True)
class BackgroundRuntime:
    submit_chat: Callable[..., bool]
    register_backlog_dispatcher: Callable[[Callable[[], None]], None]
    begin_shutdown: Callable[[], None]
```

`begin_shutdown` is part of `BackgroundRuntime`. Signal-handler installation captures that callable explicitly; Phase 4 must not fall back to discovering it through a global services registry.

Do not include domain/job-storage functions such as `enqueue_job`, `finish_job`, or `recover_jobs`; those are application persistence behavior and remain Phase 5 candidates.

## 11. BridgeServices

Conceptually:

```python
@dataclass(frozen=True)
class BridgeServices:
    config: BridgeConfig
    db_factory: Callable[[], sqlite3.Connection]
    telegram: TelegramRuntime
    background: BackgroundRuntime
```

This context is immutable.

Workers receive the same `BridgeServices` instance created at startup.

No setter or global registry for the active services object is introduced.

There is no:

```python
CURRENT_SERVICES = ...
get_services()
set_services()
```

Such a registry would simply replace one form of global discovery with another.

## 12. Composition Construction

`main.py` remains the transitional composition root.

After current environment/bootstrap actions:

```python
config = load_bridge_config(
    os.environ,
    character_dir=CHARACTER_DIR,
    db_file=DB_FILE,
)

services = BridgeServices(
    config=config,
    db_factory=make_database_factory(config.db_file),
    telegram=TelegramRuntime(
        request=telegram_request,
        send_text=send_text,
    ),
    background=BackgroundRuntime(
        submit_chat=submit_chat_background,
        register_backlog_dispatcher=register_durable_backlog_dispatcher,
        begin_shutdown=begin_background_shutdown,
    ),
)
```

Provide an explicit builder:

```python
def build_bridge_services(
    config: BridgeConfig,
    *,
    db_factory: Callable[[], sqlite3.Connection],
    telegram: TelegramRuntime,
    background: BackgroundRuntime,
) -> BridgeServices:
    ...
```

The builder must receive transitional compatibility implementations explicitly. `bridge/composition.py` must not import or inspect the shared runtime namespace to discover them.

## 13. Database Factory

### 13.1 Required behavior

Workers must open databases via:

```python
db = services.db_factory()
```

rather than direct `db_connect()`.

### 13.2 Explicit database path

To support a real factory without mutating `DB_FILE`, Phase 4 **will** extend:

```python
db_connect()
```

to:

```python
db_connect(database_path: Path | None = None)
```

with:

- `None` preserving existing `DB_FILE` behavior
- an explicit path using that path for the connection
- identical serialized connection factory
- identical pragmas
- identical migration initialization

Then:

```python
def make_database_factory(path: Path):
    return lambda: db_connect(path)
```

Before the factory is used, schema-initialization state must be verified to be safe across multiple database paths. Any readiness/cache state currently shared across paths must be keyed by resolved database path or removed. An injected test database must never reuse initialization state from another database.

### 13.3 Compatibility

Existing unmigrated code may continue calling `db_connect()` with no argument.

Phase 4 does not require migration of every database-open call.

## 14. Worker Signature Migration

### 14.1 Message worker

Move from:

```python
process_message_job(token, api_key, model, fields, chat_id, text, ...)
```

to:

```python
process_message_job(
    services,
    fields,
    chat_id,
    text,
    message_id,
    queued_session_id=None,
    job_id=None,
)
```

Inside:

- DB comes from `services.db_factory()`
- token comes from `services.config.bot_token`
- API key comes from `services.config.api_key`
- model comes from `services.config.default_model`
- worker-level failure text uses `services.telegram.send_text`

The downstream `process_message(...)` call remains a compatibility-runtime function in Phase 4.

### 14.2 Image worker

Move startup values to `services`.

Per-job values remain explicit:

- chat ID
- file ID
- caption
- file size
- message ID
- queued session ID
- job ID

### 14.3 Callback worker

Receive `services` plus callback/job data.

Use:

- injected DB factory
- configured bot token
- injected worker-level `send_text` for failure reporting

`process_callback()` remains a compatibility workflow.

### 14.4 Edit worker

Receive `services` plus edit/job data.

Use configured token/API key/default model from `services.config`.

### 14.5 Voice/document workers

Any top-level worker whose only startup arguments are repeated token/API/model values follows the same rule.

Phase 4 should not force context injection into lower-level feature functions merely because a worker calls them.

## 15. Durable Job Recovery

Change conceptually from:

```python
dispatch_recovered_jobs(db, token, api_key, default_model, fields)
```

to:

```python
dispatch_recovered_jobs(
    db,
    services,
    fields,
    *,
    recover_running=True,
)
```

Recovered payloads may still override the model when that behavior already exists.

The key invariant is that recovered workers receive the same startup `BridgeServices` instance as newly queued workers.

No recovered job may reconstruct config by reading `os.environ`.

## 16. Backlog Dispatcher

Change:

```python
make_durable_backlog_dispatcher(token, api_key, default_model, fields)
```

to:

```python
make_durable_backlog_dispatcher(services, fields)
```

The returned closure uses:

```python
db = services.db_factory()
dispatch_recovered_jobs(db, services, fields, recover_running=False)
```

This makes restart/recovery execution use the same dependency graph as live execution.

## 17. Main Loop

### 17.1 Decompose startup construction

`main()` should no longer own every construction detail inline.

Preferred shape:

```python
def main() -> int:
    args = parse_args()
    load_env_file()
    refresh_phase3_config()
    enforce_runtime_permissions()

    config = load_bridge_config(...)
    validate_bridge_config(config)
    validate_startup_credential(config.default_model)
    services = build_bridge_services(...)

    if args.check:
        return run_check(services)

    fields = load_default_character_fields(config)
    return run_bridge(services, fields)
```

Phase 4 must extract at least these focused helpers from `main()`: configuration construction/validation and service construction. The poll/update loop may remain in `main.py`, but `main()` itself must not retain inline dependency construction.

### 17.2 Poll loop

The poll loop uses:

```python
services.telegram.request(
    services.config.bot_token,
    "getUpdates",
    ...
)
```

The durable-job wrapper becomes explicit about background submission:

```python
submit_durable_chat_job(
    db,
    services.background,
    label,
    chat_id,
    worker,
    *args,
)
```

and calls `services.background.submit_chat` internally. It must no longer discover `submit_chat_background` globally.

### 17.3 Allowed users

The poll loop uses:

```python
services.config.allowed_users
```

rather than calling `allowed_users()` again.

The existing compatibility helper may remain for unmigrated callers.

## 18. run_check()

Change to:

```python
run_check(services: BridgeServices) -> int
```

It must not reload `.env` or reconstruct runtime configuration.

`main()` owns bootstrap once, then passes the constructed services object.

`run_check()` may still use current sync-authentication compatibility functions during Phase 4.

Telegram `getMe` uses `services.telegram.request`.

Printed output continues to expose the configured model, card name, Telegram username, and database path as today, but must never print token/API-key values.

## 19. Shutdown

Signal handlers currently call global `begin_background_shutdown()`.

Preferred Phase 4 shape:

- signal request sets the existing shutdown event
- shutdown infrastructure is provided from the composed background runtime

Because Python signal handlers are process-global callbacks, one narrow compatibility bridge is acceptable if required:

```python
def install_bridge_signal_handlers(on_shutdown: Callable[[], None]) -> None:
    ...
```

The handler closure captures `services.background.begin_shutdown`.

Do not introduce a global `CURRENT_SERVICES` merely to make signal handling convenient.

## 20. Runtime Loader Compatibility

Phase 4 must coexist with the compatibility loader.

Rules:

- `bridge/composition.py` is an ordinary import
- it is not added to `DEFAULT_RUNTIME_STAGES`
- it must not use `exec()`
- it must not access runtime globals dynamically
- runtime-loader override expectations remain unchanged unless a migrated function signature requires a narrowly documented test update
- no new public-callable runtime override is introduced
- no new `_ORIGINAL_*` capture chain is introduced
- production startup still enters through `bridge.runtime` during Phase 4

The auditor's `runtime_loader.py` `exec()` security finding was classified as a false positive because the sources are trusted/static. Phase 4 therefore does not treat removal of `exec()` as a security hotfix. Its retirement remains the planned later compatibility phase.

## 21. Compatibility Signature Rule

The auditor reports many "unused parameter" findings.

Phase 4 must not remove a parameter solely because a static analyzer marks it unused.

A parameter may be removed only if all of the following are true:

1. all direct call sites are identified
2. runtime-loader/public-callable compatibility is checked
3. extension registry call shape is checked
4. recovery/safety wrappers are checked
5. tests prove the parameter is not part of an externally relied-upon call contract

This rule is especially important for command routes and extension callbacks where uniform signatures may be intentional.

Phase 4 may remove startup-only parameters from the worker functions being explicitly migrated because those values are replaced by `BridgeServices` and all call sites are part of the migration scope.

## 22. Complexity Containment Rule

The external audit flags substantial maintainability complexity.

Phase 4 does not need to reduce the global count of 57 complex functions because that report predates current main and covers unrelated domains.

However:

- a function modified for composition must not gain avoidable branch depth
- `main()` should delegate configuration/construction to focused helpers
- worker signatures should shrink
- recovered dispatch should use data/context separation
- new functions should have one clear ownership purpose

The goal is local complexity reduction in the touched startup/worker slice, not metric gaming.

No code should be split solely to satisfy a static analyzer if the resulting boundary is less coherent.

## 23. Error Handling

### Configuration errors

Invalid startup configuration fails before:

- background worker registration
- recovered job dispatch
- Telegram polling
- database job processing

Errors remain concise and operator-facing.

Credentials must not be included in error messages.

### Worker errors

Existing worker behavior remains:

- errors are logged with detailed server-side context
- durable jobs transition to failed/done as today
- Telegram receives existing generic failure messages

Only the transport for root-level failure delivery changes to injected `services.telegram.send_text`.

### Factory errors

If `services.db_factory()` fails:

- the worker fails through the existing worker exception path
- no partially constructed global runtime context is retained

## 24. Data Flow

### Startup

```text
process environment
     |
load_env_file
     |
refresh legacy compatibility config
     |
load_bridge_config(os.environ, paths)
     |
validate BridgeConfig
     |
build BridgeServices
     |
load default character fields
     |
register recovery/backlog
     |
poll Telegram
```

### Live message job

```text
poll update
   |
enqueue durable job
   |
background.submit_chat(
    process_message_job,
    services,
    per-job data
)
   |
worker -> services.db_factory()
   |
existing compatibility process_message(...)
   |
close DB
```

### Recovered job

```text
recover_jobs(db)
   |
dispatch_recovered_jobs(db, services, fields)
   |
background.submit_chat(
    worker,
    services,
    recovered job data
)
```

The same services object flows through both paths.

## 25. Testing Strategy

### 25.1 Config construction

Test `load_bridge_config()` with a plain dict.

Assert:

- all expected fields are parsed
- allowed users become an immutable set
- card path is derived correctly
- credentials are not exposed in repr
- input mapping is unchanged

No test should need to patch `os.environ` for the pure config constructor.

### 25.2 Config validation

Test independently:

- missing bot token
- missing model
- missing default character
- nonexistent card file
- valid config

Provider-specific key behavior remains tested through existing provider/startup tests.

### 25.3 Database factory

Build services with a fake/temp DB factory.

Assert worker entry points call that factory and do not call the shared global `db_connect`.

If `db_connect(path)` is introduced, test two independent file paths to ensure schema-init/connection state does not leak between paths.

### 25.4 Worker config injection

For message/image/edit/voice/document workers:

- construct `BridgeServices` with sentinel token/API/model values
- patch only downstream compatibility workflow functions as needed
- assert the worker passes values from `services.config`
- assert it does not rediscover those values from environment

### 25.5 Telegram injection

Use fake:

```python
TelegramRuntime(
    request=fake_request,
    send_text=fake_send_text,
)
```

Assert:

- `run_check()` uses fake request
- main polling boundary uses fake request in focused tests
- worker error paths use fake send_text

Do not require migration of every feature-specific Telegram call.

### 25.6 Background injection

Use fake background runtime.

Assert:

- live jobs are submitted through injected submitter
- recovered jobs use the same submitter
- backlog dispatcher registration receives a closure bound to the same services instance

### 25.7 Recovery identity

Pass one `BridgeServices` instance.

Capture worker arguments from recovered dispatch.

Assert the exact same object identity is supplied to each submitted worker.

This prevents accidental reconstruction of per-job contexts.

### 25.8 No environment rediscovery

Source-level or trace-based test for the migrated functions:

- `run_check`
- worker entry points
- recovered dispatch
- backlog dispatcher

Assert they do not call `os.environ.get` for token/API/model/allowed users.

`main()` may access `os.environ` exactly once by passing the mapping to `load_bridge_config`.

### 25.9 Runtime compatibility

Run the existing runtime-loader audit.

Assert:

- stage ordering is unchanged
- `composition.py` is not a stage
- no new public override appears
- existing extension registry behavior remains green

### 25.10 Durable jobs and recovery

Run existing:

- background lifecycle tests
- recovery tests
- session command routing
- SQLite contention tests
- state integrity
- sync audit
- generation continuation
- Group transaction/recovery regressions

Phase 3 transaction behavior must remain green.

### 25.11 Startup/check behavior

Cover:

- `--check`
- missing required config
- valid card loading
- Telegram `getMe` check
- startup credential validation
- permitted user parsing
- backlog registration before/after expected lifecycle points

## 26. Expected File Responsibilities

### New: `bridge/composition.py`

Owns:

- `BridgeConfig`
- `TelegramRuntime`
- `BackgroundRuntime`
- `BridgeServices`
- pure `load_bridge_config(...)`
- pure/small builders that do not discover runtime globals

It must not own domain workflows.

### Modify: `bridge/main.py`

Owns:

- transitional composition root
- environment/bootstrap sequence
- construction of `BridgeServices` using existing compatibility implementations
- startup validation
- worker entry points receiving `BridgeServices`
- durable dispatch propagation
- poll loop using explicit config/root infrastructure

It should become simpler, not become a larger service container.

### Modify: `bridge/database.py`

Only if needed to support explicit path-aware DB factories:

- optional explicit DB path for `db_connect`
- path-safe initialization behavior

Do not otherwise expand Phase 4 database scope.

### Tests

Add a focused composition/runtime-context test module, likely:

`tests/test_composition.py`

Update existing startup/background/recovery tests only where signatures change.

## 27. Acceptance Criteria

Phase 4 is complete when all of the following are true:

1. `bridge/composition.py` exists as an ordinary importable module.
2. `BridgeConfig` is frozen/immutable.
3. Credential fields are excluded from dataclass repr.
4. Config can be built from an explicit mapping without reading or mutating `os.environ`.
5. Config contains Telegram token, generic API key, default model, default card identity/path, DB path, and allowed users required by startup.
6. Required startup configuration is validated once before polling/workers.
7. `BridgeServices` is frozen/immutable.
8. `BridgeServices` contains only root-level config/infrastructure dependencies.
9. No global current-services registry is introduced.
10. Startup constructs one services instance.
11. `run_check()` receives the constructed services object and does not reload config.
12. Migrated worker entry points receive `BridgeServices`.
13. Repeated token/API/model positional bundles are removed from migrated worker signatures.
14. Workers open DB connections through `services.db_factory`.
15. Message worker uses config values from services.
16. Image worker obtains its bot token and default model from services rather than positional startup arguments.
17. Callback worker uses root Telegram failure delivery from services.
18. Edit worker uses config values from services.
19. Voice/document workers follow the same rule if they currently carry repeated startup values.
20. Recovered-job dispatch receives and propagates the same services object.
21. Backlog dispatcher closes over the same services object.
22. Live and recovered job submission use the injected background submitter at the migrated boundary.
23. Main/check root Telegram requests use injected Telegram runtime.
24. Allowed users are parsed once into config and used by the poll loop.
25. Migrated worker/check functions do not rediscover token/API/model values from `os.environ`.
26. No `GroupDirectorService`, `MemoryService`, `PersonaService`, `SyncService`, or `JobService` is introduced.
27. No generic DI framework/service locator is introduced.
28. `composition.py` is not added to runtime stages.
29. Runtime-loader stage order remains unchanged.
30. No new public-callable runtime override is introduced.
31. No new `_ORIGINAL_*` capture chain is introduced.
32. Existing Phase 3 repository/transaction tests remain green.
33. Existing durable-job/recovery behavior remains green.
34. Existing Telegram command behavior remains green.
35. Full repository CI and dependency audit pass on the exact final head.

## 28. Rejected Approaches

### 28.1 Global runtime-context registry

Rejected:

```python
CURRENT_CONTEXT = ...
def get_runtime_context(): ...
```

This would replace one global-discovery mechanism with another and make tests/order coupling persist.

### 28.2 Full dependency injection through every feature

Rejected because it collapses Phase 4 and Phase 5 into one large rewrite.

Phase 4 injects only the startup/worker boundary.

### 28.3 Full application-service extraction now

Rejected because the parent roadmap reserves focused business workflow extraction for Phase 5.

Creating placeholder service classes with no coherent workflow ownership would increase indirection without reducing coupling.

### 28.4 Generic DI/IoC framework

Rejected as unnecessary.

The repository needs explicit Python dataclasses/callables, not a container framework.

### 28.5 Put every compatibility callable into BridgeServices

Rejected because that would create a service-locator-shaped mirror of the existing runtime namespace.

Only root infrastructure belongs in the Phase 4 context.

### 28.6 Mass-remove auditor unused parameters

Rejected because compatibility route/hook signatures may intentionally be uniform.

Only parameters inside the explicitly migrated worker boundary are changed after auditing all call sites.

### 28.7 Remove runtime-loader exec() in Phase 4

Rejected because the audit classified the current trusted-source `exec()` finding as a false positive, and the roadmap already assigns compatibility-runtime retirement to a later phase.

## 29. Relationship to the Auditor Report

Phase 4 uses the auditor report as a maintainability signal, not a checklist.

Addressed directly:

- startup/worker parameter sprawl
- `main.py` complexity pressure
- hidden dependency discovery
- testability of root infrastructure

Deferred:

- broad complex-function count
- missing docstrings
- duplicate strings/code
- generic dead-code findings
- unrelated route complexity
- runtime-loader retirement

After Phase 4 and Phase 5, rerunning the auditor on the then-current commit should provide a more meaningful before/after architecture comparison.

## 30. Relationship to Phase 5

Phase 4 establishes:

```text
main.py composition root
        |
        v
BridgeServices
        |
        +-- config
        +-- database factory
        +-- Telegram root adapter
        +-- background runtime
        |
        v
compatibility workflows
```

Phase 5 then replaces compatibility workflows with focused services:

```text
BridgeServices
        |
        +-- GroupDirectorService
        +-- MemoryService
        +-- PersonaService
        +-- SyncService
        +-- JobService
```

Phase 4 must not pre-create those classes.

The service context should be designed so Phase 5 can add focused service fields while removing transitional compatibility calls, not so that Phase 5 must replace the context itself.

## 31. Relationship to Compatibility Runtime Retirement

Phase 4 is intentionally transitional.

It makes dependencies explicit at the process boundary while production still enters through `bridge.runtime`.

Later phases will:

1. extract cohesive application services
2. replace safety/recovery override behavior with explicit adapters/decorators
3. remove public override ownership from runtime stages
4. migrate production imports to ordinary Python modules
5. remove the compatibility `exec()` loader from production

Phase 4 is successful if it reduces the surface that still depends on global discovery without destabilizing existing behavior.

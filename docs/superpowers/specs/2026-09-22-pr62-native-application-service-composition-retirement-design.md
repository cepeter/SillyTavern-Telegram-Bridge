# PR #62 Native Application Service Composition Retirement — Design

Date: 2026-09-22  
Status: Written-spec review pending  
Repository: `cepeter/SillyTavern-Telegram-Bridge`  
Baseline: merged `main` at `772e29d65f8fd5c3116c4f7d5514f82f6e0865ef`  
Predecessor: PR #61 — architecture documentation refresh  
Branch: `refactor/pr62-native-service-composition`

## 1. Purpose

PR #62 completes the application-service composition migration started in Phase 5 and made practical by the Phase 7 compatibility retirement.

The repository already has canonical ordinary-import application services:

- `MemoryService`
- `PersonaService`
- `SyncService`
- `GroupDirectorService`
- `JobService`

Production startup already constructs all five services. PR #60 made `JobService` mandatory and removed its compatibility fallback. The other four services still retain temporary direct-call compatibility adapters introduced while the old runtime architecture existed.

PR #62 removes those remaining application-service compatibility adapters and establishes one invariant:

> Production application workflows receive their application services explicitly from the startup composition graph. They never reconstruct a missing service and never silently fall back to a compatibility service.

This repository is preproduction. Backward compatibility with the retired compatibility-call style is not a requirement.

## 2. Current state

### 2.1 Root composition is already explicit

`bridge.main._build_startup_services()` constructs:

- `GroupDirectorService`
- `MemoryService`
- `PersonaService`
- `SyncService`
- `BackgroundRuntime`
- `JobService`

and passes them to `build_bridge_services(...)`.

`JobService` is already required by both `BridgeServices` and `build_bridge_services()`.

The remaining four application services are still typed as optional:

```python
@dataclass(frozen=True)
class BridgeServices:
    ...
    jobs: JobService
    group_director: GroupDirectorService | None = None
    memory: MemoryService | None = None
    persona: PersonaService | None = None
    sync: SyncService | None = None
```

That optionality no longer represents production reality.

### 2.2 Memory still has a compatibility constructor

`bridge.memory` still exposes a short-lived compatibility service:

```python
compatibility_memory_service()
resolve_memory_service(memory_service=None)
```

Callers such as reset, generation, recovery, and session deletion accept `memory_service=None` and reconstruct the service when it is absent.

### 2.3 Persona still has a compatibility constructor

`bridge.persona_sync` still exposes:

```python
compatibility_persona_service()
resolve_persona_service(persona_service=None)
```

Persona UI/input helpers often accept `persona_service=None` and resolve a fallback at call time.

### 2.4 Sync still has a compatibility constructor

`bridge.sync_api` still exposes:

```python
compatibility_sync_service()
resolve_sync_service(sync_service=None)
```

The realtime worker and callback path may reconstruct a Sync service when one is not provided.

### 2.5 Group Director still has compatibility wrappers

`bridge.groups` still constructs a service through:

```python
_compat_group_director_service()
```

and exposes compatibility delegation through functions such as:

- `parse_group_director_decision()`
- `group_director_plan()`
- `group_prompt_context()`

Meanwhile `process_message()` already prefers `services.group_director` but falls back to those wrappers when `services` or `group_director` is absent.

### 2.6 Application routing still accepts an absent service graph

Examples include:

```python
process_message(..., *, services=None)
process_callback(..., *, services=None)
```

and patterns such as:

```python
getattr(services, "memory", None)
getattr(services, "persona", None)
getattr(services, "sync", None)
getattr(services, "group_director", None)
```

Those patterns preserve an architecture that production no longer needs.

## 3. Goals

PR #62 must:

1. make `MemoryService`, `PersonaService`, `SyncService`, and `GroupDirectorService` required members of `BridgeServices`;
2. make their corresponding `build_bridge_services()` arguments required;
3. remove all application-service compatibility constructors and resolver helpers;
4. remove application workflow fallbacks that reconstruct missing services;
5. require the service graph at application routing boundaries that depend on it;
6. pass narrow required services explicitly to lower-level helpers rather than making lower-level modules discover dependencies;
7. preserve all existing business behavior, persistence semantics, recovery semantics, startup behavior, and Telegram behavior;
8. preserve service implementations and service public use-case semantics unless a signature tightening is necessary to remove optionality;
9. add permanent regression guards preventing compatibility service reconstruction from returning;
10. leave later runtime-context and composition-root decomposition work for separate PRs.

## 4. Non-goals

PR #62 does not:

- remove or redesign `bridge.runtime_context`;
- replace DB/panel/actor thread-local context;
- split `bridge.main`;
- move `_build_startup_services()` out of `bridge.main`;
- redesign `MemoryService`, `PersonaService`, `SyncService`, `GroupDirectorService`, or `JobService`;
- add a generic dependency-injection framework;
- introduce a global service registry;
- introduce `CURRENT_SERVICES`, `get_services()`, or equivalent service-locator state;
- convert every function to accept `BridgeServices`;
- change database schema or migrations;
- change Hindsight behavior;
- change Persona storage or integrity behavior;
- change Live Sync conflict/safety behavior;
- change Group Director policy or model behavior;
- change durable-job state semantics;
- change Telegram command/callback behavior;
- perform broad module-size refactoring;
- remove `runtime_context.py`;
- clean unrelated historical tests or documentation.

Those items belong to later phases, especially the proposed explicit request/job context and `bridge.main` decomposition work.

## 5. Architectural approaches considered

### 5.1 Selected: required root graph plus narrow explicit leaf dependencies

Make the full production service graph mandatory at the composition boundary.

High-level routing functions receive `BridgeServices` explicitly. Lower-level functions receive only the specific service they use.

Example:

```text
main worker
    |
    v
process_message(..., services=BridgeServices)
    |
    +--> services.memory ------> generation/reset/recovery helper
    |
    +--> services.persona -----> Persona input/panel helper
    |
    +--> services.sync --------> Sync callback/worker helper
    |
    +--> services.group_director -> group planning/context
```

Advantages:

- explicit dependency ownership;
- no service reconstruction;
- leaf modules remain narrow;
- no global service locator;
- production and tests use the same dependency shape;
- compatible with later request-context work.

This is the selected approach.

### 5.2 Rejected: pass `BridgeServices` through every function

This would remove fallback discovery but would make many leaf helpers depend on the entire application service graph when they only need one service.

That would turn `BridgeServices` into a broad service-locator object and increase coupling.

### 5.3 Rejected: install a process-global current-service registry

A global registry would make signatures smaller but would recreate the hidden dependency-discovery problem that Phase 7 removed.

It would also complicate tests, worker isolation, and later request-context migration.

## 6. Target composition contract

### 6.1 BridgeServices

The target contract is conceptually:

```python
@dataclass(frozen=True)
class BridgeServices:
    config: BridgeConfig
    db_factory: Callable[[], sqlite3.Connection]
    telegram: TelegramRuntime
    background: BackgroundRuntime
    jobs: JobService
    group_director: GroupDirectorService
    memory: MemoryService
    persona: PersonaService
    sync: SyncService
```

There are no optional application services.

### 6.2 build_bridge_services

The corresponding builder requires every application service:

```python
def build_bridge_services(
    config: BridgeConfig,
    *,
    db_factory,
    telegram: TelegramRuntime,
    background: BackgroundRuntime,
    jobs: JobService,
    group_director: GroupDirectorService,
    memory: MemoryService,
    persona: PersonaService,
    sync: SyncService,
) -> BridgeServices:
    ...
```

A test or alternate entry point that wants a lightweight service graph must inject an explicit fake/stub service. It must not omit the dependency.

### 6.3 Startup ownership

`bridge.main._build_startup_services()` remains the production composition root for this PR.

It continues to construct all application services from canonical collaborators and passes them to `build_bridge_services()`.

No new production constructor module is required in PR #62.

## 7. Routing boundary changes

### 7.1 Message routing

`process_message(..., services=...)` becomes an explicit required-service boundary.

The exact Python parameter placement may remain keyword-only, but absence is no longer supported.

The function uses:

- `services.memory`
- `services.persona`
- `services.group_director`

directly. Persona display/name lookup in this application path also uses
`services.persona` rather than bypassing the service through a direct
compatibility-era Persona helper.

The following fallback logic is removed:

```python
getattr(services, "memory", None)
getattr(services, "persona", None)
getattr(services, "group_director", None)
```

and the Group Director compatibility branch is removed.

### 7.2 Callback routing

`process_callback(..., services=...)` becomes a required-service boundary.

It obtains `services.persona` and `services.sync` directly.

Callback helpers that need one service receive that concrete service explicitly.

### 7.3 Command routing

Where `handle_command_route()` depends on service-backed flows, its `services` input becomes required rather than an optional compatibility hook.

Command helpers should not reconstruct application services.

### 7.4 Worker paths

Existing worker entry points already receive `BridgeServices`.

They continue passing the exact service instance into their downstream workflows.

No worker may construct a replacement application service because an injected field is absent.

## 8. Memory compatibility retirement

Delete from `bridge.memory`:

- `compatibility_memory_service()`
- `resolve_memory_service()`

All application functions that currently accept `memory_service=None` and then resolve a fallback must instead require a `MemoryService` when the workflow needs memory.

Examples include the affected reset, generation, edit/recovery, image, and session-deletion paths.

The migration rule is:

> Required memory behavior receives the already-composed `MemoryService`; a helper that genuinely does not need memory should not receive one.

The existing backend functions remain canonical collaborators for constructing the production service in `bridge.main`. Their algorithms are not moved or rewritten by this PR.

## 9. Persona compatibility retirement

Delete from `bridge.persona_sync`:

- `compatibility_persona_service()`
- `resolve_persona_service()`

Persona-related helpers in `input_flows.py`, callback routing, and related panels receive an explicit `PersonaService`.

Existing Persona native-storage functions remain backend collaborators used during production service construction.

The Persona service continues to own:

- create/select;
- update;
- disable;
- delete-if-unused;
- list/get/name/default resolution.

No UI helper may silently instantiate a replacement Persona service.

## 10. Sync compatibility retirement

Delete from `bridge.sync_api`:

- `compatibility_sync_service()`
- `resolve_sync_service()`

The realtime worker lifecycle must receive the composed `SyncService` explicitly.

Conceptually:

```python
start_phase3_sync_worker(sync_service: SyncService)
_phase3_worker_loop(sync_service: SyncService)
```

Callback routing passes `services.sync` explicitly.

Raw Sync backend functions remain infrastructure used to construct the service. PR #62 does not change transport or poll-safety behavior.

## 11. Group Director compatibility retirement

Delete `_compat_group_director_service()` from `bridge.groups`.

Application routing uses the composed `services.group_director` directly for:

- planning;
- prompt context.

Delete the compatibility delegation wrappers after production and tests are migrated:

- `group_director_plan()`;
- `group_prompt_context()`;
- `parse_group_director_decision()`.

This repository is preproduction and does not retain those wrappers as a public
backward-compatibility surface. Tests that need parser behavior target
`GroupDirectorService` directly rather than preserving wrapper functions for
test convenience.

## 12. Narrow dependency rule

PR #62 should not solve optionality by passing `BridgeServices` into every leaf module.

Use this rule:

- worker/router/controller boundary: `BridgeServices` is appropriate;
- feature helper needing one application service: pass that service;
- pure/backend helper: no application service dependency.

Examples:

```text
process_message(..., services)
    -> generate_and_store_reply(..., memory_service=services.memory,
                                 persona_service=services.persona)

process_callback(..., services)
    -> handle_persona_callback(..., persona_service=services.persona)
    -> handle_sync_callback(..., sync_service=services.sync)
```

This keeps dependency direction explicit without creating a new broad context object prematurely.

## 13. Failure semantics

Missing application services are programmer/configuration errors, not runtime fallback conditions.

After PR #62:

- constructing `BridgeServices` without one of the required services fails immediately;
- calling a required-service route without its service graph fails through normal Python signature/type errors;
- no code catches that absence and reconstructs a compatibility service.

Existing operational failures remain unchanged:

- Hindsight errors retain current fail-closed or fallback behavior where already defined by Memory workflows;
- Persona integrity/storage failures retain current behavior;
- Sync expected errors retain current disable/realtime behavior;
- Group Director generation failure retains its existing round-robin fallback inside `GroupDirectorService`;
- durable job failures retain existing `JobService` state transitions.

PR #62 changes dependency absence handling only.

## 14. Recovery and idempotency constraints

PR #62 must preserve:

1. reset durable phase ordering;
2. inactive-session deletion memory-purge ordering;
3. edited-turn recovery behavior;
4. `/regen` and `/continue` recovery behavior;
5. callback operation idempotency;
6. durable job start/complete/fail behavior;
7. worker DB ownership and closure;
8. Group Director fallback-to-round-robin behavior;
9. Sync realtime disable/conflict behavior;
10. Persona reference protection and integrity locking.

Service injection changes may not move transaction ownership or operation-phase boundaries.

## 15. Test strategy

Implementation must use TDD for the compatibility-retirement contract.

### 15.1 Initial RED guards

Add or extend architecture tests so they initially fail on the current baseline.

They should assert:

- `BridgeServices.memory` has no default;
- `BridgeServices.persona` has no default;
- `BridgeServices.sync` has no default;
- `BridgeServices.group_director` has no default;
- corresponding `build_bridge_services()` parameters have no defaults;
- no `compatibility_memory_service` remains;
- no `resolve_memory_service` remains;
- no `compatibility_persona_service` remains;
- no `resolve_persona_service` remains;
- no `compatibility_sync_service` remains;
- no `resolve_sync_service` remains;
- no `_compat_group_director_service` remains;
- application source does not contain optional `services=None` at the selected routing boundaries;
- application source does not use `getattr(services, ..., None)` for required application services.

### 15.2 Service-specific regression tests

Existing tests should be migrated to explicit fakes/mocks.

Coverage must continue proving:

Memory:
- prompt context;
- summary behavior;
- retention;
- reset purge;
- inactive-session purge;
- edit/recovery paths.

Persona:
- create;
- update;
- select;
- disable;
- delete-if-unused;
- UI/input routing.

Sync:
- status/callback behavior;
- manual sync;
- toggle;
- worker polling;
- expected failure behavior.

Group Director:
- decision parsing behavior;
- planning;
- prompt context;
- failure fallback.

### 15.3 Composition tests

Tests must prove:

- production startup constructs all required services;
- the same instances enter `BridgeServices`;
- message routing consumes the injected Memory/Persona/Group Director services;
- callback routing consumes the injected Persona/Sync services;
- sync worker startup consumes the injected Sync service;
- no application workflow constructs a compatibility service.

### 15.4 Full validation

Before PR completion, run the repository's normal gates:

```bash
python3 -m unittest discover -s tests -q
python3 -m pytest -q
python3 -m compileall -q bridge tests
python3 -m pip check
python3 -m pip_audit
```

Use the exact repository CI equivalents if the workflow differs.

## 16. Implementation sequence

The implementation plan should preserve this dependency order:

1. add RED architecture guards for required service fields and forbidden compatibility helpers;
2. make `BridgeServices` and `build_bridge_services()` require all application services;
3. migrate Memory callers to required explicit `MemoryService`;
4. delete Memory compatibility constructor/resolver;
5. migrate Persona callers to required explicit `PersonaService`;
6. delete Persona compatibility constructor/resolver;
7. migrate Sync callers and worker lifecycle to required explicit `SyncService`;
8. delete Sync compatibility constructor/resolver;
9. migrate Group Director callers to `services.group_director`;
10. delete Group Director compatibility construction/delegation that is no longer needed;
11. tighten message/callback/command routing signatures so the service graph cannot be omitted;
12. update tests to construct explicit service fakes;
13. run focused suites;
14. run full CI-equivalent validation;
15. review the final diff for accidental feature changes or unrelated refactoring;
16. create the PR from the feature branch to upstream `main`.

## 17. Expected file scope

Likely production files:

- `bridge/composition.py`
- `bridge/main.py`
- `bridge/memory.py`
- `bridge/message_commands.py`
- `bridge/telegram.py`
- `bridge/media.py` if optional Memory/Persona service signatures remain there
- `bridge/help.py` if optional service paths remain there
- `bridge/persona_sync.py`
- `bridge/input_flows.py`
- `bridge/callbacks.py`
- `bridge/panel_callback_routes.py`
- `bridge/sync_api.py`
- `bridge/groups.py`
- command-routing modules only where required for explicit service propagation

Likely tests:

- `tests/test_composition.py`
- `tests/test_native_runtime_retirement.py`
- `tests/test_memory_service.py`
- `tests/test_persona_service.py`
- `tests/test_sync_service.py`
- `tests/test_group_director_service.py`
- integration/UI tests affected by formerly optional service arguments

Exact file count is not binding. Scope is defined by removing the compatibility application-service path, not by a target number of files.

## 18. Documentation

README architecture documentation should require no major conceptual rewrite after PR #61.

If implementation changes the public architecture wording materially, update only the affected composition description.

Historical design specs remain historical records and are not rewritten merely because their compatibility phases have now ended.

## 19. Acceptance criteria

PR #62 is complete when all of the following are true:

1. `BridgeServices` requires `jobs`, `memory`, `persona`, `sync`, and `group_director`.
2. `build_bridge_services()` requires all five application services.
3. Production startup explicitly constructs and supplies all five.
4. No Memory compatibility constructor/resolver remains.
5. No Persona compatibility constructor/resolver remains.
6. No Sync compatibility constructor/resolver remains.
7. No Group Director compatibility constructor remains.
8. No production application workflow reconstructs a service because an injected one is absent.
9. Selected application routing boundaries no longer support `services=None`.
10. Leaf helpers receive explicit narrow services rather than discovering them globally.
11. No process-global service locator is introduced.
12. Existing feature behavior and recovery semantics remain covered.
13. Architecture regression guards prevent compatibility service reconstruction from returning.
14. Full CI-equivalent validation passes at the exact PR head.
15. Final diff review finds no unrelated feature/refactor expansion.

## 20. Follow-up boundary

PR #62 deliberately leaves `bridge.runtime_context` in place.

The next architecture phase after PR #62 should address ambient DB/panel/actor thread-local context by introducing an explicit request/job context. Only after that context is explicit should `bridge.main` be decomposed into smaller startup, polling/dispatch, and worker-entry modules.

That sequencing avoids mixing three independent architectural changes into one PR:

1. application-service compatibility retirement — PR #62;
2. ambient runtime-context retirement — follow-up;
3. `bridge.main` decomposition — subsequent follow-up.

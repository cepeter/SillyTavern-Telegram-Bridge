# Runtime Architecture Migration — Master Design

Date: 2026-09-19
Status: Master architecture target; implementation is decomposed into independently mergeable phases
Repository: `punzer4-code/SillyTavern-Telegram-Bridge`
Baseline: post-the merged extension-registry refactor architecture
Phase 1 design: `docs/superpowers/specs/2026-09-19-director-policy-boundary-design.md`

## 1. Purpose

This document defines the long-term architecture target for the SillyTavern Telegram Bridge and the migration program used to reach it without a big-bang rewrite.

the merged extension-registry refactor established explicit extension hooks for several newer cross-cutting features. The codebase still relies on a compatibility runtime that executes modules into one shared namespace, selected late overrides for recovery and safety behavior, global runtime state, mixed schema/runtime initialization, and helper functions that often own their own commits.

The migration must remove those architectural constraints while preserving the reliability properties already present in the application.

The governing rule is:

> Core services own execution. Policies, repositories, and infrastructure adapters provide explicit capabilities through narrow interfaces.

The migration is planned as one architecture program, but implemented as sequential pull requests. Every phase must leave the repository deployable, testable, and behaviorally compatible.

## 2. Why this is one program but not one PR

The target architecture spans multiple independent concerns:

- runtime composition
- feature policy boundaries
- database schema migration
- transaction ownership
- application services
- infrastructure adapters
- safety/recovery layers
- global configuration/runtime state
- eventual removal of `exec()`-based shared-namespace loading

Changing all of them in one PR would make regressions hard to isolate and review. It would also couple database, job recovery, sync, memory, and group-generation changes in one failure domain.

Therefore:

- there is one master target
- each phase has a narrow purpose
- each phase has explicit acceptance criteria
- each phase must pass the full regression suite
- later phases build only on merged, green predecessors

## 3. Current architecture

The current runtime is a compatibility facade.

```text
bridge/runtime.py
      |
      v
bridge/runtime_loader.py
      |
      +-- exec() module source into one shared namespace
      |
      +-- validate ordered runtime stages
      |
      +-- permit selected late public-callable overrides
```

The loader currently separates:

- core modules
- recovery overrides
- sync extensions
- native adapter overrides
- identity extensions
- safety overrides

This staging makes load order explicit, but correctness still partially depends on that order.

The remaining safety/recovery layers include late replacement of behavior such as:

- operation/recovery functions
- sync hardening
- Persona integrity
- Hindsight retention
- database/job scheduling helpers
- Group Director policy

the merged extension-registry refactor removed late override composition for Scene State and Memory Curator and moved several cross-cutting behaviors into explicit registries.

That registry is useful as a migration boundary, but it is not the intended final application architecture.

## 4. Current strengths that must be preserved

The migration must not discard the reliability engineering already present.

Important properties include:

- durable job state persisted in SQLite
- crash recovery for queued/running work
- idempotent operation handling
- WAL-based SQLite configuration
- busy-timeout and contention handling
- bounded background executors
- session isolation
- stale Hindsight snapshot protection
- bounded Scene State and Memory Curator processing
- deterministic Group Director fallback
- sync safety/backoff behavior
- explicit runtime override auditing
- broad regression coverage

Architecture cleanup is successful only if those properties survive.

## 5. Target architecture

The long-term target is a single-process Ports-and-Adapters architecture with explicit application-service composition.

```text
Telegram / UI Adapters
        |
        v
Application Services
        |
        +-- ConversationService
        +-- GroupService
        +-- GroupDirectorService
        +-- MemoryService
        +-- PersonaService
        +-- SyncService
        +-- JobService
        |
        v
Domain Policies / Ports
        |
        +-- DirectorPolicy
        +-- ModelRouter
        +-- MemoryBackend
        +-- PersonaStore
        +-- SyncBackend
        +-- JobRepository
        |
        v
Infrastructure Adapters
        |
        +-- SQLite repositories
        +-- Hindsight
        +-- SillyTavern native files/settings
        +-- Telegram API
        +-- model provider APIs
```

A composition root constructs these dependencies once at startup.

Conceptually:

```python
services = BridgeServices(
    config=config,
    database=database,
    groups=group_service,
    director=director_service,
    memory=memory_service,
    personas=persona_service,
    sync=sync_service,
    jobs=job_service,
)
```

The end state does not depend on function replacement, import/load order, or global discovery of implementation behavior.

## 6. Architectural principles

### 6.1 One execution owner

Every important workflow has one canonical execution owner.

Examples:

- Group Director execution belongs to `GroupDirectorService`
- memory lifecycle belongs to `MemoryService`
- Persona lifecycle belongs to `PersonaService`
- durable job lifecycle belongs to `JobService`

Extensions may supply policy or infrastructure, but they must not duplicate the workflow.

### 6.2 Explicit dependencies

A unit should receive what it needs through constructor/function arguments or a deliberately injected service container.

No new behavior should depend on:

- implicit module load order
- captured `_ORIGINAL_*` functions
- replacement of public functions
- hidden mutable globals when an explicit dependency is practical

### 6.3 Hooks only for true events

Hooks are appropriate when zero or more independent consumers may react to an event.

Examples:

- post-retain notification
- summary-clear notification

Hooks are not the preferred mechanism when exactly one component should own a decision.

Examples that should use explicit ports instead:

- model routing
- transaction ownership
- Persona persistence
- job scheduling
- memory backend selection

### 6.4 Repositories do persistence; services own use cases

Repositories expose data access operations.

Application services coordinate:

- transactions
- policies
- repositories
- external adapters
- user-visible workflow semantics

A repository getter must not create schema or commit unrelated work.

### 6.5 Schema migration happens at startup

Schema creation and evolution are startup concerns, not request-path concerns.

Feature getters must not execute `CREATE TABLE`, `ALTER TABLE`, or commit schema changes.

### 6.6 Reliability before purity

Migration order is chosen to reduce risk, not to make the architecture look clean as quickly as possible.

Recovery, job scheduling, sync hardening, and stale-memory protection remain in place until their explicit replacements have equivalent regression coverage.

## 7. Dependency direction

Target dependency direction:

```text
Adapters -> Application Services -> Domain Ports/Policies
                               -> Repositories (interfaces)
Infrastructure ----------------^ implements ports/repositories
```

Domain/application layers must not import Telegram-specific, filesystem-specific, Hindsight-specific, or provider-specific implementation details unless that implementation is itself the boundary being migrated.

## 8. Compatibility migration strategy

The migration uses a strangler approach.

Existing runtime behavior remains callable while new explicit boundaries are introduced around it.

The sequence is:

```text
late override
    |
    v
explicit compatibility boundary
    |
    v
ordinary typed interface
    |
    v
injected service/adapter
    |
    v
remove compatibility layer
```

The compatibility registry introduced in the merged extension-registry refactor is intentionally temporary. It provides deterministic extension boundaries while the composition root does not yet exist.

## 9. Migration invariants

From the start of this migration, new code must not add:

- new `_ORIGINAL_*` capture chains
- new public-callable runtime overrides
- new schema creation from read/getter paths
- new hidden commits in read helpers
- new feature-global registries when a single explicit owner is more appropriate
- duplicated copies of an existing execution workflow
- new dependencies on runtime module ordering

Existing instances may remain temporarily only until the phase responsible for replacing them.

## 10. Phase map

The migration is decomposed into seven primary implementation phases.

### Phase 1 — DirectorPolicy boundary

Detailed design:
`docs/superpowers/specs/2026-09-19-director-policy-boundary-design.md`

Purpose:

- remove the two remaining Director Goals public-callable overrides
- keep one canonical Group Director execution path
- establish a narrow policy boundary that later maps directly to dependency injection

Completion signal:

- `director_goals.py` has zero public-callable overrides
- Group Director behavior and fallbacks are unchanged

### Phase 2 — Versioned database migrations

Purpose:

- replace ad hoc feature schema creation with a centralized migration runner
- record applied migration versions
- move Scene State, Director Goals, Memory Curator, and other feature DDL into startup migrations
- make schema initialization deterministic and testable from empty and legacy databases

Target shape:

```text
db_connect()
    |
    v
MigrationRunner
    |
    +-- migration 001
    +-- migration 002
    +-- ...
    |
    v
schema ready
```

Requirements:

- existing data remains intact
- migrations are idempotent
- migration ordering is explicit
- old databases upgrade in place
- startup failure reports the failing migration clearly
- request-time getters perform no DDL

This phase does not yet require a general ORM or full repository rewrite.

### Phase 3 — Repository and transaction ownership

Purpose:

- make database reads side-effect free
- separate SQL persistence helpers from application transaction ownership
- remove unnecessary helper-level commits from the first migrated domains

Initial domains:

- Director Goals
- Scene State
- Memory Curator
- Group persistence where directly required

Rules:

- repository reads never commit
- repository writes do not silently decide unrelated transaction scope
- service/use-case boundaries decide commit/rollback
- multi-write operations can become atomic without fighting nested helper commits

A lightweight SQLite repository layer is preferred over an ORM.

### Phase 4 — Composition root and runtime context

Purpose:

- introduce explicit configuration and service construction
- begin replacing module-global discovery with injected dependencies

Target types:

```python
@dataclass
class BridgeConfig:
    ...

@dataclass
class BridgeServices:
    ...
```

The composition root owns construction of:

- configuration
- database factory/repositories
- executors/schedulers
- memory backend
- sync adapter
- Persona store
- service instances

This phase must coexist with the compatibility runtime; it does not remove `runtime_loader.py` yet.

### Phase 5 — Application service extraction

Purpose:

Extract cohesive workflows incrementally.

Recommended order:

1. `GroupDirectorService`
2. `MemoryService`
3. `PersonaService`
4. `SyncService`
5. `JobService`
6. remaining conversation/generation coordination as justified

Each service extraction must:

- preserve current behavior
- own one clear workflow
- expose a small public interface
- depend on explicit ports/repositories
- avoid importing unrelated UI/router logic

Large routing/UI modules may then delegate to services rather than own business logic.

### Phase 6 — Replace safety/recovery overrides with adapters and decorators

Purpose:

Remove late hardening overrides only after explicit service/port boundaries exist.

Examples:

```text
PersonaStore
    |
    v
IntegrityCheckedPersonaStore
    |
    v
SillyTavernPersonaStore
```

```text
MemoryBackend
    |
    v
StaleGuardMemoryBackend
    |
    v
HindsightMemoryBackend
```

Scheduler/recovery behavior should similarly become explicit collaborators of `JobService` rather than replacements for globally defined functions.

Requirements:

- no loss of stale-snapshot protection
- no loss of serialized Persona writes
- no loss of scheduler durability
- no loss of sync backoff/recovery semantics

### Phase 7 — Retire compatibility runtime loading

Purpose:

Remove the shared `exec()` namespace only after the application no longer depends on it.

Preconditions:

- ordinary imports are viable
- service construction is explicit
- no public-callable override allowlist remains
- no `_ORIGINAL_*` override chains remain
- compatibility registries have either become ordinary injected dependencies or retained only where true event semantics justify them
- tests no longer depend on mutating one shared runtime namespace

Expected end state:

- `bridge/runtime_loader.py` is deleted or reduced to a temporary compatibility shim with no production role
- `bridge/runtime.py` becomes a normal facade/composition entry point or disappears
- import order no longer changes behavior

## 11. Database migration design direction

The current schema module already centralizes substantial DDL, but evolution is performed through table inspection and conditional alterations. Newer features may still create their own schema at runtime.

The target migration system should use an explicit ledger.

Conceptually:

```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at REAL NOT NULL
);
```

Each migration is ordered and applied exactly once.

The migration framework should support:

- empty database bootstrap
- legacy database upgrade
- transactional migrations where SQLite permits
- clear failure reporting
- test fixtures for historical schema versions

Maintenance tasks such as row-retention cleanup should remain separate from structural migrations when possible.

## 12. Transaction model

The target convention is:

```text
Application service/use case
        |
        +-- begin transaction
        |
        +-- repository operation
        +-- repository operation
        +-- external decision if safe/appropriate
        |
        +-- commit
```

Not every use case needs an explicit multi-statement transaction, but transaction ownership must be visible.

Rules:

- a read does not commit
- a getter does not migrate schema
- repository helpers do not unexpectedly commit caller-owned work
- operations that require durable handoff keep the durability semantics they have today
- SQLite lock behavior remains bounded and tested

## 13. Configuration and mutable runtime state

Global state is reduced gradually.

Long-term candidates for explicit ownership include:

- DB path/configuration
- provider/model configuration
- executor instances
- semaphores
- caches
- lifecycle state
- locks
- job dispatch state

Not every lock must become an object immediately. Migration should prioritize globals that impede isolation, tests, or clear ownership.

Tests should increasingly construct a test context instead of patching global runtime state.

## 14. Error-handling policy

The migration must preserve semantic differences between failure types.

### User command failures

A recognized extension command must not silently fall through into ordinary character generation.

### Optional event hooks

Independent optional listeners should isolate failures where one listener should not block others.

### Optional policies

If an optional policy fails, the owning core service should use a documented default/fallback when safe.

### Persistence failures

Database write failures propagate to the transaction/use-case boundary, which decides rollback and user/job failure reporting.

### Infrastructure failures

Network/model/sync failures continue to use the existing retry/backoff/durable-job semantics until a replacement explicitly preserves them.

## 15. Testing strategy

Every migration phase must maintain both unit and integration confidence.

Required categories over the whole program:

- runtime/load determinism until runtime loader retirement
- migration tests from empty DB
- migration tests from representative legacy schemas
- SQLite contention tests
- transaction rollback tests
- job crash/recovery tests
- stale-memory protection tests
- Group Director behavior/fallback tests
- sync backoff/conflict tests
- Persona serialization/integrity tests
- command routing tests
- service tests using fake ports/adapters
- end-to-end smoke tests through the normal runtime entry point

Tests should progressively move away from patching `bridge.runtime` globals and toward constructing explicit dependencies.

## 16. CI evolution

CI should evolve with the architecture.

Near-term additions after explicit imports become common:

- linting
- static type checking for new service/port boundaries
- architecture checks preventing forbidden dependency directions

Possible architecture assertions:

- no new `_ORIGINAL_*` symbols outside explicitly grandfathered files
- no new runtime override allowlist entries
- no DDL statements in repository getter modules
- no service-to-Telegram adapter imports

These checks should be introduced incrementally so they enforce the migration rather than block it prematurely.

## 17. Pull-request discipline

Each migration PR must:

1. have one primary architectural claim
2. preserve user-visible behavior unless change is explicitly in scope
3. include regression tests for the boundary being moved
4. avoid unrelated cleanup
5. update this master spec only when the target architecture itself changes
6. leave the branch deployable
7. pass the full repository CI before merge

A later phase may be split into multiple PRs if implementation proves larger than expected. The master phase number describes architectural order, not a requirement that every phase fit exactly one PR.

## 18. Rollback and compatibility strategy

Each phase should be reversible through ordinary Git rollback without requiring a coordinated database downgrade, unless that phase explicitly introduces a forward-only migration.

Database migrations must favor additive/backward-compatible changes during the transition.

Destructive schema changes should be deferred until the old runtime path is no longer needed.

Feature flags are not required by default; they should be introduced only where a migration cannot be made behaviorally transparent.

## 19. Final acceptance criteria

The migration program is complete when all of the following are true:

- zero `_ORIGINAL_*` function-capture chains remain
- zero public-callable runtime overrides remain
- zero behavior depends on runtime module execution order
- ordinary Python imports are used for production composition
- schema evolution is versioned and centralized
- feature getters perform no schema creation
- read helpers perform no implicit commits
- transaction ownership is explicit at service/use-case boundaries
- one startup composition root constructs application dependencies
- core workflows live in focused application services
- external systems are accessed through explicit ports/adapters
- safety hardening is represented through services, policies, adapters, or decorators rather than late replacement
- tests can construct isolated service graphs without mutating one shared runtime namespace
- the compatibility `exec()` loader has no production role

## 20. Definition of architectural maturity

The codebase should satisfy this mental model:

```text
Can I understand a workflow by starting at one service?
Can I identify who owns its transaction?
Can I replace an external dependency with a fake?
Can I change one adapter without changing its consumers?
Can I import modules in ordinary Python order without changing behavior?
Can I initialize the database once and then treat schema as stable?
```

If the answer to all six questions is yes, the migration has achieved its intended result.

## 21. Relationship to Phase 1

The DirectorPolicy design is the first detailed implementation slice under this master target.

It intentionally uses the compatibility extension registry because the composition root does not yet exist.

That registry slot is transitional.

Later, the same semantic interface should become an injected `DirectorPolicy` dependency of `GroupDirectorService` without changing the policy responsibility defined in Phase 1.

This preserves work across migration phases instead of replacing one temporary architecture with another unrelated one.

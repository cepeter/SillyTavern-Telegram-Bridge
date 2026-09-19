# Versioned Database Migrations — Design

Date: 2026-09-19  
Status: written-spec review pending  
Parent architecture: `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`  
Migration phase: Phase 2  
Repository: `cepeter/SillyTavern-Telegram-Bridge`  
Baseline observed while writing: upstream `main` at `59f53caa49fcbd13843789a50fa2268fab7cce34`  
Target implementation: Phase 2 implementation PR (number assigned when opened)

## 1. Purpose

Phase 2 replaces ad hoc schema creation and schema inspection with one explicit, versioned startup migration path.

The runtime currently has three different structural-schema patterns:

1. `bridge/schema.py` creates the core/generation/RAG/job/panel schema and performs conditional `ALTER TABLE` upgrades.
2. `bridge/scene_state.py` and `bridge/director_goals.py` create their own tables/triggers from request-time helpers.
3. `bridge/sync_safety.py` creates a session-cleanup trigger from a late `initialize_database_schema()` override.

These patterns work, but they make schema evolution implicit, difficult to audit, and partially dependent on which feature path has executed.

The Phase 2 target is:

```text
db_connect()
    |
    v
initialize_database_schema()
    |
    v
run_migrations(db, SCHEMA_MIGRATIONS)
    |
    +-- schema_migrations ledger
    +-- 001 current core baseline / legacy reconciliation
    +-- 002 scene state
    +-- 003 director goals
    +-- 004 sync lifecycle trigger
    |
    v
schema ready
```

After Phase 2, all structural database changes are owned by ordered startup migrations. Request-time feature getters and commands assume the schema already exists.

## 2. Goals

Phase 2 must:

- introduce an explicit `schema_migrations` ledger
- apply migrations in deterministic numeric order
- preserve existing user data
- upgrade existing databases in place
- bootstrap a completely empty database
- make repeated startup idempotent
- identify the exact migration that failed
- keep migration history internally consistent
- move Scene State structural DDL out of `scene_state.py`
- move Director Goals structural DDL out of `director_goals.py`
- move the sync session-cleanup trigger out of `sync_safety.py`
- keep non-structural sync orphan cleanup separate from structural migrations
- remove request-time schema creation from Scene State and Director Goals call paths
- preserve the current once-per-process database initialization guard in `scheduler_safety.py`
- provide test fixtures for empty, current-without-ledger, and representative legacy databases
- leave the repository ready for all future schema changes to be added as new numbered migrations

## 3. Non-goals

Phase 2 does not:

- introduce an ORM
- redesign repositories
- move transaction ownership into application services
- eliminate all helper-level `commit()` calls
- make every getter side-effect free
- change SQLite WAL, busy timeout, or connection-pragmas policy
- redesign job durability or scheduler recovery
- remove `scheduler_safety.py`'s late `db_connect()` override
- remove `sync_safety.py`'s compatibility override entirely
- remove the shared compatibility runtime loader
- perform destructive schema cleanup
- compact or rewrite existing user data
- redesign Scene State, Director Goals, RAG, group, sync, or panel behavior
- add migrations for Memory Curator when no SQLite structural state exists for that feature

Repository/transaction cleanup belongs to Phase 3. Safety/compatibility override removal belongs to later roadmap phases.

## 4. Current Schema Ownership

### 4.1 Core schema

`bridge/schema.py` currently owns five schema groups:

- `_ensure_core_tables()`
- `_ensure_generation_tables()`
- `_ensure_rag_tables()`
- `_ensure_job_tables()`
- `_ensure_panel_tables()`

The current baseline contains conditional upgrades for historical columns including:

- `messages.session_id`
- `messages.telegram_message_id`
- `messages.telegram_message_ids`
- `sessions.author_note`
- `sessions.system_prompt`
- `sessions.response_language`
- `response_variants.user_rowid`
- `data_bank_documents.version_number`
- `data_bank_documents.active`
- `data_bank_embeddings.embedding_namespace`
- `data_bank_embeddings.vector_signature`
- `data_bank_embeddings.vector_norm`
- `rag_embedding_cache.vector_norm`
- `failed_turns.session_id`
- `panel_sessions.owner_user_id`
- current `sync_bindings` compatibility columns
- `group_sessions.mode`
- `group_sessions.forced_speaker`
- `group_sessions.turn_user_id`
- `group_sessions.turn_users_json`

These conditional upgrades are valuable legacy reconciliation logic. Phase 2 keeps that behavior but runs it exactly once as the first recorded migration for databases that do not yet have migration history.

### 4.2 Scene State

`bridge/scene_state.py` currently owns:

- `scene_states` table
- `scene_states_updated_idx`
- `scene_states_session_delete` trigger

Multiple runtime paths call `ensure_scene_state_schema()`, including reads and clears.

After Phase 2, `scene_state.py` performs no structural DDL.

### 4.3 Director Goals

`bridge/director_goals.py` currently owns:

- `director_goals` table
- `director_goals_session_delete` trigger

`get_director_goal()` and `set_director_goal()` call `ensure_director_goal_schema()`.

After Phase 2, Director Goal reads/writes assume startup migration has already created the schema.

### 4.4 Sync safety

`bridge/sync_safety.py` currently wraps `initialize_database_schema()` and performs two different responsibilities:

- data maintenance: delete orphaned `sync_bindings`
- structural migration: create `sessions_delete_sync_binding`

Phase 2 separates those responsibilities:

- the trigger becomes a numbered schema migration
- orphan cleanup remains startup maintenance in the compatibility layer for now

### 4.5 Memory Curator

The current Memory Curator implementation has no feature-specific SQLite table, index, trigger, or column migration.

Therefore Phase 2 adds no Memory Curator migration. The master roadmap's generic reference to Memory Curator DDL does not create artificial schema work where none exists.

## 5. Chosen Architecture

Phase 2 uses two layers.

### 5.1 Generic migration engine: `bridge/migrations.py`

A new standalone module owns migration mechanics only.

Conceptual interface:

```python
@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


class MigrationError(RuntimeError):
    ...


def run_migrations(
    db: sqlite3.Connection,
    migrations: Sequence[Migration],
) -> None:
    ...
```

This module owns:

- ledger bootstrap
- declaration validation
- applied-history validation
- migration ordering
- per-migration transaction boundary
- migration ledger insertion
- rollback on failure
- migration-specific failure reporting

It does not contain application-specific schema SQL.

### 5.2 Schema declaration: `bridge/schema.py`

`bridge/schema.py` remains the owner of application schema definitions.

It will:

- explicitly import `sqlite3` so it is valid as an ordinary module, not only inside the shared runtime namespace
- retain/refactor the existing core schema helper functions
- define the Scene State migration DDL
- define the Director Goals migration DDL
- define the sync lifecycle trigger migration DDL
- expose an immutable ordered `SCHEMA_MIGRATIONS`
- keep `initialize_database_schema(db)` as the compatibility entry point
- delegate `initialize_database_schema(db)` to `run_migrations(db, SCHEMA_MIGRATIONS)`

This split keeps migration mechanics independent from domain schema while avoiding a new ORM or schema framework.

## 6. Migration Ledger

The migration engine bootstraps:

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at REAL NOT NULL
);
```

The ledger is infrastructure for migrations and is not itself represented as a numbered application migration.

Ledger rules:

- version numbers are positive integers
- declared migrations must be strictly increasing
- declared migration versions must be unique
- declared migration names are immutable identifiers
- an applied ledger row must match the current declared name for that version
- applied migration history must be a valid prefix of the declared migration list
- unknown future versions cause startup to fail safely
- a gap such as applied versions `1, 3` with missing `2` is treated as invalid history rather than silently applying migrations out of order

No checksum is introduced in Phase 2. Migration functions are treated as immutable after merge; later schema changes require a new migration version.

## 7. Migration Transactions and Failure Semantics

Each missing migration is applied independently.

Conceptual sequence:

```text
BEGIN IMMEDIATE
    migration.apply(db)
    INSERT schema_migrations(version, name, applied_at)
COMMIT
```

If any migration operation fails:

1. rollback the active migration transaction
2. do not insert its ledger row
3. raise `MigrationError` containing:
   - version
   - migration name
   - original exception as cause
4. stop startup immediately
5. leave later migrations unapplied

This gives a precise failure such as:

```text
Migration 003 (director_goals) failed: <sqlite error>
```

A later startup retries the failed migration because it was never recorded as applied.

SQLite database-wide PRAGMAs such as `auto_vacuum` and `journal_mode=WAL` remain outside migration transactions in the existing connection setup.

## 8. Migration 001 — Current Core Baseline / Legacy Reconciliation

Migration 001 is intentionally not an invented reconstruction of every historical release.

Its purpose is to bring either an empty database or a pre-ledger legacy database to the current core schema.

It reuses the current idempotent structural behavior represented by:

- core tables/indexes
- generation settings/presets
- RAG/data-bank tables/indexes
- failed-turn/job tables/indexes
- panel/sync/group tables/indexes
- all currently required conditional legacy column additions

Properties:

- empty DB: creates the current core schema
- current DB without ledger: verifies/reconciles the current shape and records 001
- old DB: performs the same additive conditional upgrades the application already performs today
- existing rows remain intact
- no destructive column/table rebuild is introduced
- no migration is recorded until the complete baseline reconciliation succeeds

This is the compatibility bridge from implicit historical upgrades to explicit forward-only migrations.

Once Phase 2 merges, future schema evolution must not be folded back into migration 001.

## 9. Migration 002 — Scene State

Migration 002 creates:

- `scene_states`
- `scene_states_updated_idx`
- `scene_states_session_delete`

The SQL remains idempotent with `IF NOT EXISTS`.

After migration 002:

- `ensure_scene_state_schema()` is removed or reduced out of production use
- `get_scene_state()` performs only its data read
- `clear_scene_state()` performs only its data mutation
- Scene refresh paths no longer create schema
- background Scene State workers rely on `db_connect()` startup migration

Existing `scene_states` data is preserved when a current database without a ledger first runs migration 002.

## 10. Migration 003 — Director Goals

Migration 003 creates:

- `director_goals`
- `director_goals_session_delete`

After migration 003:

- `ensure_director_goal_schema()` is removed or reduced out of production use
- `get_director_goal()` performs only its SELECT
- `set_director_goal()` performs only its data mutation
- Director policy evaluation never performs schema DDL

Existing Director Goal rows remain intact when the migration ledger is first introduced.

## 11. Migration 004 — Sync Lifecycle Trigger

Migration 004 creates:

- `sessions_delete_sync_binding`

The trigger is structural schema and therefore belongs in the migration registry.

The existing orphan cleanup:

```sql
DELETE FROM sync_bindings
WHERE NOT EXISTS (...)
```

is not a schema migration. It is data maintenance.

For Phase 2, the compatibility `sync_safety.py` startup wrapper may continue to run that cleanup after `initialize_database_schema()`, but it must no longer execute `CREATE TRIGGER`.

A later roadmap phase can move or remove the compatibility wrapper entirely.

## 12. Startup Flow and Existing Safety Layer

Production startup currently relies on `scheduler_safety.py` to ensure database-wide setup is executed only once per process:

```text
scheduler_safety.db_connect()
    |
    +-- first caller:
    |      acquire _DB_SCHEMA_LOCK
    |      call original db_connect()
    |          -> PRAGMAs / WAL / optional extension
    |          -> initialize_database_schema()
    |              -> migrations
    |          -> sync orphan maintenance through current compatibility wrapper
    |      set _DB_SCHEMA_READY
    |
    +-- later callers:
           lightweight connection only
```

Phase 2 preserves:

- `_DB_SCHEMA_LOCK`
- `_DB_SCHEMA_READY`
- lightweight worker connections
- one-time database-wide startup behavior

The migration runner must still be correct when called more than once, but Phase 2 does not remove the existing process-level optimization.

## 13. Request-time Schema Rule

After Phase 2, application request paths may assume startup migration completed successfully.

The following are prohibited from request-time feature functions:

- `CREATE TABLE`
- `CREATE INDEX`
- `CREATE TRIGGER`
- `ALTER TABLE`
- migration-ledger mutation

This specifically applies to:

- Scene State reads/writes/refresh
- Director Goal reads/writes/policy evaluation
- sync request handling

This rule does not yet prohibit ordinary data writes or helper-level commits. For example, `get_generation_settings()` currently performs `INSERT OR IGNORE` to materialize default settings; that is transaction/repository debt for Phase 3, not structural DDL debt for Phase 2.

## 14. Data Preservation and Compatibility

Phase 2 is additive and compatibility-first.

It must preserve:

- messages and Telegram delivery IDs
- sessions
- response variants
- generation settings/presets
- RAG/data-bank content and embeddings metadata
- failed turns and durable jobs
- panel state
- sync bindings
- group sessions
- Scene State rows
- Director Goal rows

Migration 001 preserves the current conditional-`ALTER TABLE` upgrade behavior.

Migrations 002–004 use idempotent create operations so a database that already acquired those feature structures through request-time initialization can adopt the ledger without rebuilding or deleting them.

No table rename, destructive rebuild, column drop, or data-copy migration belongs in this phase.

## 15. Migration History Safety

The runner validates migration history before applying anything.

Examples:

### Valid

Declared:

```text
001 core_baseline
002 scene_state
003 director_goals
004 sync_lifecycle_trigger
```

Applied:

```text
001 core_baseline
002 scene_state
```

Result: apply 003 then 004.

### Invalid name drift

Ledger:

```text
002 old_scene_name
```

Code:

```text
002 scene_state
```

Result: fail startup with migration-history mismatch.

### Invalid gap

Ledger:

```text
001 core_baseline
003 director_goals
```

Result: fail startup rather than applying 002 after 003.

### Future database

Ledger contains version 005 while this binary knows only through 004.

Result: fail startup instead of allowing an older binary to mutate an unknown newer schema.

## 16. Testing Strategy

A new focused migration test module should cover the migration engine and application schema together.

### 16.1 Empty database bootstrap

Start with a blank temporary SQLite file.

Assert:

- ledger exists
- versions 001–004 are recorded in order
- all current core tables/columns exist
- Scene State structures exist
- Director Goals structures exist
- sync lifecycle trigger exists
- running initialization again changes no migration history

### 16.2 Current database without ledger

Construct a database matching the current production schema, including existing Scene/Director structures, but without `schema_migrations`.

Seed representative data before migration.

Assert:

- 001–004 are recorded
- rows remain unchanged
- no duplicate/destructive structures are created
- startup succeeds repeatedly

### 16.3 Representative legacy database upgrade

Construct a historical-style database missing a representative set of columns that the current baseline reconciliation already handles, including cases from:

- messages
- sessions
- response variants
- RAG/data-bank metadata
- failed turns
- panel sessions
- sync bindings
- group sessions

Seed rows before migration.

Assert:

- required columns are added
- defaults are valid
- original row content survives
- all migrations are recorded only after successful reconciliation

The test fixture does not need to reconstruct every released historical schema. It must exercise each category of current conditional upgrade behavior sufficiently to prove migration 001 retained it.

### 16.4 Failure rollback

Use a small synthetic migration sequence against the generic runner:

```text
001 succeeds
002 deliberately fails
003 would succeed
```

Assert:

- 001 is recorded
- 002 is not recorded
- changes performed inside failed 002 are rolled back
- 003 is not executed
- raised error identifies version 002 and its name

### 16.5 Migration-history validation

Test:

- duplicate declared versions rejected
- non-increasing declaration rejected
- applied-name mismatch rejected
- applied-version gap rejected
- unknown future applied version rejected

### 16.6 Request-time no-DDL regression

Initialize the database first, then use SQLite trace callbacks around representative operations:

- `get_scene_state()`
- `clear_scene_state()`
- `get_director_goal()`
- `set_director_goal()`
- Director policy lookup

Assert those operations issue no structural DDL statements.

The test should inspect actual executed SQL rather than merely grep source.

### 16.7 Runtime compatibility

Existing runtime-loader/scheduler tests must continue proving:

- one-time schema initialization remains deterministic
- lightweight worker connections do not renegotiate WAL/schema
- current safety override ordering still works
- no new public runtime override is introduced by Phase 2

## 17. Expected File Responsibilities

### New: `bridge/migrations.py`

Owns:

- `Migration`
- `MigrationError`
- ledger bootstrap
- migration declaration/history validation
- ordered transactional execution

Contains no feature-specific SQL.

### Modify: `bridge/schema.py`

Owns:

- schema-specific migration functions
- existing legacy core reconciliation helpers
- `SCHEMA_MIGRATIONS`
- compatibility entry point `initialize_database_schema()`

Must become directly importable by explicitly importing its own dependencies rather than assuming shared runtime globals.

### Modify: `bridge/scene_state.py`

Owns Scene State behavior only.

Must stop creating structural schema.

### Modify: `bridge/director_goals.py`

Owns Director Goal persistence/policy behavior only.

Must stop creating structural schema.

### Modify: `bridge/sync_safety.py`

Retains temporary startup orphan cleanup and other sync safety behavior.

Must stop creating the sync lifecycle trigger.

### Modify: migration/schema tests

Add dedicated migration fixtures and no-DDL regression coverage.

## 18. CI and Architecture Invariants

Phase 2 should add or make enforceable these invariants:

- new structural schema changes require a new numbered migration
- request-time feature modules contain no structural DDL
- `schema_migrations` history is deterministic
- migration versions are immutable and ordered
- an existing database can be reopened repeatedly without schema churn
- the complete existing test suite remains green
- SQLite contention/recovery tests remain green
- runtime-loader override audit remains green

A lightweight source-level CI assertion may be added for known request-time modules if useful, but behavior-level SQL trace tests are the primary protection.

## 19. Error Handling

Migration failures are startup failures.

They must not be swallowed or converted into ordinary feature fallbacks.

The application must surface:

- migration version
- migration name
- underlying SQLite error

Optional feature degradation is appropriate for model/policy failures, but not for an unknown or partially migrated database schema.

This distinction is intentional.

## 20. Rollback Strategy

Code rollback is straightforward before a new schema migration has shipped to production.

After numbered migrations have been applied:

- migrations are forward-only
- released migration definitions are immutable
- rollback of application code must not assume the database reverted
- additive/backward-compatible schema changes are preferred throughout the migration program
- destructive schema changes require a separately designed later migration and compatibility plan

Phase 2 itself performs only additive/reconciliation operations.

## 21. Acceptance Criteria

Phase 2 is complete when all of the following are true:

1. `schema_migrations` exists and records ordered versions/names/timestamps.
2. A generic migration runner applies only the missing suffix of declared migrations.
3. Migration declaration/history inconsistencies fail startup clearly.
4. A failed migration rolls back and is not recorded.
5. Empty database bootstrap produces the complete current schema.
6. A current pre-ledger database adopts versions 001–004 without data loss.
7. Representative legacy databases upgrade through migration 001 without data loss.
8. Scene State DDL exists only in the migration-owned schema path.
9. Director Goals DDL exists only in the migration-owned schema path.
10. The sync lifecycle trigger is migration-owned.
11. Sync orphan cleanup remains separate from structural migration.
12. Memory Curator receives no artificial migration because it has no feature SQLite schema.
13. Scene State request-time operations execute no structural DDL.
14. Director Goal request-time operations execute no structural DDL.
15. `scheduler_safety.py` still preserves once-per-process database-wide initialization.
16. Existing WAL/lightweight-worker behavior is unchanged.
17. Existing user data survives migration fixtures.
18. Full repository CI passes.
19. No Phase 3 repository/transaction refactor is mixed into this change.
20. No new public runtime override or `_ORIGINAL_*` chain is introduced.

## 22. Rejected Approaches

### 22.1 Reconstruct every historical release as a migration

Rejected because the repository's existing upgrade logic already converges legacy schemas to the current structure, while exact historical reconstruction would require reverse-engineering old releases and create unnecessary failure modes.

Phase 2 establishes a reliable baseline now and makes future evolution explicitly versioned.

### 22.2 Use only `PRAGMA user_version`

Rejected because it stores only one integer and provides no migration names/history for diagnostics or audit.

The explicit ledger matches the master architecture and gives clearer startup failures.

### 22.3 Keep feature `ensure_*_schema()` calls as a fallback

Rejected because that preserves two schema ownership paths and defeats the central Phase 2 invariant.

Startup migration is mandatory. If startup migration fails, the application must fail rather than silently create partial schema later from a feature request.

### 22.4 Move transaction/repository ownership in the same phase

Rejected because schema versioning and application transaction ownership are separate risk domains.

Phase 3 will address read-side writes/commits, repository boundaries, and transaction ownership after schema evolution is deterministic.

### 22.5 Remove scheduler/sync compatibility overrides now

Rejected because those overrides also contain reliability behavior beyond schema ownership.

Phase 2 removes structural DDL from them only. Full compatibility-layer replacement belongs to later roadmap phases.

## 23. Relationship to Phase 3

Phase 2 creates a stable structural foundation for Phase 3.

After Phase 2:

- schema exists before application services run
- repositories can assume stable tables/columns
- getters no longer need feature schema guards
- schema migrations have one owner
- future repository extraction does not need to carry DDL behavior

Phase 3 can then focus narrowly on:

- repository boundaries
- explicit transaction ownership
- removing implicit commits from reads
- separating SQL persistence from use-case orchestration

without simultaneously changing how the database schema is created.

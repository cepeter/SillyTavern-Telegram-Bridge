# DB/RAG Pre-production Baseline Reset Design

Date: 2026-09-22  
Branch: `refactor/db-rag-baseline-reset`  
Base: `966c31bb02dd12bbb974c534e987c9399209cdd6`  
Status: design approved in chat; written spec awaiting review

## Intent

Reset the bridge's SQLite and RAG persistence baseline before production so the repository carries only the current schema contract, not compatibility code for earlier development revisions.

The bridge is still pre-production. Existing databases created by earlier repository revisions are intentionally unsupported after this change. Operators must archive or delete an old development database and allow the bridge to create a fresh database.

Success means:

- a fresh database is created directly in the complete current schema;
- production schema setup contains no old-column detection or upgrade-time `ALTER TABLE` logic;
- the migration ledger contains one baseline migration named `initial_schema`;
- the generic transactional migration engine remains available for future schema evolution;
- RAG embeddings are created only in the current namespace/signature/norm format;
- lazy legacy RAG signature backfill and unsigned-row compatibility fallback are removed;
- recurring startup cleanup and normal RAG behavior remain unchanged;
- exact-head tests and CI validate the new persistence contract.

## Context

Current `bridge/schema.py` declares four production migrations:

1. `core_baseline`
2. `scene_state`
3. `director_goals`
4. `sync_lifecycle_trigger`

The baseline migration itself still behaves as an upgrader. It inspects existing tables through `PRAGMA table_info` and conditionally issues `ALTER TABLE` statements for columns introduced during pre-production development. Similar compatibility logic remains for RAG document versioning, embedding metadata, failed turns, panels, Sync state, and group state.

Current RAG retrieval also supports rows created before `vector_signature` existed. `backfill_rag_embedding_signatures()` lazily mutates such rows during retrieval, and `semantic_candidate_chunk_ids()` retains deterministic positional sampling as a compatibility fallback while unsigned rows remain.

Those paths preserve historical development databases, which conflicts with the repository's current pre-production policy: remove obsolete compatibility instead of carrying it forward.

## Scope

### In scope

- `bridge/schema.py`
  - collapse the four production migrations into one current baseline;
  - create every current table, index, virtual table, and trigger directly in final form;
  - remove all compatibility-time schema introspection and conditional structural mutation;
  - keep recurring startup cleanup separate from structural creation.
- `bridge/rag_core.py`
  - remove lazy embedding-signature backfill;
  - remove retrieval-time calls to that backfill;
  - preserve current embedding generation, cache, retrieval, versioning, and reindex behavior.
- `bridge/rag_retrieval.py`
  - remove the unsigned-row positional compatibility fallback;
  - make signature-based shortlist selection the only large-corpus semantic fallback after lexical neighborhoods;
  - remove parameters/imports/constants that exist only for the legacy sampling path.
- migration/schema/RAG tests
  - replace old-schema upgrade assertions with fresh-baseline invariants;
  - preserve generic migration-engine tests;
  - add source-level regression guards preventing the removed compatibility mechanisms from returning.
- documentation that directly describes database compatibility, if needed to make the operational reset explicit.

### Out of scope

- Live Sync `phase3_*` naming cleanup;
- test-file naming cleanup;
- removal of historical Superpowers planning/spec documents;
- `bridge.main` decomposition;
- database connection tuning, WAL behavior, vacuum policy, or maintenance scheduling;
- changes to RAG ranking algorithms beyond removal of legacy fallback behavior;
- changes to embedding providers, model selection, dimensions configuration, or Data Bank user-facing features;
- migration or conversion utilities for existing pre-production databases.

These are separate follow-up PRs.

## Architectural Decision

### Keep the migration engine, reset the production migration history

`bridge/migrations.py` remains the canonical generic migration runner. Its transactionality, declaration validation, ledger validation, rollback semantics, and future-version protection remain useful once the new baseline is established.

`bridge/schema.py` will expose exactly one production migration:

```python
SCHEMA_MIGRATIONS = (
    _Migration(1, "initial_schema", _create_initial_schema),
)
```

`_create_initial_schema()` creates the complete schema expected by current production code in one transaction.

This distinguishes two concerns:

- **migration mechanism:** retained for future production evolution;
- **pre-production migration history:** reset because compatibility with those revisions is not required.

Future schema changes after this reset should be represented by migration 2, migration 3, and so on. The baseline itself must not accumulate compatibility probes.

## Fresh Database Contract

A fresh database created by the bridge must receive all current persistence objects directly.

The baseline includes the current forms of:

- `meta`
- `messages`
- `sessions`
- `response_variants`
- `generation_settings`
- `generation_presets`
- `session_summaries`
- `hindsight_documents`
- `data_bank_documents`
- `data_bank_chunks`
- `data_bank_embeddings`
- `rag_embedding_cache`
- `processed_updates`
- `failed_turns`
- `jobs`
- `callback_tokens`
- `panel_sessions`
- `operations`
- `sync_bindings`
- `data_bank_fts`
- `group_sessions`
- `scene_states`
- `director_goals`

It also creates every current index and lifecycle trigger, including the session-delete cleanup triggers for Scene State, Director Goals, and Sync bindings.

Helper names should reflect creation rather than upgrade semantics. For example, `_ensure_*_tables` may become `_create_*_tables`, with a single `_create_initial_schema` coordinating them.

## Removed Schema Compatibility

Production schema creation must not:

- query `PRAGMA table_info` to discover old columns;
- execute `ALTER TABLE` to repair old tables;
- assign document versions by scanning historical pre-versioning rows;
- inject defaults specifically to reinterpret old data;
- conditionally add old missing columns to messages, sessions, variants, embeddings, failed turns, panels, Sync bindings, or groups.

The final column definitions belong directly in each `CREATE TABLE` statement.

`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, and `CREATE TRIGGER IF NOT EXISTS` may remain. They provide safe idempotence for the declared baseline and are not a promise to repair arbitrarily old schemas.

## Unsupported Existing Databases

Existing pre-production databases are deliberately outside the compatibility contract.

A database containing the old migration ledger will fail migration-history validation because version 1 was previously named `core_baseline` and the new baseline is named `initial_schema`. That fail-fast behavior is desirable: it prevents the bridge from silently treating an old schema as current.

The implementation will not add a special converter, alias the old migration name, or preserve migrations 2-4 solely to make old databases start.

Operational guidance is:

1. stop the bridge;
2. archive the old SQLite file if its data is needed for inspection;
3. remove/rename the active development database;
4. restart the bridge and let it create the new baseline.

No automated destructive deletion is introduced.

## RAG Persistence Contract

### Embedding rows

New `data_bank_embeddings` rows are current-format rows. The schema should require the fields production writes today:

- `chunk_id`
- `embedding_namespace`
- `dimensions`
- `vector_json`
- `vector_signature`
- `vector_norm`

`embedding_namespace` must not use a `legacy` default. `vector_signature` and `vector_norm` are required rather than nullable compatibility fields.

Current production writes already serialize all three metadata values through `_embedding_row(namespace, vector)`, both when adding documents and when reindexing.

### Embedding cache

`rag_embedding_cache.vector_norm` is created as a required field. Current cache writes already provide the norm.

### Document versioning

`data_bank_documents` is created directly with `version_number` and `active`. The baseline no longer scans old rows to synthesize version history.

Current document-add behavior still increments the latest version for a filename and marks prior versions inactive. That feature remains unchanged.

## RAG Retrieval Contract

For small corpora, `semantic_candidate_chunk_ids()` continues to return the exact candidate set when it fits within the configured bound.

For large corpora:

1. lexical FTS hits seed local chunk neighborhoods;
2. the current query embedding signature ranks remaining signed embeddings by signature distance;
3. the bounded shortlist is returned;
4. full vector decoding/cosine scoring remains limited to that shortlist.

The positional sampling fallback for unsigned rows is removed because unsigned production rows no longer exist under the new schema contract.

As a result:

- `backfill_rag_embedding_signatures()` is deleted;
- retrieval no longer performs writes merely because a query is executed;
- `sample_windows` and any constants/imports used only by the legacy sampling fallback are removed;
- SQL no longer filters nullable signatures as a compatibility condition when the schema guarantees a signature.

This preserves the current bounded retrieval architecture while eliminating compatibility-driven mutation and branching.

## Startup Cleanup

`_run_startup_database_cleanup()` remains separate and unchanged in purpose.

It continues to remove expired or stale runtime data such as:

- old processed update records;
- expired RAG embedding-cache entries;
- completed/failed jobs past retention;
- old failed turns;
- expired callback tokens;
- expired panel sessions;
- old operation records;
- orphaned Sync bindings.

These are operational retention rules, not schema migrations, and must not be removed by the baseline reset.

## Error Handling and Failure Semantics

Fresh-schema creation remains transactional through `run_migrations()`. If any structural statement fails:

- the migration is rolled back;
- version 1 is not recorded as applied;
- startup fails rather than continuing with a partial baseline.

The existing generic migration-history validation remains authoritative.

No catch-and-continue path should convert an incompatible old database into a partially accepted state.

RAG embedding failures keep their existing behavior. The reset does not change provider/network error handling.

## Testing Strategy

### Generic migration engine

Keep `tests/test_migrations.py` coverage for the reusable migration engine, including:

- ordered execution;
- rollback on migration failure;
- declaration validation;
- migration-name drift rejection;
- invalid history/gap rejection;
- unknown future version rejection;
- transaction behavior.

Those tests describe infrastructure that remains supported.

### Production schema baseline

Replace old-production-schema upgrade tests with fresh-baseline tests that assert:

- `SCHEMA_MIGRATIONS` has exactly one migration;
- its version/name are `1` / `initial_schema`;
- a fresh database records exactly that ledger row;
- every required table/index/trigger exists;
- critical current columns and constraints are present;
- repeated initialization does not re-run structural DDL after the database path has been marked ready;
- startup cleanup still runs as expected.

Add a source-level regression guard asserting production schema setup contains no:

- `ALTER TABLE`;
- `PRAGMA table_info`;
- old migration function names such as `_migration_002_scene_state`, `_migration_003_director_goals`, or `_migration_004_sync_lifecycle_trigger`.

### RAG tests

Retain current tests for:

- exact small-corpus candidates;
- bounded large-corpus candidates;
- lexical-neighborhood inclusion;
- signature shortlist finding a non-lexical semantic target;
- bounded vector decoding;
- embedding batching outside long write transactions;
- reindex batching and transaction release.

Remove the legacy backfill test.

Replace it with invariants showing:

- production embedding creation stores non-null namespace/signature/norm;
- reindex writes the same current metadata;
- retrieval does not invoke any lazy data-upgrade operation;
- source no longer exposes `backfill_rag_embedding_signatures` or legacy positional fallback language/code.

### Full validation

The implementation PR must pass the repository's optimized CI workflow on its exact head:

- locked dependency installation;
- development test tooling;
- `pip check`;
- Python compilation;
- complete pytest suite;
- parallel `pip-audit`.

## TDD Execution Shape

Implementation should proceed through focused RED -> GREEN slices:

1. add baseline-shape tests that fail against the current four-migration/upgrade schema;
2. collapse schema creation to `initial_schema` and make those tests green;
3. add RAG current-format tests/guards that fail while backfill and fallback remain;
4. remove RAG legacy paths and make focused tests green;
5. run schema/RAG focused suites;
6. run full exact-head CI;
7. perform whole-branch review before marking the PR ready.

No compatibility shim should be introduced merely to make old tests pass. Tests that assert intentionally retired behavior must be rewritten or deleted.

## Documentation

README/database setup guidance should explicitly state that this pre-production reset does not migrate older development databases and that an old SQLite file must be archived/removed before using this revision.

Documentation must not imply that the bridge automatically upgrades arbitrary earlier development schemas.

## Risks and Mitigations

### Risk: accidental loss of a development database

Mitigation: the code does not delete databases. It fails on known old migration history and documentation instructs operators to archive before removing an old file.

### Risk: final schema accidentally omits an object previously added by migrations 2-4

Mitigation: fresh-baseline tests enumerate current tables, indexes, and triggers, and focused review compares the new baseline against the current post-migration schema.

### Risk: a RAG write path still creates unsigned or norm-less rows

Mitigation: schema `NOT NULL` constraints make such writes fail immediately, and tests cover both document-add and reindex paths.

### Risk: removing positional fallback reduces retrieval coverage

Mitigation: the fallback exists only for unsigned legacy rows. Under the new schema, every production embedding has a signature, so the signature shortlist is the intended current behavior.

### Risk: migration framework is removed prematurely

Mitigation: retain `bridge/migrations.py` and its tests. Only pre-production migration history is reset.

## Acceptance Criteria

The DB/RAG baseline-reset PR is complete only when all of the following are true:

1. fork branch is based on the latest merged upstream `main`;
2. `SCHEMA_MIGRATIONS` contains exactly `Migration(1, "initial_schema", ...)`;
3. fresh initialization creates the complete current application schema;
4. production schema code contains no `ALTER TABLE` or `PRAGMA table_info`;
5. old migrations 2-4 and their compatibility responsibilities are gone;
6. old pre-production databases are explicitly unsupported with no migration shim;
7. `data_bank_embeddings` requires current namespace/signature/norm metadata;
8. RAG lazy signature backfill is removed;
9. unsigned-row positional retrieval fallback is removed;
10. startup cleanup remains intact;
11. generic migration-engine behavior remains tested;
12. focused schema/RAG tests pass;
13. full exact-head CI passes;
14. whole-branch review finds no unresolved Critical or Important issues;
15. the PR remains unmerged until the user explicitly decides to merge it.

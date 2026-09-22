# DB/RAG Pre-production Baseline Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bridge's pre-production SQLite upgrade history with one current `initial_schema` baseline and remove legacy RAG embedding/backfill compatibility without changing current runtime behavior.

**Architecture:** Keep `bridge/migrations.py` as the reusable transactional migration engine, but make `bridge/schema.py` declare only migration 1, `initial_schema`, which directly creates every current table/index/trigger. Tighten the RAG schema to require current embedding metadata and remove retrieval-time legacy mutation/sampling paths. Existing pre-production databases are not migrated; fresh databases are the supported persistence contract.

**Tech Stack:** Python 3.11, stdlib `sqlite3`, `unittest` tests collected by pytest, GitHub Actions CI, existing bridge RAG/SQLite modules.

**Spec:** `docs/superpowers/specs/2026-09-22-db-rag-preproduction-baseline-reset-design.md`

## Global Constraints

- Repository is pre-production; backward compatibility with earlier development SQLite schemas is intentionally not required.
- Existing old development databases must be archived/removed and recreated; do not add conversion code, aliases, compatibility shims, or automatic destructive deletion.
- Preserve `bridge/migrations.py` and its generic transaction/history validation behavior for future post-baseline migrations.
- Production schema setup must contain no `ALTER TABLE` and no `PRAGMA table_info` compatibility probes.
- Production migration history after this reset is exactly `Migration(1, "initial_schema", ...)`.
- `data_bank_embeddings.embedding_namespace`, `vector_signature`, and `vector_norm` are current-format required fields; do not restore a `legacy` namespace default.
- Preserve Data Bank versioning, embedding batching, query cache, bounded semantic retrieval, startup cleanup, WAL/connection tuning, and database maintenance behavior.
- Do not include Live Sync `phase3_*` renames, test-file naming cleanup, historical Superpowers-doc cleanup, or `bridge.main` decomposition in this PR.
- Do not cherry-pick or merge `coderabbit/changes/a2b23dc7`; port only the approved behavior onto current `main`.
- Work only on `punzer4-code:refactor/db-rag-baseline-reset`, based on upstream merge SHA `966c31bb02dd12bbb974c534e987c9399209cdd6`.
- Keep the PR unmerged until the user explicitly decides to merge it.

## Review Focus

1. **Old ledger with `core_baseline`:** startup must reject the historical migration name before applying `initial_schema`; the test in Task 1 pins the existing migration-history mismatch behavior.
2. **Repeated startup on a current database:** structural DDL must not rerun after the baseline has been recorded, while recurring cleanup still runs; Task 1 preserves both regression checks.
3. **Incomplete RAG writes:** schema constraints must reject missing namespace/signature/norm metadata instead of creating rows that require later repair; Task 1 adds the constraint test and Task 2 verifies current write paths satisfy it.
4. **Large signed corpora without lexical overlap:** signature shortlisting must still find a non-lexical semantic target and remain bounded after positional fallback removal; Task 2 retains and strengthens that test.
5. **Embedding/reindex network work vs SQLite transactions:** removing backfill must not accidentally move embedding calls into write transactions; Task 2 retains both transaction-boundary tests and validates stored metadata afterward.

---

### Task 1: Collapse Production SQLite History to One Current Baseline

**Files:**
- Modify: `tests/test_migrations.py:201-611`
- Modify: `tests/test_database_optimization.py:118-145`
- Modify: `bridge/schema.py:12-456`
- Preserve: `bridge/migrations.py`

**Interfaces:**
- Consumes: `bridge.migrations.Migration(version: int, name: str, apply: Callable[[sqlite3.Connection], None])` and `run_migrations(db, migrations) -> None`.
- Produces: `bridge.schema.SCHEMA_MIGRATIONS == (_Migration(1, "initial_schema", _create_initial_schema),)`.
- Produces: `initialize_database_schema(db: sqlite3.Connection) -> None` with the same public signature and cleanup behavior.
- Produces: final RAG tables whose required metadata contract is consumed by Task 2.
- Does not modify the generic migration runner.

- [ ] **Step 1: Replace legacy-upgrade tests with fresh-baseline RED tests**

In `tests/test_migrations.py`, keep `MigrationEngineTests` unchanged. In `ApplicationSchemaMigrationTests`, delete `_create_representative_legacy_schema()` and the tests whose purpose is to preserve/adopt historical application schemas:

```text
test_core_baseline_upgrades_representative_legacy_schema
test_existing_scene_and_director_rows_survive_ledger_adoption
test_current_schema_without_ledger_adopts_all_versions_without_data_loss
```

Rename the fresh bootstrap test and add these assertions:

```python
def test_initial_schema_is_single_current_baseline(self):
    self.assertEqual(len(schema.SCHEMA_MIGRATIONS), 1)
    migration = schema.SCHEMA_MIGRATIONS[0]
    self.assertEqual((migration.version, migration.name), (1, "initial_schema"))


def test_initial_schema_bootstraps_current_database(self):
    schema.initialize_database_schema(self.db)

    self.assertEqual(
        self.db.execute(
            "SELECT version,name FROM schema_migrations ORDER BY version"
        ).fetchall(),
        [(1, "initial_schema")],
    )

    for table in (
        "meta",
        "messages",
        "sessions",
        "response_variants",
        "generation_settings",
        "generation_presets",
        "session_summaries",
        "hindsight_documents",
        "data_bank_documents",
        "data_bank_chunks",
        "data_bank_embeddings",
        "rag_embedding_cache",
        "processed_updates",
        "failed_turns",
        "jobs",
        "callback_tokens",
        "panel_sessions",
        "operations",
        "sync_bindings",
        "data_bank_fts",
        "group_sessions",
        "scene_states",
        "director_goals",
    ):
        with self.subTest(table=table):
            self.assertIsNotNone(
                self.db.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type IN ('table','view') AND name=?",
                    (table,),
                ).fetchone()
            )

    for object_type, name in (
        ("index", "scene_states_updated_idx"),
        ("trigger", "scene_states_session_delete"),
        ("trigger", "director_goals_session_delete"),
        ("trigger", "sessions_delete_sync_binding"),
    ):
        with self.subTest(name=name):
            self.assertIsNotNone(
                self.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type=? AND name=?",
                    (object_type, name),
                ).fetchone()
            )
```

Add a strict RAG-column assertion:

```python
def test_initial_schema_requires_current_rag_embedding_metadata(self):
    schema.initialize_database_schema(self.db)

    embedding_columns = {
        row[1]: row
        for row in self.db.execute(
            "PRAGMA table_info(data_bank_embeddings)"
        ).fetchall()
    }
    self.assertEqual(embedding_columns["embedding_namespace"][3], 1)
    self.assertIsNone(embedding_columns["embedding_namespace"][4])
    self.assertEqual(embedding_columns["vector_signature"][3], 1)
    self.assertEqual(embedding_columns["vector_norm"][3], 1)

    cache_columns = {
        row[1]: row
        for row in self.db.execute(
            "PRAGMA table_info(rag_embedding_cache)"
        ).fetchall()
    }
    self.assertEqual(cache_columns["vector_norm"][3], 1)
```

The `PRAGMA table_info` use here is test-only inspection; the production ban applies to `bridge/schema.py`.

Add the source regression guard:

```python
def test_production_schema_has_no_preproduction_upgrade_paths(self):
    source = (
        Path(__file__).parents[1] / "bridge" / "schema.py"
    ).read_text(encoding="utf-8")

    self.assertNotIn("ALTER TABLE", source)
    self.assertNotIn("PRAGMA table_info", source)
    self.assertNotIn("_migration_002_scene_state", source)
    self.assertNotIn("_migration_003_director_goals", source)
    self.assertNotIn("_migration_004_sync_lifecycle_trigger", source)
    self.assertNotIn('"core_baseline"', source)
```

Add an old-ledger rejection test:

```python
def test_preproduction_core_baseline_ledger_is_rejected(self):
    self.db.execute(
        "CREATE TABLE schema_migrations("
        "version INTEGER PRIMARY KEY,"
        "name TEXT NOT NULL,"
        "applied_at REAL NOT NULL)"
    )
    self.db.execute(
        "INSERT INTO schema_migrations VALUES(1,'core_baseline',1.0)"
    )
    self.db.execute(
        "CREATE TABLE sentinel(value TEXT NOT NULL)"
    )
    self.db.execute(
        "INSERT INTO sentinel(value) VALUES('preserve-me')"
    )
    self.db.commit()

    with self.assertRaisesRegex(MigrationError, "name mismatch"):
        schema.initialize_database_schema(self.db)

    self.assertEqual(
        self.db.execute("SELECT value FROM sentinel").fetchone()[0],
        "preserve-me",
    )
    self.assertEqual(
        self.db.execute(
            "SELECT version,name FROM schema_migrations ORDER BY version"
        ).fetchall(),
        [(1, "core_baseline")],
    )
```

Rename `test_startup_cleanup_runs_when_core_migration_is_already_applied` to `test_startup_cleanup_runs_when_initial_schema_is_already_applied`; keep its behavioral assertions.

Keep `RequestTimeSchemaRegressionTests` unchanged.

- [ ] **Step 2: Update the database connection-gate expectation**

In `tests/test_database_optimization.py::test_db_connect_runs_database_wide_schema_setup_once_per_process`, change only the migration count:

```python
self.assertEqual(
    second.execute(
        "SELECT COUNT(*) FROM schema_migrations"
    ).fetchone()[0],
    1,
)
```

Keep the trace assertion that no structural DDL runs on the lightweight second connection.

- [ ] **Step 3: Run the focused schema tests and verify RED**

Run:

```bash
python -m pytest -q   tests/test_migrations.py   tests/test_database_optimization.py
```

Expected before implementation:

- `test_initial_schema_is_single_current_baseline` fails because four migrations are declared;
- `test_initial_schema_requires_current_rag_embedding_metadata` fails because signature/norm fields are nullable and namespace still has the legacy default;
- `test_production_schema_has_no_preproduction_upgrade_paths` fails because `ALTER TABLE`, `PRAGMA table_info`, and old migration functions remain;
- the database optimization count assertion fails because the current ledger contains four application migrations.

- [ ] **Step 4: Commit the RED test contract**

```bash
git add tests/test_migrations.py tests/test_database_optimization.py
git commit -m "test: pin fresh SQLite baseline contract"
```

If this harness cannot run local tests, open/update the Draft PR after this commit so upstream CI provides authoritative RED evidence on the exact SHA. Record the failing test names in the plan execution ledger before implementation.

- [ ] **Step 5: Convert schema helper functions from upgrade semantics to creation semantics**

In `bridge/schema.py` rename:

```python
_ensure_core_tables       -> _create_core_tables
_ensure_generation_tables -> _create_generation_tables
_ensure_rag_tables        -> _create_rag_tables
_ensure_job_tables        -> _create_job_tables
_ensure_panel_tables      -> _create_application_tables
```

Keep the current `CREATE TABLE` / `CREATE INDEX` statements, but move every current column into its table's direct declaration and delete every post-create compatibility inspection/mutation block.

The final `messages` definition must include the previously upgraded column directly:

```python
db.execute("""CREATE TABLE IF NOT EXISTS messages (
    chat_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT 'default',
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    telegram_message_id TEXT,
    telegram_message_ids TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
)""")
```

Keep these current columns directly in their existing table definitions and remove their old conditional-add blocks:

```text
sessions.author_note
sessions.system_prompt
sessions.response_language
response_variants.user_rowid
data_bank_documents.version_number
data_bank_documents.active
failed_turns.session_id
panel_sessions.owner_user_id
sync_bindings.conflict
sync_bindings.last_error
sync_bindings.last_checked_at
sync_bindings.realtime_enabled
sync_bindings.realtime_failures
sync_bindings.realtime_next_retry_at
group_sessions.mode
group_sessions.forced_speaker
group_sessions.turn_user_id
group_sessions.turn_users_json
```

Delete the RAG document-versioning upgrade loop; new databases already start with `version_number` and `active`.

- [ ] **Step 6: Make the RAG table definitions current-format only**

Replace the embedding/cache declarations in `_create_rag_tables()` with:

```python
db.execute("""CREATE TABLE IF NOT EXISTS data_bank_embeddings (
    chunk_id INTEGER PRIMARY KEY,
    embedding_namespace TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector_json TEXT NOT NULL,
    vector_signature INTEGER NOT NULL,
    vector_norm REAL NOT NULL
)""")

db.execute(
    "CREATE INDEX IF NOT EXISTS "
    "data_bank_embeddings_namespace_chunk_idx "
    "ON data_bank_embeddings(embedding_namespace, chunk_id)"
)
db.execute(
    "CREATE INDEX IF NOT EXISTS "
    "data_bank_embeddings_namespace_signature_idx "
    "ON data_bank_embeddings("
    "embedding_namespace, vector_signature, chunk_id)"
)

db.execute("""CREATE TABLE IF NOT EXISTS rag_embedding_cache (
    cache_key TEXT PRIMARY KEY,
    dimensions INTEGER NOT NULL,
    vector_json TEXT NOT NULL,
    vector_norm REAL NOT NULL,
    created_at REAL NOT NULL
)""")
```

Do not retain `DEFAULT 'legacy'`, nullable signature/norm fields, or conditional `ALTER TABLE` logic.

- [ ] **Step 7: Fold Scene State, Director Goals, and Sync lifecycle objects into the baseline**

Delete:

```text
_migration_001_core_baseline
_migration_002_scene_state
_migration_003_director_goals
_migration_004_sync_lifecycle_trigger
```

Create one coordinator:

```python
def _create_initial_schema(db: sqlite3.Connection) -> None:
    _create_core_tables(db)
    _create_generation_tables(db)
    _create_rag_tables(db)
    _create_job_tables(db)
    _create_application_tables(db)

    db.execute(
        """CREATE TABLE IF NOT EXISTS scene_states (
            chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            state_json TEXT NOT NULL DEFAULT '{}',
            updated_through_rowid INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL,
            PRIMARY KEY(chat_id, session_id)
        )"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS scene_states_updated_idx "
        "ON scene_states(chat_id, session_id, updated_through_rowid)"
    )
    db.execute(
        """CREATE TRIGGER IF NOT EXISTS scene_states_session_delete
        AFTER DELETE ON sessions
        BEGIN
            DELETE FROM scene_states
            WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        END"""
    )

    db.execute(
        """CREATE TABLE IF NOT EXISTS director_goals (
            chat_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            goal TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(chat_id, session_id)
        )"""
    )
    db.execute(
        """CREATE TRIGGER IF NOT EXISTS director_goals_session_delete
        AFTER DELETE ON sessions
        BEGIN
            DELETE FROM director_goals
            WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        END"""
    )

    db.execute(
        """CREATE TRIGGER IF NOT EXISTS sessions_delete_sync_binding
        AFTER DELETE ON sessions
        FOR EACH ROW
        BEGIN
            DELETE FROM sync_bindings
            WHERE chat_id=OLD.chat_id AND session_id=OLD.session_id;
        END"""
    )
```

Declare only:

```python
SCHEMA_MIGRATIONS = (
    _Migration(1, "initial_schema", _create_initial_schema),
)
```

Keep:

```python
def initialize_database_schema(db: sqlite3.Connection) -> None:
    """Apply structural migrations, then run recurring startup cleanup."""
    _run_migrations(db, SCHEMA_MIGRATIONS)
    _run_startup_database_cleanup(db)
    db.commit()
```

Do not modify `_run_startup_database_cleanup()`.

- [ ] **Step 8: Run focused schema tests and verify GREEN**

Run:

```bash
python -m pytest -q   tests/test_migrations.py   tests/test_database_optimization.py
```

Expected: all tests pass.

Also run the production source guard directly:

```bash
python - <<'PY'
from pathlib import Path
import bridge.schema as schema

source = Path("bridge/schema.py").read_text(encoding="utf-8")
assert "ALTER TABLE" not in source
assert "PRAGMA table_info" not in source
assert [(m.version, m.name) for m in schema.SCHEMA_MIGRATIONS] == [
    (1, "initial_schema")
]
print("SQLite baseline reset verified")
PY
```

Expected:

```text
SQLite baseline reset verified
```

- [ ] **Step 9: Commit the schema reset**

```bash
git add bridge/schema.py tests/test_migrations.py tests/test_database_optimization.py
git commit -m "refactor: reset SQLite schema baseline"
```

---

### Task 2: Remove Legacy RAG Backfill and Positional Fallback

**Files:**
- Modify: `tests/test_rag_scaling.py:17-246`
- Modify: `bridge/rag_core.py:203-425`
- Modify: `bridge/rag_retrieval.py:1-220`
- Modify: `bridge/rag.py:8-41`

**Interfaces:**
- Consumes: Task 1's strict `data_bank_embeddings` schema.
- Preserves: `_embedding_row(namespace: str, vector: list[float]) -> tuple` returning `(namespace, dimensions, vector_json, signature, norm)`.
- Preserves: `semantic_candidate_chunk_ids(db, chat_id, embedding_namespace, lexical_chunk_ids, *, query_signature=None, candidate_limit=..., neighbor_radius=2) -> tuple[int, ...]`.
- Removes: `backfill_rag_embedding_signatures(...)`.
- Removes: `sample_windows` from `semantic_candidate_chunk_ids`.
- Removes: `DEFAULT_SEMANTIC_SAMPLE_WINDOWS`.
- Preserves current Data Bank ingestion/reindex behavior and bounded shortlist semantics.

- [ ] **Step 1: Rewrite RAG fixtures so tests only create current-format embeddings**

In `tests/test_rag_scaling.py`, remove `with_signatures` from `_insert_chunks()` and always insert current metadata:

```python
def _insert_chunks(
    self,
    count: int,
    needle_index: int | None = None,
    semantic_target_index: int | None = None,
):
    now = time.time()
    document_id = "doc-large"
    self.db.execute(
        "INSERT INTO data_bank_documents("
        "chat_id,document_id,filename,byte_size,chunk_count,"
        "created_at,updated_at"
        ") VALUES(?,?,?,?,?,?,?)",
        ("chat", document_id, "large.txt", count, count, now, now),
    )
    ids = []
    for index in range(count):
        content = f"chunk {index}"
        if index == needle_index:
            content += " needle"
        cursor = self.db.execute(
            "INSERT INTO data_bank_chunks("
            "chat_id,document_id,chunk_index,content"
            ") VALUES(?,?,?,?)",
            ("chat", document_id, index, content),
        )
        chunk_id = int(cursor.lastrowid)
        ids.append(chunk_id)

        vector = [1.0, 0.0]
        if semantic_target_index is not None and index != semantic_target_index:
            vector = [-1.0, 0.0]

        self.db.execute(
            "INSERT INTO data_bank_embeddings("
            "chunk_id,embedding_namespace,dimensions,vector_json,"
            "vector_signature,vector_norm"
            ") VALUES(?,?,?,?,?,?)",
            (
                chunk_id,
                self.namespace,
                2,
                json.dumps(vector),
                _m_rag.embedding_signature(vector),
                _m_rag.embedding_norm(vector),
            ),
        )
        self.db.execute(
            "INSERT INTO data_bank_fts("
            "content,chat_id,document_id,filename,chunk_id"
            ") VALUES(?,?,?,?,?)",
            (content, "chat", document_id, "large.txt", chunk_id),
        )
    self.db.commit()
    return ids
```

Delete `test_legacy_embedding_signatures_are_backfilled_lazily`.

- [ ] **Step 2: Add RED invariants for current metadata and retired compatibility APIs**

Add:

```python
def test_current_embedding_schema_rejects_missing_signature_and_norm(self):
    now = time.time()
    self.db.execute(
        "INSERT INTO data_bank_documents("
        "chat_id,document_id,filename,byte_size,chunk_count,"
        "created_at,updated_at"
        ") VALUES(?,?,?,?,?,?,?)",
        ("chat", "strict-doc", "strict.txt", 1, 1, now, now),
    )
    chunk_id = self.db.execute(
        "INSERT INTO data_bank_chunks("
        "chat_id,document_id,chunk_index,content"
        ") VALUES(?,?,?,?)",
        ("chat", "strict-doc", 0, "strict"),
    ).lastrowid

    with self.assertRaises(sqlite3.IntegrityError):
        self.db.execute(
            "INSERT INTO data_bank_embeddings("
            "chunk_id,embedding_namespace,dimensions,vector_json"
            ") VALUES(?,?,?,?)",
            (chunk_id, self.namespace, 2, "[1.0,0.0]"),
        )


def test_rag_sources_have_no_legacy_backfill_or_sampling_fallback(self):
    root = Path(__file__).parents[1] / "bridge"
    core = (root / "rag_core.py").read_text(encoding="utf-8")
    retrieval = (root / "rag_retrieval.py").read_text(encoding="utf-8")
    shell = (root / "rag.py").read_text(encoding="utf-8")

    self.assertNotIn("backfill_rag_embedding_signatures", core)
    self.assertNotIn("backfill_rag_embedding_signatures", shell)
    self.assertNotIn("DEFAULT_SEMANTIC_SAMPLE_WINDOWS", retrieval)
    self.assertNotIn("sample_windows", retrieval)
    self.assertNotIn("Compatibility fallback", retrieval)
```

Add `import sqlite3` to the test module if it is not already present.

Extend `test_add_document_embedding_batches_run_outside_write_transaction` after the existing row-count assertion:

```python
rows = self.db.execute(
    "SELECT embedding_namespace,vector_signature,vector_norm "
    "FROM data_bank_embeddings"
).fetchall()
self.assertEqual(len(rows), 65)
self.assertTrue(
    all(
        namespace == self.namespace
        and signature is not None
        and norm is not None
        for namespace, signature, norm in rows
    )
)
```

Extend `test_reindex_embedding_batches_release_write_transaction_between_calls`:

```python
rows = self.db.execute(
    "SELECT embedding_namespace,vector_signature,vector_norm "
    "FROM data_bank_embeddings"
).fetchall()
self.assertEqual(len(rows), 65)
self.assertTrue(
    all(
        namespace == self.namespace
        and signature is not None
        and norm is not None
        for namespace, signature, norm in rows
    )
)
```

- [ ] **Step 3: Run the focused RAG suite and verify RED**

Run:

```bash
python -m pytest -q tests/test_rag_scaling.py
```

Expected before Task 2 implementation:

- source-retirement guard fails because backfill and sampling symbols still exist;
- strict-schema test is already enabled by Task 1 and passes, serving as characterization of the new persistence contract;
- existing retrieval/batching tests continue to pass.

The intentional RED is the compatibility-source guard.

- [ ] **Step 4: Commit the RAG RED contract**

```bash
git add tests/test_rag_scaling.py
git commit -m "test: pin current RAG embedding contract"
```

- [ ] **Step 5: Delete lazy signature backfill from the RAG core**

Delete `backfill_rag_embedding_signatures()` entirely from `bridge/rag_core.py`.

In `retrieve_data_bank()`, change:

```python
namespace = rag_embedding_namespace()
backfill_rag_embedding_signatures(db, chat_id, namespace)
semantic_ids = semantic_candidate_chunk_ids(
```

to:

```python
namespace = rag_embedding_namespace()
semantic_ids = semantic_candidate_chunk_ids(
```

Do not alter `_embedding_row()`, `add_data_bank_document()`, `cached_rag_embedding()`, or `reindex_data_bank_documents()` except where formatting is mechanically required. Those paths already write namespace/signature/norm.

- [ ] **Step 6: Remove positional sampling compatibility from shortlist selection**

In `bridge/rag_retrieval.py`:

Delete:

```python
DEFAULT_SEMANTIC_SAMPLE_WINDOWS = 12
```

Change the function signature from:

```python
def semantic_candidate_chunk_ids(
    db: sqlite3.Connection,
    chat_id: str,
    embedding_namespace: str,
    lexical_chunk_ids: list[int] | tuple[int, ...],
    *,
    query_signature: int | None = None,
    candidate_limit: int = DEFAULT_SEMANTIC_CANDIDATE_LIMIT,
    neighbor_radius: int = 2,
    sample_windows: int = DEFAULT_SEMANTIC_SAMPLE_WINDOWS,
) -> tuple[int, ...]:
```

to:

```python
def semantic_candidate_chunk_ids(
    db: sqlite3.Connection,
    chat_id: str,
    embedding_namespace: str,
    lexical_chunk_ids: list[int] | tuple[int, ...],
    *,
    query_signature: int | None = None,
    candidate_limit: int = DEFAULT_SEMANTIC_CANDIDATE_LIMIT,
    neighbor_radius: int = 2,
) -> tuple[int, ...]:
```

Change the docstring to:

```python
"""Return a bounded semantic shortlist without decoding the whole corpus.

Small corpora remain exact. Larger corpora prioritize FTS-hit neighborhoods,
then globally rank compact embedding signatures by Hamming distance.
"""
```

Delete:

```python
sample_windows = max(1, min(int(sample_windows), 64))
```

In the signature-ranking query, remove the nullable-legacy filter:

```python
rows = db.execute(
    "SELECT e.chunk_id,e.vector_signature "
    "FROM data_bank_embeddings e "
    "JOIN data_bank_chunks c ON c.chunk_id=e.chunk_id "
    "JOIN data_bank_documents d "
    "ON d.chat_id=c.chat_id AND d.document_id=c.document_id "
    "WHERE c.chat_id=? AND d.active=1 AND e.embedding_namespace=?",
    (str(chat_id), str(embedding_namespace)),
)
```

After the signature-ranking block, return immediately:

```python
return tuple(selected)
```

Delete the entire old positional fallback beginning with the `Compatibility fallback` comment through the old final return.

Keep `import math` because `cosine_similarity()` still uses `math.sqrt` when NumPy is unavailable.

- [ ] **Step 7: Remove the obsolete public shell import**

In `bridge/rag.py`, remove only:

```python
backfill_rag_embedding_signatures,
```

from the `bridge.rag_core` import list.

Do not introduce an alias or compatibility wrapper.

- [ ] **Step 8: Run focused RAG and schema suites and verify GREEN**

Run:

```bash
python -m pytest -q   tests/test_rag_scaling.py   tests/test_migrations.py   tests/test_database_optimization.py
```

Expected: all tests pass.

Run the source retirement guard:

```bash
python - <<'PY'
from pathlib import Path

core = Path("bridge/rag_core.py").read_text(encoding="utf-8")
retrieval = Path("bridge/rag_retrieval.py").read_text(encoding="utf-8")
shell = Path("bridge/rag.py").read_text(encoding="utf-8")

assert "backfill_rag_embedding_signatures" not in core
assert "backfill_rag_embedding_signatures" not in shell
assert "DEFAULT_SEMANTIC_SAMPLE_WINDOWS" not in retrieval
assert "sample_windows" not in retrieval
assert "Compatibility fallback" not in retrieval
print("RAG legacy compatibility retired")
PY
```

Expected:

```text
RAG legacy compatibility retired
```

- [ ] **Step 9: Commit the RAG compatibility retirement**

```bash
git add   bridge/rag_core.py   bridge/rag_retrieval.py   bridge/rag.py   tests/test_rag_scaling.py
git commit -m "refactor: retire legacy RAG embedding compatibility"
```

---

### Task 3: Document the Pre-production Database Reset

**Files:**
- Modify: `README.md:757-780`

**Interfaces:**
- Consumes: Task 1's one-baseline database contract.
- Produces: operator guidance that old development SQLite files are unsupported and must be archived/recreated.
- Does not add runtime migration or deletion behavior.

- [ ] **Step 1: Add the operational reset notice**

Immediately after the `--check` explanation and before “Start the bridge”, add:

```markdown
### Pre-production database reset

SQLite now starts from one `initial_schema` migration containing the complete
current schema and constraints. Databases created by earlier pre-production
revisions are intentionally unsupported.

Before starting this revision with an older development database, stop the
bridge, archive the existing SQLite file if you need its data for inspection,
then remove or rename the active database and let the bridge create a fresh
one. The bridge does not automatically convert or delete an older database.
```

Do not document any conversion command because none exists.

- [ ] **Step 2: Check README wording against the implementation**

Run:

```bash
python - <<'PY'
from pathlib import Path

readme = Path("README.md").read_text(encoding="utf-8")
assert "one `initial_schema` migration" in readme
assert "intentionally unsupported" in readme
assert "does not automatically convert or delete" in readme
print("Database reset documentation verified")
PY
```

Expected:

```text
Database reset documentation verified
```

- [ ] **Step 3: Commit the documentation**

```bash
git add README.md
git commit -m "docs: document pre-production database reset"
```

---

### Task 4: Exact-head Verification, Whole-branch Review, and PR Readiness

**Files:**
- Modify: `docs/superpowers/plans/2026-09-22-db-rag-preproduction-baseline-reset.md` only to append execution evidence.
- No production changes unless verification finds a defect; defects return to the owning RED → GREEN task.

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: exact-head verification evidence and a reviewable Draft PR against upstream `main`.
- Does not merge the PR.

- [ ] **Step 1: Run compilation**

Run:

```bash
python -m compileall -q bridge tests sillytavern_telegram_bridge.py
```

Expected: exit 0.

- [ ] **Step 2: Run the complete pytest suite**

Run:

```bash
python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Validate installed dependencies**

Run:

```bash
python -m pip check
```

Expected:

```text
No broken requirements found.
```

- [ ] **Step 4: Audit locked dependencies**

Run:

```bash
python -m pip_audit -r requirements.lock
```

Expected: no known vulnerabilities.

If local `pip-audit` is unavailable, exact-head GitHub Actions dependency-audit is authoritative; do not install unrelated tooling solely for this check.

- [ ] **Step 5: Run architectural source guards**

Run:

```bash
python - <<'PY'
from pathlib import Path

import bridge.schema as schema

schema_source = Path("bridge/schema.py").read_text(encoding="utf-8")
rag_core = Path("bridge/rag_core.py").read_text(encoding="utf-8")
rag_retrieval = Path("bridge/rag_retrieval.py").read_text(encoding="utf-8")
rag_shell = Path("bridge/rag.py").read_text(encoding="utf-8")

assert [(m.version, m.name) for m in schema.SCHEMA_MIGRATIONS] == [
    (1, "initial_schema")
]
assert "ALTER TABLE" not in schema_source
assert "PRAGMA table_info" not in schema_source
assert "backfill_rag_embedding_signatures" not in rag_core
assert "backfill_rag_embedding_signatures" not in rag_shell
assert "DEFAULT_SEMANTIC_SAMPLE_WINDOWS" not in rag_retrieval
assert "sample_windows" not in rag_retrieval

print("DB/RAG baseline architectural guards verified")
PY
```

Expected:

```text
DB/RAG baseline architectural guards verified
```

- [ ] **Step 6: Verify scope against the approved baseline**

Compare the feature branch with base:

```bash
git diff --stat 966c31bb02dd12bbb974c534e987c9399209cdd6...HEAD
git diff --name-status 966c31bb02dd12bbb974c534e987c9399209cdd6...HEAD
```

Expected implementation scope:

```text
README.md
bridge/schema.py
bridge/rag.py
bridge/rag_core.py
bridge/rag_retrieval.py
tests/test_migrations.py
tests/test_database_optimization.py
tests/test_rag_scaling.py
docs/superpowers/specs/2026-09-22-db-rag-preproduction-baseline-reset-design.md
docs/superpowers/plans/2026-09-22-db-rag-preproduction-baseline-reset.md
```

No Live Sync naming files, `bridge.main` decomposition, or unrelated architecture cleanup belongs in this PR.

- [ ] **Step 7: Verify no obsolete RAG/schema symbols remain repository-wide**

Run:

```bash
python - <<'PY'
from pathlib import Path

needles = (
    "backfill_rag_embedding_signatures",
    "DEFAULT_SEMANTIC_SAMPLE_WINDOWS",
    "_migration_002_scene_state",
    "_migration_003_director_goals",
    "_migration_004_sync_lifecycle_trigger",
)

hits = []
for root_name in ("bridge", "tests"):
    for path in Path(root_name).rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in text:
                hits.append((str(path), needle))

assert not hits, hits
print("No retired DB/RAG compatibility symbols remain")
PY
```

Expected:

```text
No retired DB/RAG compatibility symbols remain
```

- [ ] **Step 8: Record execution evidence in this plan**

Append an `## Execution Evidence` section only after the corresponding commands have actually run. Record, in order:

- base SHA `966c31bb02dd12bbb974c534e987c9399209cdd6`;
- the observed Task 1 RED commit SHA and the exact failing schema-test names;
- the observed Task 1 GREEN commit SHA and focused schema-test result;
- the observed Task 2 RED commit SHA and the exact RAG compatibility-source failure;
- the observed Task 2 GREEN commit SHA and focused RAG/schema-test result;
- the observed documentation commit SHA;
- the observed final implementation-head SHA;
- compile result;
- full pytest result;
- `pip check` result;
- `pip-audit` result;
- architectural source-guard result;
- branch scope-diff result;
- exact-head CI run number and conclusion;
- whole-branch review findings.

Use only values copied from command/GitHub output in the same execution session. If any evidence is unavailable, leave the plan uncommitted at this step rather than inventing or approximating it.

- [ ] **Step 9: Commit verification evidence**

```bash
git add docs/superpowers/plans/2026-09-22-db-rag-preproduction-baseline-reset.md
git commit -m "docs: record DB and RAG baseline verification"
```

- [ ] **Step 10: Ensure the Draft PR targets upstream main**

Target:

```text
base: cepeter/SillyTavern-Telegram-Bridge:main
head: punzer4-code:refactor/db-rag-baseline-reset
```

Title:

```text
refactor: reset DB and RAG pre-production baseline
```

PR body must state:

```markdown
## Summary

- collapse the pre-production SQLite history into one current `initial_schema`
- remove upgrade-time `ALTER TABLE` / `PRAGMA table_info` compatibility paths
- require current RAG embedding namespace/signature/norm metadata
- remove lazy signature backfill and unsigned-row positional retrieval fallback
- keep the generic migration engine for future production migrations
- document that older development databases must be archived/recreated

## Compatibility

This repository is pre-production. Databases created by older development
revisions are intentionally unsupported and are not automatically converted or
deleted.

## Validation

- focused schema/RAG RED → GREEN evidence recorded in the implementation plan
- full pytest suite
- compileall
- pip check
- pip-audit
- exact-head GitHub Actions CI
```

Keep the PR Draft until exact-head CI succeeds and review is clear.

- [ ] **Step 11: Inspect GitHub Actions on the exact final head**

Required CI jobs after the merged PR #66 workflow optimization:

```text
test
dependency-audit
```

The `test` job must include locked runtime dependencies, development tooling, `pip check`, compileall, and the full pytest suite. The independent dependency-audit job must pass `pip-audit`.

Do not claim exact-head success until the workflow run for the final commit SHA reports `conclusion: success`.

- [ ] **Step 12: Perform whole-branch review**

Review the complete branch against the approved spec and verify:

```text
- no Critical or Important findings
- no obsolete schema upgrade paths
- no RAG backfill/sampling compatibility remains
- migration engine itself is unchanged
- startup cleanup is unchanged in purpose
- current Data Bank behavior is preserved
- no unrelated Live Sync/main/docs cleanup entered the diff
- no unresolved review threads/comments
- PR is mergeable
- exact PR head equals the green CI SHA
```

If a defect is found, return to the owning task, add/adjust a regression test, implement the minimum correction, and repeat exact-head verification.

- [ ] **Step 13: Mark the PR Ready for review**

Only after Steps 1-12 have current evidence.

Do not merge. Merge remains a separate user decision.

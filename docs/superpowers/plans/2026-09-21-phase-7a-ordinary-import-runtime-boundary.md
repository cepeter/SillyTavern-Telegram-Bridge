# Phase 7A — Ordinary-Import Runtime Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `performance.py`, `native_cache.py`, and `schema.py` permanently out of the shared exec runtime while preserving `bridge.runtime` compatibility and all existing behavior.

**Architecture:** Introduce an ordinary-only immutable `runtime_defaults.py`, decouple `schema.py` from normally importing heavyweight `common.py`, then pre-populate `bridge.runtime` with canonical objects from the three ordinary modules before executing the remaining legacy runtime stage. Remove those three source files from `DEFAULT_RUNTIME_STAGES` so there is exactly one function/state owner for each migrated module.

**Tech Stack:** Python 3.11, stdlib modules/subprocess/unittest, existing staged runtime loader, SQLite migration engine, GitHub Actions CI.

**Spec:** `docs/superpowers/specs/2026-09-21-phase-7a-ordinary-import-runtime-boundary-design.md`

## Global Constraints

- Baseline upstream `main` is `ef23d33950f5e83d74c6770867343380ff5d2dbf`.
- Preserve database schema contents, migration versions, cleanup queries, retention values, and transaction behavior exactly.
- Preserve `performance.py` semantics exactly.
- Preserve `native_cache.py` semantics exactly.
- Do not normally import `bridge.common` merely to satisfy `schema.py`.
- Do not add `runtime_defaults.py` to any runtime stage.
- Do not add new `bridge.runtime` dependencies to migrated production modules.
- Do not add a service locator, compatibility dictionary, `globals()` dependency lookup, or replacement runtime stage.
- Do not migrate `database.py`, routing/UI, generation, memory, Sync, Persona, extension-registry behavior, or `main.py` in this phase.
- Keep the relative order of all remaining legacy runtime files unchanged.
- Do not merge the implementation PR automatically.

## Review Focus

1. **Schema standalone import accidentally initializes duplicate `common.py` process state** — importing `bridge.schema` alone must leave both `bridge.runtime` and `bridge.common` absent from `sys.modules`.
2. **Facade/export duplication** — `bridge.runtime` must expose the exact canonical function objects from `bridge.performance`, `bridge.native_cache`, and `bridge.schema`, not equivalent exec-created copies.
3. **Native cache state split** — writes through `rt.cached_text` must be visible through `bridge.native_cache.cached_text` and vice versa because both references use one module-owned cache.
4. **Import-order sensitivity** — importing an ordinary module before or after `bridge.runtime` must produce the same canonical object identities.
5. **Retention/schema drift** — moving `PROCESSED_UPDATE_RETENTION_SECONDS` must leave the value at exactly `30 * 86400`, keep migration versions `1..4` unchanged, and keep empty/legacy DB migration behavior green.

## File Structure

- Create `bridge/runtime_defaults.py` — ordinary-only immutable runtime defaults shared safely by legacy and ordinary modules.
- Modify `bridge/common.py` — import the retention constant from `runtime_defaults.py` instead of defining it locally.
- Modify `bridge/schema.py` — import the retention constant from `runtime_defaults.py`, eliminating the normal import of `bridge.common`.
- Modify `bridge/runtime.py` — explicitly import/re-export canonical public objects from the three migrated ordinary modules before loading remaining legacy sources.
- Modify `bridge/runtime_loader.py` — remove `performance.py`, `native_cache.py`, and `schema.py` from the core stage without reordering anything else.
- Create `tests/test_runtime_import_islands.py` — fresh-process independence, facade identity, import-order, single-state, and permanent stage-retirement guards.
- Modify `tests/test_runtime_loader.py` only if existing report expectations need the retired modules removed explicitly.
- Reuse `tests/test_migrations.py` for full schema/legacy-database regression verification; do not duplicate its migration fixtures.

---

### Task 1: Extract the immutable retention default and make `schema.py` truly standalone

**Files:**
- Create: `bridge/runtime_defaults.py`
- Modify: `bridge/common.py`
- Modify: `bridge/schema.py`
- Create: `tests/test_runtime_import_islands.py`

**Interfaces:**
- Produces: `bridge.runtime_defaults.PROCESSED_UPDATE_RETENTION_SECONDS: int` with value `30 * 86400`.
- Preserves: `bridge.common.PROCESSED_UPDATE_RETENTION_SECONDS` and `bridge.schema._PROCESSED_UPDATE_RETENTION_SECONDS` at the same value.
- Establishes: `import bridge.schema` does not import `bridge.common` or `bridge.runtime`.

- [ ] **Step 1: Write the failing fresh-process import tests**

Create `tests/test_runtime_import_islands.py`:

```python
from pathlib import Path
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).parents[1]


class RuntimeImportIslandTests(unittest.TestCase):
    def _run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_runtime_defaults_is_ordinary_only_and_has_existing_retention_value(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.runtime_defaults as defaults\n"
            "assert defaults.PROCESSED_UPDATE_RETENTION_SECONDS == 30 * 86400\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
        )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_schema_import_does_not_import_runtime_or_common(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.schema as schema\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
            "assert [m.version for m in schema.SCHEMA_MIGRATIONS] == [1, 2, 3, 4]\n"
        )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_performance_and_native_cache_import_without_runtime(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.performance\n"
            "import bridge.native_cache\n"
            "assert 'bridge.runtime' not in sys.modules\n"
        )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python -m pytest   tests/test_runtime_import_islands.py::RuntimeImportIslandTests::test_runtime_defaults_is_ordinary_only_and_has_existing_retention_value   tests/test_runtime_import_islands.py::RuntimeImportIslandTests::test_schema_import_does_not_import_runtime_or_common   -q
```

Expected:

- `test_runtime_defaults...` FAILS because `bridge.runtime_defaults` does not exist.
- `test_schema_import...` FAILS because current `bridge.schema` imports `bridge.common`.

Run the already-independent check separately:

```bash
python -m pytest   tests/test_runtime_import_islands.py::RuntimeImportIslandTests::test_performance_and_native_cache_import_without_runtime   -q
```

Expected: PASS. This is characterization, not the RED target.

- [ ] **Step 3: Create `runtime_defaults.py`**

Create `bridge/runtime_defaults.py`:

```python
"""Immutable defaults shared by ordinary and legacy runtime modules."""

PROCESSED_UPDATE_RETENTION_SECONDS = 30 * 86400
```

Do not import `common.py`, `runtime.py`, or mutable process state here.

- [ ] **Step 4: Retarget the retention constant in `common.py`**

Near the imports in `bridge/common.py`, add:

```python
from bridge.runtime_defaults import (
    PROCESSED_UPDATE_RETENTION_SECONDS,
)
```

Delete the local assignment:

```python
PROCESSED_UPDATE_RETENTION_SECONDS = 30 * 86400
```

Do not move any other constants in this phase.

- [ ] **Step 5: Retarget `schema.py` away from `common.py`**

Replace:

```python
from bridge.common import (
    PROCESSED_UPDATE_RETENTION_SECONDS as _PROCESSED_UPDATE_RETENTION_SECONDS,
)
```

with:

```python
from bridge.runtime_defaults import (
    PROCESSED_UPDATE_RETENTION_SECONDS as _PROCESSED_UPDATE_RETENTION_SECONDS,
)
```

No other `schema.py` behavior changes.

- [ ] **Step 6: Run the import-island tests**

Run:

```bash
python -m pytest tests/test_runtime_import_islands.py -q
```

Expected: all three tests PASS.

- [ ] **Step 7: Run schema regression tests**

Run:

```bash
python -m pytest tests/test_migrations.py -q
```

Expected: PASS, including:

- empty database bootstrap;
- representative legacy schema upgrade;
- startup cleanup after migrations are already applied;
- migration versions exactly 1–4;
- existing Scene State/Director Goal data preservation.

- [ ] **Step 8: Commit the dependency extraction**

```bash
git add   bridge/runtime_defaults.py   bridge/common.py   bridge/schema.py   tests/test_runtime_import_islands.py
git commit -m "refactor: isolate immutable runtime defaults"
```

---

### Task 2: Retire exec ownership and make `bridge.runtime` an explicit facade for the first island

**Files:**
- Modify: `tests/test_runtime_import_islands.py`
- Modify: `bridge/runtime.py`
- Modify: `bridge/runtime_loader.py`
- Modify: `tests/test_runtime_loader.py` only if its load-report assertions require explicit retired-module checks.

**Interfaces:**
- Consumes canonical ordinary modules:
  - `bridge.performance.performance_enabled`
  - `bridge.performance.perf_span`
  - `bridge.performance.timed_call`
  - `bridge.native_cache.cached_json`
  - `bridge.native_cache.cached_png_metadata`
  - `bridge.native_cache.cached_text`
  - `bridge.schema.SCHEMA_MIGRATIONS`
  - `bridge.schema.initialize_database_schema`
- Produces the same names through `bridge.runtime` as exact object re-exports.
- Removes `performance.py`, `native_cache.py`, and `schema.py` from all `DEFAULT_RUNTIME_STAGES`.

- [ ] **Step 1: Add failing stage-retirement and facade-identity tests**

Append to `RuntimeImportIslandTests`:

```python
    def test_first_import_island_is_absent_from_runtime_stages(self):
        from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

        loaded = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertTrue(
            {
                "performance.py",
                "native_cache.py",
                "schema.py",
                "runtime_defaults.py",
            }.isdisjoint(loaded)
        )

    def test_runtime_facade_uses_canonical_import_island_objects(self):
        import bridge.native_cache as native_cache
        import bridge.performance as performance
        import bridge.runtime as rt
        import bridge.schema as schema

        self.assertIs(rt.performance_enabled, performance.performance_enabled)
        self.assertIs(rt.perf_span, performance.perf_span)
        self.assertIs(rt.timed_call, performance.timed_call)
        self.assertIs(rt.cached_json, native_cache.cached_json)
        self.assertIs(rt.cached_png_metadata, native_cache.cached_png_metadata)
        self.assertIs(rt.cached_text, native_cache.cached_text)
        self.assertIs(rt.SCHEMA_MIGRATIONS, schema.SCHEMA_MIGRATIONS)
        self.assertIs(
            rt.initialize_database_schema,
            schema.initialize_database_schema,
        )

    def test_runtime_load_report_has_no_import_island_sources(self):
        import bridge.runtime as rt

        loaded = {
            entry["module"]
            for entry in rt.RUNTIME_LOAD_REPORT
        }
        self.assertTrue(
            {
                "performance.py",
                "native_cache.py",
                "schema.py",
                "runtime_defaults.py",
            }.isdisjoint(loaded)
        )
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python -m pytest   tests/test_runtime_import_islands.py::RuntimeImportIslandTests::test_first_import_island_is_absent_from_runtime_stages   tests/test_runtime_import_islands.py::RuntimeImportIslandTests::test_runtime_facade_uses_canonical_import_island_objects   tests/test_runtime_import_islands.py::RuntimeImportIslandTests::test_runtime_load_report_has_no_import_island_sources   -q
```

Expected: FAIL because the three source files are still exec-loaded and the runtime functions are distinct exec-created objects rather than canonical module objects.

- [ ] **Step 3: Add explicit canonical imports to `runtime.py` before loader execution**

After the `Path` import and before importing/using the runtime loader, add:

```python
from bridge.native_cache import (
    cached_json,
    cached_png_metadata,
    cached_text,
)
from bridge.performance import (
    perf_span,
    performance_enabled,
    timed_call,
)
from bridge.schema import (
    SCHEMA_MIGRATIONS,
    initialize_database_schema,
)
```

Keep these names public in the `bridge.runtime` namespace.

Do not alias them to private names; legacy exec-loaded files such as `cards.py` and `database.py` need the public names available in the shared namespace before they execute.

- [ ] **Step 4: Remove the three migrated files from the core runtime stage**

In `bridge/runtime_loader.py`, change the beginning of the core module tuple from:

```python
"common.py", "performance.py", "native_cache.py",
"cards.py", "schema.py", "database.py",
```

to:

```python
"common.py",
"cards.py", "database.py",
```

Do not change the relative order of any remaining module.

Do not add `runtime_defaults.py` anywhere in `DEFAULT_RUNTIME_STAGES`.

- [ ] **Step 5: Add an explicit permanent-retirement assertion to the loader suite**

In `tests/test_runtime_loader.py`, add:

```python
    def test_phase_7a_import_island_is_never_exec_loaded(self):
        loaded_modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertTrue(
            {
                "performance.py",
                "native_cache.py",
                "schema.py",
                "runtime_defaults.py",
            }.isdisjoint(loaded_modules)
        )
```

This duplicates the architecture invariant intentionally in the loader-focused suite so future runtime-loader edits cannot silently reintroduce these files.

- [ ] **Step 6: Run the focused runtime suites**

Run:

```bash
python -m pytest   tests/test_runtime_import_islands.py   tests/test_runtime_loader.py   -q
```

Expected: PASS.

- [ ] **Step 7: Run migration tests again through the new runtime topology**

Run:

```bash
python -m pytest tests/test_migrations.py -q
```

Expected: PASS.

This confirms `database.py`, still exec-loaded, successfully resolves the explicitly pre-populated canonical `initialize_database_schema`.

- [ ] **Step 8: Commit the first exec-retirement tranche**

```bash
git add   bridge/runtime.py   bridge/runtime_loader.py   tests/test_runtime_import_islands.py   tests/test_runtime_loader.py
git commit -m "refactor: establish first ordinary runtime import island"
```

---

### Task 3: Prove import-order independence and single native-cache state

**Files:**
- Modify: `tests/test_runtime_import_islands.py`

**Interfaces:**
- Consumes: canonical re-exports established in Task 2.
- Produces: permanent subprocess and state-sharing regression guards proving the first import island is genuinely independent of runtime load order.

- [ ] **Step 1: Add two-way import-order tests**

Append:

```python
    def test_ordinary_modules_before_runtime_keep_canonical_identity(self):
        completed = self._run_python(
            "import bridge.performance as performance\n"
            "import bridge.native_cache as native_cache\n"
            "import bridge.schema as schema\n"
            "import bridge.runtime as rt\n"
            "assert rt.perf_span is performance.perf_span\n"
            "assert rt.cached_json is native_cache.cached_json\n"
            "assert rt.initialize_database_schema is schema.initialize_database_schema\n"
        )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_runtime_before_ordinary_modules_keeps_canonical_identity(self):
        completed = self._run_python(
            "import bridge.runtime as rt\n"
            "import bridge.performance as performance\n"
            "import bridge.native_cache as native_cache\n"
            "import bridge.schema as schema\n"
            "assert rt.perf_span is performance.perf_span\n"
            "assert rt.cached_json is native_cache.cached_json\n"
            "assert rt.initialize_database_schema is schema.initialize_database_schema\n"
        )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
```

These should already PASS after Task 2. They are a regression characterization for the architecture now created, not a new production RED slice.

- [ ] **Step 2: Add a shared native-cache state test**

Append:

```python
    def test_runtime_and_native_cache_share_one_text_cache(self):
        import bridge.native_cache as native_cache
        import bridge.runtime as rt

        key = "phase-7a-single-cache-state"
        native_cache._TEXT_CACHE.pop(key, None)
        try:
            first = rt.cached_text(
                key,
                lambda: "from-runtime",
            )
            second = native_cache.cached_text(
                key,
                lambda: "from-module",
            )
        finally:
            native_cache._TEXT_CACHE.pop(key, None)

        self.assertEqual(first, "from-runtime")
        self.assertEqual(second, "from-runtime")
        self.assertIs(rt.cached_text, native_cache.cached_text)
```

Expected: PASS only when the facade uses the canonical module function/state.

- [ ] **Step 3: Add the production dependency guard**

Append:

```python
    def test_migrated_modules_do_not_import_bridge_runtime(self):
        for filename in (
            "runtime_defaults.py",
            "performance.py",
            "native_cache.py",
            "schema.py",
        ):
            with self.subTest(filename=filename):
                source = (
                    REPO_ROOT
                    / "bridge"
                    / filename
                ).read_text(encoding="utf-8")
                self.assertNotIn(
                    "import bridge.runtime",
                    source,
                )
                self.assertNotIn(
                    "from bridge.runtime import",
                    source,
                )
```

- [ ] **Step 4: Run the complete import-island suite**

Run:

```bash
python -m pytest tests/test_runtime_import_islands.py -q
```

Expected: PASS.

- [ ] **Step 5: Run focused behavior characterization for the ordinary modules**

Run:

```bash
python - <<'PY'
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import bridge.native_cache as cache
import bridge.performance as performance
import bridge.runtime_defaults as defaults

assert defaults.PROCESSED_UPDATE_RETENTION_SECONDS == 30 * 86400

with patch.dict(os.environ, {"SILLYTAVERN_PERF_LOG": "1"}, clear=False):
    assert performance.performance_enabled() is True
with patch.dict(os.environ, {"SILLYTAVERN_PERF_LOG": "0"}, clear=False):
    assert performance.performance_enabled() is False

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "data.json"
    path.write_text('{"value": 1}', encoding="utf-8")
    first = cache.cached_json(path)
    first["value"] = 999
    second = cache.cached_json(path)
    assert second == {"value": 1}

print("Phase 7A ordinary-module behavior verified")
PY
```

Expected: prints `Phase 7A ordinary-module behavior verified`.

- [ ] **Step 6: Commit the permanent architecture guards**

```bash
git add tests/test_runtime_import_islands.py
git commit -m "test: guard phase 7a import independence"
```

---

### Task 4: Exact-head verification and PR readiness

**Files:**
- No production changes expected.
- Review all Phase 7A changed files plus the approved spec and this plan.

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: exact-head verification evidence and an unmerged Phase 7A PR ready for user review.

- [ ] **Step 1: Verify current upstream drift**

Run:

```bash
git fetch upstream main
git rev-list --left-right --count upstream/main...HEAD
```

Expected: behind count is `0`. If upstream moved, inspect overlap before rebasing.

- [ ] **Step 2: Verify the loader has permanently retired the island**

Run:

```bash
python - <<'PY'
from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

loaded = {
    module
    for stage in DEFAULT_RUNTIME_STAGES
    for module in stage.modules
}
for filename in (
    "performance.py",
    "native_cache.py",
    "schema.py",
    "runtime_defaults.py",
):
    assert filename not in loaded, filename

print("Phase 7A runtime-stage retirement verified")
PY
```

Expected: prints `Phase 7A runtime-stage retirement verified`.

- [ ] **Step 3: Verify exact facade ownership**

Run:

```bash
python - <<'PY'
import bridge.native_cache as native_cache
import bridge.performance as performance
import bridge.runtime as rt
import bridge.schema as schema

assert rt.performance_enabled is performance.performance_enabled
assert rt.perf_span is performance.perf_span
assert rt.timed_call is performance.timed_call
assert rt.cached_json is native_cache.cached_json
assert rt.cached_png_metadata is native_cache.cached_png_metadata
assert rt.cached_text is native_cache.cached_text
assert rt.SCHEMA_MIGRATIONS is schema.SCHEMA_MIGRATIONS
assert rt.initialize_database_schema is schema.initialize_database_schema

loaded = {
    entry["module"]
    for entry in rt.RUNTIME_LOAD_REPORT
}
assert "performance.py" not in loaded
assert "native_cache.py" not in loaded
assert "schema.py" not in loaded
assert "runtime_defaults.py" not in loaded

print("Phase 7A facade identity verified")
PY
```

Expected: prints `Phase 7A facade identity verified`.

- [ ] **Step 4: Run Python compilation exactly as CI does**

```bash
python -m compileall -q bridge tests sillytavern_telegram_bridge.py
```

Expected: exit 0.

- [ ] **Step 5: Run full unittest suite**

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 6: Run full pytest suite**

```bash
python -m pytest -q
```

Expected: all tests and subtests PASS.

- [ ] **Step 7: Validate dependencies**

```bash
python -m pip check
```

Expected: `No broken requirements found.`

- [ ] **Step 8: Audit locked dependencies**

```bash
python -m pip_audit -r requirements.lock
```

Expected: no known vulnerabilities.

- [ ] **Step 9: Review whole branch against the approved scope**

Run:

```bash
git diff --stat ef23d33950f5e83d74c6770867343380ff5d2dbf...HEAD
git diff ef23d33950f5e83d74c6770867343380ff5d2dbf...HEAD --   bridge/runtime_defaults.py   bridge/common.py   bridge/schema.py   bridge/runtime.py   bridge/runtime_loader.py   tests/test_runtime_import_islands.py   tests/test_runtime_loader.py
```

Review specifically for:

- only one constant moved out of `common.py`;
- no other `common.py` configuration/state moved;
- no ordinary module imports `bridge.runtime`;
- no migrated module is still exec-loaded;
- no remaining runtime module was reordered;
- no schema DDL/migration/version change;
- no native-cache behavior change;
- no performance behavior change;
- no extension registry, service, routing, generation, Sync, Persona, JobService, or `main.py` behavior change.

- [ ] **Step 10: Run exact-head GitHub Actions and prepare the PR**

Open a draft PR if needed so authoritative CI runs against the exact branch head.

Confirm:

- compile: success;
- full unittest: success;
- full pytest: success;
- `pip check`: success;
- `pip-audit`: success;
- branch: 0 behind current `main`;
- PR: mergeable;
- review threads/comments: none unresolved;
- whole-branch review: no Critical or Important findings.

Mark the PR ready for review after all checks are green.

Do not merge it.

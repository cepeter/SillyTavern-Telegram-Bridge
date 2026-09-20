# Phase 6F Persona Load Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the final Persona runtime override by making `persona_sync.py::load_personas` the sole canonical loader while preserving the currently effective native Persona read behavior exactly.

**Architecture:** First characterize the existing final loader semantics and call-time binding of Persona read helpers. Then atomically delete the duplicate `cards.py::load_personas` definition and remove the `persona_sync.py::load_personas` override allowlist, leaving `persona_sync.py` runtime-loaded but with zero public callable overrides. No new adapter or service boundary is introduced.

**Tech Stack:** Python 3.11, stdlib `unittest`, `unittest.mock`, pathlib, existing shared compatibility runtime, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-20-phase-6f-persona-load-owner-design.md`

## Global Constraints

- `persona_sync.py` becomes the canonical public owner of `load_personas`.
- Delete only the duplicate `cards.py::load_personas`.
- Keep `persona_sync.py::load_personas` behavior unchanged.
- Successful loads continue to delegate to `load_native_personas()`.
- Loader failure still returns `{}`.
- Loader failure warning remains exactly `Could not load native Persona metadata`.
- Loader failure warning still passes `exc_info=True`.
- Native Persona cache behavior remains unchanged.
- `get_persona`, `default_persona_id`, and `persona_name` remain in `cards.py`.
- `PersonaService`, `IntegrityCheckedPersonaStore`, native create/update/delete, settings/API/file access, and avatar behavior remain unchanged.
- `persona_sync.py` remains in the existing `native_adapter_overrides` runtime stage.
- Do not rename runtime stages.
- No new `_ORIGINAL_*` capture, public runtime override, service locator, or load-order dependency.
- Do not modify `recovery.py`.
- Do not begin Phase 7.

## Review Focus

- `load_native_personas()` raises: public `load_personas()` must return `{}` and log exactly `Could not load native Persona metadata` with `exc_info=True`.
- Final runtime `load_personas` is patched after runtime construction: `get_persona()` must observe the patched loader at call time.
- Final runtime `load_personas` is patched after runtime construction: `default_persona_id()` must observe the patched loader and still validate the configured default against that result.
- Final runtime `load_personas` is patched after runtime construction: `persona_name()` and compatibility `PersonaService` must observe the patched final data rather than a captured earlier function.
- After cutover, `persona_sync.py` must remain runtime-loaded but report zero public callable overrides, and `recovery.py` must be the only module with any allowlisted public runtime override.

---

## File Structure

**Modify `tests/test_persona_native_sync.py`**
- Characterize the currently effective loader failure semantics.
- Pin call-time binding for `get_persona`, `default_persona_id`, and `persona_name`.

**Modify `tests/test_persona_service.py`**
- Strengthen the existing compatibility-service late-binding test so `list()` explicitly observes patched final `load_personas`.

**Modify `tests/test_persona_native_storage.py`**
- Add source-boundary coverage proving only `persona_sync.py` defines public `load_personas`.

**Modify `tests/test_runtime_loader.py`**
- Add canonical owner, zero-override, no-allowlist, and "recovery-only remaining override module" assertions.

**Modify `bridge/cards.py`**
- Delete the duplicate `load_personas` function only.
- Leave `get_persona`, `default_persona_id`, and `persona_name` unchanged.

**Modify `bridge/runtime_loader.py`**
- Keep `persona_sync.py` in `native_adapter_overrides`.
- Remove only `(("persona_sync.py", ("load_personas",)),)`.

**Do not modify `bridge/persona_sync.py`.**

---

### Task 1: Characterize Final Persona Loader Semantics and Call-Time Binding

**Files:**
- Modify: `tests/test_persona_native_sync.py`
- Modify: `tests/test_persona_service.py`

**Interfaces:**
- Consumes:
  - existing public `rt.load_personas() -> dict[str, dict[str, object]]`;
  - existing `rt.get_persona(persona_id)`;
  - existing `rt.default_persona_id()`;
  - existing `rt.persona_name(persona_id)`;
  - existing `rt.compatibility_persona_service()`.
- Produces:
  - characterization coverage that Task 2 must preserve exactly.
- No production code changes in this task.

- [ ] **Step 1: Add mock support to the native Persona test module**

Change the imports at the top of `tests/test_persona_native_sync.py` from:

```python
import unittest

import bridge.runtime as rt
```

to:

```python
import unittest
from unittest.mock import patch

import bridge.runtime as rt
```

- [ ] **Step 2: Pin the currently effective loader failure semantics**

Add to `NativePersonaSyncTests`:

```python
    def test_loader_failure_returns_empty_and_logs_final_warning(self):
        with patch.object(
            rt,
            "load_native_personas",
            side_effect=RuntimeError("boom"),
        ), patch.object(
            rt.logging,
            "warning",
        ) as warning:
            result = rt.load_personas()

        self.assertEqual(result, {})
        warning.assert_called_once_with(
            "Could not load native Persona metadata",
            exc_info=True,
        )
```

This must pass before the ownership cutover. It pins the effective `persona_sync.py` behavior rather than the obsolete `cards.py` warning text.

- [ ] **Step 3: Pin call-time binding for cards.py Persona helpers**

Add to `NativePersonaSyncTests`:

```python
    def test_persona_read_helpers_use_final_runtime_loader_at_call_time(self):
        personas = {
            "patched.png": {
                "name": "Patched",
                "description": "Patched description",
                "sillytavern_avatar": "patched.png",
            }
        }

        with patch.object(
            rt,
            "load_personas",
            return_value=personas,
        ), patch.object(
            rt,
            "_native_settings",
            return_value={
                "power_user": {
                    "default_persona": "patched.png",
                }
            },
        ):
            self.assertEqual(
                rt.get_persona("patched.png"),
                personas["patched.png"],
            )
            self.assertEqual(
                rt.default_persona_id(),
                "patched.png",
            )
            self.assertEqual(
                rt.persona_name("patched.png"),
                "Patched",
            )
```

This proves the helpers resolve the shared runtime name at call time and do not capture the earlier `cards.py::load_personas` object.

- [ ] **Step 4: Strengthen compatibility PersonaService late-binding coverage**

In `tests/test_persona_service.py::PersonaCompatibilityServiceTests.test_compatibility_service_late_binds_final_runtime_collaborators`, immediately after:

```python
            service = rt.compatibility_persona_service()
```

add:

```python
            self.assertEqual(
                service.list(),
                {
                    "native.png": {
                        "name": "Native",
                        "description": "D",
                        "sillytavern_avatar": "native.png",
                    }
                },
            )
```

Keep the existing `service.name()`, `service.default_id()`, and update assertions.

- [ ] **Step 5: Run characterization tests**

Run:

```bash
python -m unittest   tests.test_persona_native_sync.NativePersonaSyncTests.test_loader_failure_returns_empty_and_logs_final_warning   tests.test_persona_native_sync.NativePersonaSyncTests.test_persona_read_helpers_use_final_runtime_loader_at_call_time   tests.test_persona_service.PersonaCompatibilityServiceTests.test_compatibility_service_late_binds_final_runtime_collaborators -v
```

Expected: PASS on the pre-cutover runtime.

These are characterization tests, not architectural RED tests. If any fail, stop and reconcile the spec with actual merged behavior before Task 2.

- [ ] **Step 6: Commit Task 1**

```bash
git add   tests/test_persona_native_sync.py   tests/test_persona_service.py
git commit -m "test: pin final Persona loader semantics"
```

---

### Task 2: Atomically Retire the Persona load_personas Runtime Override

**Files:**
- Modify: `bridge/cards.py`
- Modify: `bridge/runtime_loader.py`
- Modify: `tests/test_persona_native_storage.py`
- Modify: `tests/test_runtime_loader.py`

**Interfaces:**
- Consumes:
  - characterized final loader semantics from Task 1;
  - unchanged `persona_sync.py::load_personas() -> dict[str, dict[str, object]]`.
- Produces:
  - exactly one public runtime definition of `load_personas`;
  - canonical owner `persona_sync.py`;
  - `persona_sync.py` runtime-loaded with zero allowed/public overrides;
  - `recovery.py` as the only remaining module with an allowlisted public override.
- Production invariant:
  - `bridge/persona_sync.py` must not be edited.

- [ ] **Step 1: Add canonical source-owner RED tests**

In `tests/test_persona_native_storage.py::NativePersonaSourceBoundaryTests`, add:

```python
    def test_persona_sync_is_the_only_runtime_load_personas_definition(self):
        root = Path(__file__).parents[1] / "bridge"
        cards = (root / "cards.py").read_text(
            encoding="utf-8"
        )
        persona_sync = (
            root / "persona_sync.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn(
            "def load_personas(",
            cards,
        )
        self.assertIn(
            "def load_personas(",
            persona_sync,
        )
        self.assertIn(
            "Could not load native Persona metadata",
            persona_sync,
        )
```

- [ ] **Step 2: Add runtime ownership/retirement RED tests**

In `tests/test_runtime_loader.py::RuntimeLoaderTests`, add:

```python
    def test_persona_load_public_owner_is_persona_sync(self):
        self.assertEqual(
            Path(
                rt.load_personas.__code__.co_filename
            ).name,
            "persona_sync.py",
        )

    def test_persona_sync_remains_loaded_without_public_overrides(self):
        stage = next(
            stage
            for stage in DEFAULT_RUNTIME_STAGES
            if stage.name == "native_adapter_overrides"
        )
        self.assertIn(
            "persona_sync.py",
            stage.modules,
        )
        self.assertEqual(
            stage.allowed_overrides_for(
                "persona_sync.py"
            ),
            frozenset(),
        )

        report = next(
            item
            for item in rt.RUNTIME_LOAD_REPORT
            if item["module"] == "persona_sync.py"
        )
        self.assertEqual(
            report["public_callable_overrides"],
            (),
        )

    def test_load_personas_has_no_runtime_override_allowlist(self):
        for stage in DEFAULT_RUNTIME_STAGES:
            for filename, names in (
                stage.allowed_public_callable_overrides
            ):
                self.assertNotIn(
                    "load_personas",
                    names,
                    msg=(
                        f"{filename} still overrides "
                        "load_personas"
                    ),
                )

    def test_recovery_is_only_remaining_public_override_module(self):
        allowlisted_modules = {
            filename
            for stage in DEFAULT_RUNTIME_STAGES
            for filename, names in (
                stage.allowed_public_callable_overrides
            )
            if names
        }
        self.assertEqual(
            allowlisted_modules,
            {"recovery.py"},
        )
```

The public-owner assertion already passes in the current late-override state. The other three assertions must fail before cutover and become green afterward.

- [ ] **Step 3: Run architecture tests and verify RED**

Run:

```bash
python -m unittest   tests.test_persona_native_storage.NativePersonaSourceBoundaryTests.test_persona_sync_is_the_only_runtime_load_personas_definition   tests.test_runtime_loader.RuntimeLoaderTests.test_persona_load_public_owner_is_persona_sync   tests.test_runtime_loader.RuntimeLoaderTests.test_persona_sync_remains_loaded_without_public_overrides   tests.test_runtime_loader.RuntimeLoaderTests.test_load_personas_has_no_runtime_override_allowlist   tests.test_runtime_loader.RuntimeLoaderTests.test_recovery_is_only_remaining_public_override_module -v
```

Expected:

- source-owner test FAILS because `cards.py` still defines `load_personas`;
- zero-override test FAILS because `persona_sync.py` is allowlisted/reported as overriding `load_personas`;
- no-allowlist test FAILS because the current allowlist contains `load_personas`;
- recovery-only test FAILS because `persona_sync.py` is still another allowlisted override module;
- public-owner assertion PASSES and serves as characterization of the currently effective owner.

- [ ] **Step 4: Delete only the duplicate cards.py loader**

In `bridge/cards.py`, delete exactly:

```python
def load_personas() -> dict[str, dict[str, str]]:
    try:
        return load_native_personas()
    except Exception:
        logging.warning(
            "Could not load native SillyTavern personas",
            exc_info=True,
        )
        return {}
```

including the blank lines immediately following it.

Do not modify:

```python
def get_persona(...)
def default_persona_id(...)
def persona_name(...)
```

- [ ] **Step 5: Remove only the Persona override allowlist**

Change `bridge/runtime_loader.py` from:

```python
    RuntimeStage(
        "native_adapter_overrides",
        ("persona_sync.py",),
        (("persona_sync.py", ("load_personas",)),),
    ),
```

to:

```python
    RuntimeStage(
        "native_adapter_overrides",
        ("persona_sync.py",),
    ),
```

Do not rename the stage.

Do not move `persona_sync.py` to another stage.

Do not alter `recovery_overrides`.

- [ ] **Step 6: Run the architectural tests and verify GREEN**

Run:

```bash
python -m unittest   tests.test_persona_native_storage.NativePersonaSourceBoundaryTests.test_persona_sync_is_the_only_runtime_load_personas_definition   tests.test_runtime_loader.RuntimeLoaderTests.test_persona_load_public_owner_is_persona_sync   tests.test_runtime_loader.RuntimeLoaderTests.test_persona_sync_remains_loaded_without_public_overrides   tests.test_runtime_loader.RuntimeLoaderTests.test_load_personas_has_no_runtime_override_allowlist   tests.test_runtime_loader.RuntimeLoaderTests.test_recovery_is_only_remaining_public_override_module -v
```

Expected: PASS.

- [ ] **Step 7: Re-run Task 1 behavior/call-time-binding tests after cutover**

Run:

```bash
python -m unittest   tests.test_persona_native_sync.NativePersonaSyncTests.test_loader_failure_returns_empty_and_logs_final_warning   tests.test_persona_native_sync.NativePersonaSyncTests.test_persona_read_helpers_use_final_runtime_loader_at_call_time   tests.test_persona_service.PersonaCompatibilityServiceTests.test_compatibility_service_late_binds_final_runtime_collaborators -v
```

Expected: PASS unchanged.

- [ ] **Step 8: Run focused Persona and runtime suites**

Run:

```bash
python -m unittest   tests.test_persona_native_sync   tests.test_persona_native_storage   tests.test_persona_service   tests.test_persona_integrity   tests.test_persona_editor   tests.test_runtime_loader -v
```

Expected: all tests PASS.

- [ ] **Step 9: Run a source/ownership guard**

Run:

```bash
python - <<'PY'
from pathlib import Path

import bridge.runtime as rt
from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

root = Path("bridge")
cards = (root / "cards.py").read_text(
    encoding="utf-8"
)
persona_sync = (
    root / "persona_sync.py"
).read_text(encoding="utf-8")

assert "def load_personas(" not in cards
assert "def load_personas(" in persona_sync
assert (
    Path(rt.load_personas.__code__.co_filename).name
    == "persona_sync.py"
)

native_stage = next(
    stage
    for stage in DEFAULT_RUNTIME_STAGES
    if stage.name == "native_adapter_overrides"
)
assert "persona_sync.py" in native_stage.modules
assert (
    native_stage.allowed_overrides_for(
        "persona_sync.py"
    )
    == frozenset()
)

report = next(
    item
    for item in rt.RUNTIME_LOAD_REPORT
    if item["module"] == "persona_sync.py"
)
assert report["public_callable_overrides"] == ()

for stage in DEFAULT_RUNTIME_STAGES:
    for filename, names in (
        stage.allowed_public_callable_overrides
    ):
        assert "load_personas" not in names, (
            filename,
            names,
        )

allowlisted_modules = {
    filename
    for stage in DEFAULT_RUNTIME_STAGES
    for filename, names in (
        stage.allowed_public_callable_overrides
    )
    if names
}
assert allowlisted_modules == {"recovery.py"}

print(
    "Phase 6F Persona load ownership verified"
)
PY
```

Expected:

```text
Phase 6F Persona load ownership verified
```

- [ ] **Step 10: Commit Task 2**

```bash
git add   bridge/cards.py   bridge/runtime_loader.py   tests/test_persona_native_storage.py   tests/test_runtime_loader.py
git commit -m "refactor: retire Persona load runtime override"
```

---

### Task 3: Exact-Head Verification, Whole-Branch Review, and PR Readiness

**Files:**
- Modify: `docs/superpowers/plans/2026-09-20-phase-6f-persona-load-owner.md`
- No production changes unless verification finds a defect. Any defect returns to the owning RED -> GREEN task.

**Interfaces:**
- Consumes: completed Phase 6F branch.
- Produces: exact-head verification evidence and a Draft PR against upstream `main`.
- Does not merge the PR.

- [ ] **Step 1: Run compile verification**

Run:

```bash
python -m compileall -q   bridge   tests   sillytavern_telegram_bridge.py
```

Expected: exit 0.

- [ ] **Step 2: Run full unittest discovery**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 3: Run full pytest**

Run:

```bash
python -m pytest -q
```

Expected: all tests PASS.

- [ ] **Step 4: Validate dependency environment**

Run:

```bash
python -m pip check
```

Expected:

```text
No broken requirements found.
```

- [ ] **Step 5: Audit locked dependencies**

Run:

```bash
python -m pip_audit -r requirements.lock
```

Expected: no known vulnerabilities.

If local `pip-audit` is unavailable, exact-head GitHub Actions dependency audit is authoritative; do not install unrelated tooling solely for this check.

- [ ] **Step 6: Verify Phase 6F non-scope equivalence against baseline**

Baseline:

```text
df552d3d40b92c1016431fb4ef7eeee74bbbc284
```

Required unchanged files:

```text
bridge/persona_sync.py
bridge/persona_service.py
bridge/persona_integrity.py
bridge/recovery.py
```

All four must be byte-for-byte identical to baseline.

In `bridge/cards.py`, verify these function bodies are byte-for-byte identical to baseline:

```text
get_persona
default_persona_id
persona_name
```

The only Phase 6F production change in `cards.py` must be deletion of the duplicate `load_personas` function.

In `bridge/runtime_loader.py`, the only Phase 6F production change must be removal of the `persona_sync.py::load_personas` allowlist tuple.

- [ ] **Step 7: Verify the currently effective loader body was preserved exactly**

Extract baseline and final `persona_sync.py::load_personas` and compare byte-for-byte.

Required exact body:

```python
def load_personas() -> dict[str, dict[str, object]]:
    """Load native Persona metadata; the bridge JSON catalog is not used."""
    try:
        return load_native_personas()
    except Exception:
        logging.warning("Could not load native Persona metadata", exc_info=True)
        return {}
```

Any difference is out of scope and must be reverted unless required by a failing regression test.

- [ ] **Step 8: Verify final runtime override state**

Run:

```bash
python - <<'PY'
from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

entries = [
    (filename, name)
    for stage in DEFAULT_RUNTIME_STAGES
    for filename, names in (
        stage.allowed_public_callable_overrides
    )
    for name in names
]

assert entries
assert {
    filename
    for filename, _name in entries
} == {"recovery.py"}
assert all(
    name != "load_personas"
    for _filename, name in entries
)

print("remaining runtime overrides:")
for filename, name in entries:
    print(f"{filename}: {name}")
PY
```

Expected: every printed entry belongs to `recovery.py`, and none is `load_personas`.

- [ ] **Step 9: Check upstream drift**

Compare current upstream `main` with baseline `df552d3d40b92c1016431fb4ef7eeee74bbbc284`.

If upstream has not moved, continue.

If upstream changed only unrelated files, document that result.

If upstream changed any Phase 6F implementation/test file, reconcile first and rerun focused plus full verification.

- [ ] **Step 10: Record execution evidence**

Append `## Execution Evidence` to this plan with:

- Task 1 characterization commit SHA and test result;
- Task 2 RED commit SHA and exact failing assertions;
- Task 2 GREEN commit SHA;
- any execution rulings with cost-if-wrong;
- final implementation head SHA;
- unittest result;
- pytest result;
- `pip check` result;
- dependency audit result;
- source/ownership guard result;
- byte-for-byte non-scope equivalence result;
- exact `persona_sync.py::load_personas` equivalence result;
- final remaining runtime override module set;
- current upstream `main` SHA and drift result.

Do not claim exact-head CI success until GitHub Actions completes on that exact SHA.

- [ ] **Step 11: Commit verification documentation**

Run:

```bash
git add   docs/superpowers/plans/2026-09-20-phase-6f-persona-load-owner.md
git commit -m "docs: record Phase 6F verification"
```

- [ ] **Step 12: Open or update a Draft PR**

Target:

```text
base: cepeter/SillyTavern-Telegram-Bridge:main
head: punzer4-code:refactor/phase-6f-persona-load-owner
```

Title:

```text
refactor: retire Persona load runtime override
```

PR body must state:

- `persona_sync.py` is the canonical `load_personas` owner;
- duplicate `cards.py::load_personas` is removed;
- `persona_sync.py` remains runtime-loaded with zero public overrides;
- loader warning/fallback behavior is unchanged;
- Persona helper and compatibility-service call-time binding is preserved;
- `PersonaService`, Persona integrity, native persistence, and `recovery.py` are unchanged;
- `recovery.py` is now the only allowlisted public runtime override module;
- RED -> GREEN evidence and exact verification results.

Keep the PR Draft until exact-head CI succeeds and review is clear.

- [ ] **Step 13: Inspect GitHub Actions on the exact final head**

Required successful workflow steps:

- checkout;
- Python 3.11 setup;
- packaging tools;
- locked runtime dependencies;
- `pip check`;
- development test tooling;
- compile;
- unittest discovery;
- pytest;
- pip-audit installation;
- locked dependency audit.

Expected: workflow conclusion `success` on the exact PR head SHA.

- [ ] **Step 14: Perform whole-branch review and inspect PR feedback**

Before Ready-for-review, verify:

- no Critical or Important findings remain;
- no unresolved review threads;
- no unaddressed review comments;
- PR is mergeable;
- exact PR head equals the green CI SHA;
- production diff is limited to `cards.py` duplicate deletion and `runtime_loader.py` allowlist retirement.

If no independent reviewer/subagent capability exists in the harness, perform the Superpowers fallback whole-branch self-review and explicitly record that limitation.

- [ ] **Step 15: Mark PR Ready for review**

Only after Steps 13–14 pass.

Do not merge the PR. Merge remains a separate user decision.

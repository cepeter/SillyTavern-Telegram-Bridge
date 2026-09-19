# Phase 5A Group Director Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract Group Director decision and prompt coordination into an injected ordinary-import `GroupDirectorService` while preserving all current behavior.

**Architecture:** The new service owns Director decision parsing, model invocation, policy application, fallback, and speaker prompt context. `groups.py` retains same-signature compatibility adapters, while startup injects one production service through `BridgeServices` and the message-worker path explicitly propagates that service graph.

**Tech Stack:** Python 3.11, dataclasses, sqlite3, unittest/pytest, existing extension registry and composition root.

**Spec:** `docs/superpowers/specs/2026-09-20-group-director-service-design.md`

## Global Constraints

- Preserve user-visible Group Director and Director Goals behavior.
- Add no service locator or mutable global service registry.
- Add no public-callable runtime override.
- Keep `group_director_service.py` outside `DEFAULT_RUNTIME_STAGES`.
- Keep `director_goals.py` as the transitional policy provider in this slice.
- Existing direct `group_director_plan()` and `group_prompt_context()` callers must remain compatible.
- Production live and recovered message paths must use the startup-injected service.
- Full repository CI must pass before the PR is ready.

## Review Focus

- Forced-speaker mode must bypass policy lookup and model generation exactly as before.
- Policy exceptions and malformed policy fields must preserve current fallback/model/token behavior.
- Invalid Director model output or generation failure must remain deterministic round-robin fallback.
- Direct legacy callers without `BridgeServices` must preserve current behavior through compatibility adapters.
- Recovery-wrapped `process_message` must propagate the injected service graph without altering operation recovery semantics.

---

### Task 1: Add ordinary-import GroupDirectorService

**Files:**
- Create: `bridge/group_director_service.py`
- Create: `tests/test_group_director_service.py`

**Interfaces:**
- Consumes injected group state, character, generation, settings, and policy callables.
- Produces `GroupDirectorService.plan(...)` and `GroupDirectorService.prompt_context(...)`.

- [ ] **Step 1: Write failing service tests**

Create tests that instantiate the service with fakes and prove:
- known-speaker decision succeeds,
- invalid output falls back round-robin,
- generation exception falls back round-robin,
- forced speaker bypasses policy/generation,
- bounded policy model/token customization is applied,
- prompt context appends policy speaker context.

- [ ] **Step 2: Run the new test module**

Run:

```bash
python -m unittest tests.test_group_director_service -v
```

Expected: FAIL because `bridge.group_director_service` does not exist.

- [ ] **Step 3: Implement GroupDirectorService**

Move the behavior currently implemented by `parse_group_director_decision`, `group_director_plan`, and Director-specific `group_prompt_context` logic into the service. Use only explicit constructor collaborators and standard-library imports.

- [ ] **Step 4: Run the new test module**

Run:

```bash
python -m unittest tests.test_group_director_service -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Commit message:

```text
refactor: extract Group Director application service
```

---

### Task 2: Convert groups.py Director functions into compatibility adapters

**Files:**
- Modify: `bridge/groups.py`
- Modify: `tests/test_group_director.py`

**Interfaces:**
- Consumes `GroupDirectorService` from Task 1.
- Preserves existing public function signatures for `group_director_plan` and `group_prompt_context`.

- [ ] **Step 1: Add source-boundary regression**

Add a test asserting `groups.py` no longer contains the Director model prompt/decision implementation and delegates through `GroupDirectorService`.

- [ ] **Step 2: Run Group Director compatibility tests**

Run:

```bash
python -m unittest tests.test_group_director -v
```

Expected: the new source-boundary test FAILS before adapter conversion.

- [ ] **Step 3: Replace Director implementation with thin adapters**

Import `GroupDirectorService` privately and construct a compatibility instance from the current runtime collaborators. Delegate both public functions to the service.

- [ ] **Step 4: Run service + compatibility tests**

Run:

```bash
python -m unittest tests.test_group_director_service tests.test_group_director -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Commit message:

```text
refactor: delegate Group Director compatibility API
```

---

### Task 3: Inject GroupDirectorService through BridgeServices

**Files:**
- Modify: `bridge/composition.py`
- Modify: `bridge/main.py`
- Modify: `bridge/message_commands.py`
- Modify: `bridge/recovery.py`
- Modify: `tests/test_composition.py`
- Modify: `tests/test_runtime_loader.py`

**Interfaces:**
- Consumes `GroupDirectorService` from Task 1 and existing Phase 4 `BridgeServices`.
- Produces startup-injected `services.group_director` and explicit message-workflow propagation.

- [ ] **Step 1: Replace the Phase 4 negative service guard**

Change the Phase 4 assertion that forbids `GroupDirectorService` into positive tests proving:
- `BridgeServices` carries the service,
- startup builder constructs it,
- `process_message_job` passes the same `services` object to `process_message`,
- `group_director_service.py` is not a runtime stage.

- [ ] **Step 2: Run focused composition tests**

Run:

```bash
python -m unittest tests.test_composition tests.test_runtime_loader -v
```

Expected: FAIL before production injection changes.

- [ ] **Step 3: Extend BridgeServices and startup builder**

Add `group_director` to `BridgeServices` and `build_bridge_services()`. Construct `GroupDirectorService` in `_build_startup_services()` from existing runtime collaborators and the extension-registry policy getter.

- [ ] **Step 4: Propagate services through process_message**

Add optional keyword-only `services=None` to the compatibility `process_message` definitions in `message_commands.py` and `recovery.py`. Production `process_message_job` passes `services=services`; the recovery wrapper forwards it to the original implementation.

- [ ] **Step 5: Use injected service on production message path**

In `message_commands.py`, use `services.group_director.plan()` and `.prompt_context()` when supplied. Retain compatibility-adapter fallback for direct callers without services.

- [ ] **Step 6: Run focused tests**

Run:

```bash
python -m unittest tests.test_composition tests.test_runtime_loader tests.test_group_director_service tests.test_group_director -v
```

Expected: PASS.

- [ ] **Step 7: Run message/recovery regressions**

Run:

```bash
python -m unittest tests.test_generation_continuation tests.test_session_command_routing tests.test_group_turn_gating -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

Commit message:

```text
refactor: inject Group Director service into message workflow
```

---

### Task 4: Final Phase 5A verification and PR preparation

**Files:**
- Verify all Phase 5A production/test files.
- Modify only for verified Critical/Important review findings.

**Interfaces:**
- Consumes Tasks 1–3.
- Produces a PR-ready Phase 5A branch.

- [ ] **Step 1: Compile sources**

Run:

```bash
python -m compileall bridge tests
```

Expected: exit 0.

- [ ] **Step 2: Run architecture and director regressions**

Run:

```bash
python -m unittest tests.test_group_director_service tests.test_group_director tests.test_composition tests.test_runtime_loader -v
```

Expected: PASS.

- [ ] **Step 3: Run complete unittest suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 4: Run pytest and dependency audit**

Run:

```bash
pytest
python -m pip check
pip-audit
```

Expected: every command exits 0.

- [ ] **Step 5: Review source boundaries**

Verify:
- one Director workflow implementation owner,
- no new `_ORIGINAL_*` chain,
- no new runtime override,
- no `bridge.runtime` import in `group_director_service.py` or `composition.py`,
- no service locator,
- ordinary service module absent from runtime stages.

- [ ] **Step 6: Whole-branch review**

Use Superpowers `requesting-code-review`. Any Critical/Important finding gets a regression test, RED verification, minimal fix, and GREEN/full-suite verification.

- [ ] **Step 7: Fresh exact-head CI**

The Phase 5A PR remains draft until exact-head CI is successful.

- [ ] **Step 8: Prepare PR**

PR summary must state:
- synchronized baseline `538ac6e`,
- ordinary-import `GroupDirectorService`,
- compatibility adapters preserved,
- injected startup/message path,
- Director Goals policy behavior preserved,
- compatibility runtime intentionally retained for later phases.

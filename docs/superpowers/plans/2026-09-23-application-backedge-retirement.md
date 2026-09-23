# Application Back-Edge Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Retire the coordinated `persona_sync -> input_flows`, `telegram -> session_naming`, and `telegram -> commands` back edges while preserving behavior and splitting the merged-main 27-module SCC.

**Architecture:** Move Persona serialization ownership into the native Persona adapter, move session-title validation to a pure owner, and move image application orchestration above Telegram transport. Extend the existing injected Telegram runtime only with file download, and inject document PNG image processing explicitly.

**Tech Stack:** Python 3.11+, sqlite3, dataclasses, pytest/pytest-xdist, AST import-boundary tests.

**Spec:** `docs/superpowers/specs/2026-09-23-application-backedge-retirement-design.md`

## Global Constraints

- Base lineage is merged `main` at `461d1525ecff673024987bc3be533c442aff1c23`.
- Preserve user-visible commands, messages, durable jobs, recovery, and persistence semantics.
- No compatibility aliases, dynamic imports, service locator, or new global registry.
- No GroupService, ModelRouter, route-update decomposition, or streaming-continuation fix in this PR.
- Re-measure the graph after merge before choosing the next architecture work.

## Review Focus

1. A failed/oversized image job must not download or invoke provider generation unexpectedly.
2. A queued image must keep its queued session/model/message identity when moved out of `telegram.py`.
3. A non-character PNG document must use the configured API key, not ambient `LLM_API_KEY`.
4. Persona native-store serialization must use the same re-entrant lock as the composed PersonaService.
5. Session-title normalization must preserve whitespace folding, slash rejection, max length, exception type, and message.

---

### Task 1: Move Persona lock ownership

**Files:**
- Modify: `bridge/persona_sync.py`
- Modify: `bridge/input_flows.py`
- Modify: `bridge/main.py`
- Modify: `tests/application_test_setup.py`
- Create: `tests/test_application_backedge_retirement.py`

**Interfaces:**
- Produces: `bridge.persona_sync.PERSONA_EDIT_LOCK: threading.RLock`
- Preserves: `_PERSONA_STORE` and `PersonaService` receive the same lock object.

- [x] **Step 1: Write failing architecture and identity tests**

Add tests that parse imports and assert:

```python
assert "bridge.input_flows" not in imported_modules("persona_sync.py")
assert not hasattr(input_flows, "PERSONA_EDIT_LOCK")
assert persona_sync._PERSONA_STORE.edit_lock() is persona_sync.PERSONA_EDIT_LOCK
```

Also patch/inspect startup or native test composition so the injected PersonaService lock is the same object.

- [x] **Step 2: Run the focused RED tests**

Run:
`python -m pytest -q tests/test_application_backedge_retirement.py -k persona`

Expected: failure because `persona_sync` imports the lock from `input_flows` and `input_flows` owns it.

- [x] **Step 3: Move the lock**

Create `PERSONA_EDIT_LOCK = threading.RLock()` in `persona_sync.py`, remove the UI-owned lock, and update `main.py` / test composition to import from `persona_sync`.

- [x] **Step 4: Run GREEN tests**

Run:
`python -m pytest -q tests/test_application_backedge_retirement.py -k persona tests/test_persona_service.py tests/test_persona_integrity_adapter.py`

Expected: all pass.

- [x] **Step 5: Commit**

Commit message: `refactor: move persona serialization lock to native store`

---

### Task 2: Extract pure session-title validation

**Files:**
- Create: `bridge/session_titles.py`
- Modify: bridge/session_naming.py`
- Modify: bridge/telegram.py`
- Modify: tests/test_session_naming.py
- Modify: tests/test_application_backedge_retirement.py

**Interfaces:**
- Produces: `SESSION_TITLE_MAX_CHARS = 80`
- Produces: `normalize_session_title(value: str) -> str`
- `session_naming.py` must not re-export either symbol.

- [x] **Step 1: Write failing ownership tests**

Assert the pure module exists, `telegram` no longer imports `session_naming`, and the old owner does not define/re-export normalization.

Behavior assertions:

```python
assert normalize_session_title(" A   name ") == "A name"
with pytest.raises(ValueError, match="Session name must contain 1–80"):
    normalize_session_title("x" * 81)
with pytest.raises(ValueError):
    normalize_session_title("/bad")
```

- [x] **Step 2: Run RED**

Run:
`python -m pytest -q tests/test_application_backedge_retirement.py -k session_title tests/test_session_naming.py`

Expected: ownership tests fail before the pure module exists.

- [x] **Step 3: Move implementation without behavior changes**

Move the constant/function verbatim to `bridge/session_titles.py`. Import it from both consumers. Update tests to canonical ownership; do not add aliases.

- [x] **Step 4: Run GREEN**

Run:
`python -m pytest -q tests/test_application_backedge_retirement.py -k session_title tests/test_session_naming.py tests/test_character_session_chain.py`

Expected: all pass.

- [x] **Step 5: Commit**

Commit message: `refactor: extract pure session title validation`

---

### Task 3: Retire Telegram-to-commands image orchestration

**Files:**
- Modify: `bridge/composition.py`
- Modify: bridge/main.py`
- Modify: `bridge/worker_orchestration.py`
- Modify: bridge/telegram.py`
- Modify: bridge/help.py`
- Modify: affected test service constructors in `tests/`
- Modify: `tests/test_job_service_workers.py`
- Modify: `tests/test_memory_service.py`
- Modify: `tests/test_composition.py`
- Modify: `tests/test_application_backedge_retirement.py`

**Interfaces:**
- `TelegramRuntime.download_file: Callable[..., bytes]` is required.
- `worker_orchestration.process_image_job()` downloads through that port and calls canonical `commands.process_image_message`.
- `telegram.process_telegram_image` is deleted.
- `telegram.import_telegram_document(..., api_key: str, process_image: Callable[..., None], ...)` receives the PNG fallback collaborator explicitly.

- [x] **Step 1: Write failing architecture tests**

Assert:

```python
assert "bridge.commands" not in imported_modules("telegram.py")
assert not hasattr(telegram, "process_telegram_image")
assert inspect.signature(build_bridge_services).parameters["telegram"].default is inspect.Parameter.empty
assert "download_file" in TelegramRuntime.__dataclass_fields__
```

- [x] **Step 2: Write failing image-worker behavior tests**

Use a fake `TelegramRuntime.download_file` and patch canonical `worker_orchestration.process_image_message`.

Cover:
- oversize file sends the current size-limit message and performs no download;
- valid file downloads once;
- queued session and message ID are preserved;
- configured API key/model and injected memory/persona/director services are forwarded;
- committed response recovery returns before download/provider work.

- [x] **Step 3: Run image-worker RED**

Run:
`python -m pytest -q tests/test_application_backedge_retirement.py -k "telegram or image" tests/test_job_service_workers.py`

Expected: failures because the runtime lacks download and the worker delegates to `telegram.process_telegram_image`.

- [x] **Step 4: Implement image orchestration move**

Add `download_file` to `TelegramRuntime`, compose it from `download_telegram_file`, and update all explicit test constructors.

Update `process_image_job()` to:
1. preserve job/recovery gates;
2. reject `file_size > IMAGE_MAX_BYTES`;
3. download through `services.telegram.download_file`;
4. resolve session;
5. load character fields;
6. call canonical `process_image_message`.

Delete `telegram.process_telegram_image` and the `telegram -> commands` import.

- [x] **Step 5: Write/verify document PNG fallback RED**

Before changing the document call, add tests that pass a sentinel `api_key` and fake image collaborator to `import_telegram_document`; assert a non-character PNG forwards both, while character-card PNG and non-PNG Data Bank branches do not call the collaborator.

Run:
`python -m pytest -q tests/test_application_backedge_retirement.py -k document`

Expected: failure because the current function imports/calls the global command and reads ambient API key.

- [x] **Step 6: Implement explicit document collaborator**

Add required `api_key` and `process_image` keyword parameters to `import_telegram_document`. Have `help.process_document_job()` pass `services.config.api_key` and canonical `process_image_message`. Remove the ambient environment lookup for this branch.

- [x] **Step 7: Run focused GREEN suite**

Run:
`python -m pytest -q tests/test_application_backedge_retirement.py tests/test_job_service_workers.py tests/test_memory_service.py tests/test_composition.py tests/test_session_naming.py`

Expected: all pass.

- [x] **Step 8: Commit**

Commit message: `refactor: move image orchestration above telegram transport`

---

### Task 4: Architecture verification and exact-head delivery

**Files:**
- Modify: `docs/superpowers/plans/2026-09-23-application-backedge-retirement.md`
- Modify only if required by actual ownership changes: architecture guard tests.

**Interfaces:**
- No new production API beyond the interfaces from Tasks 1–3.

- [x] **Step 1: Run graph measurement**

Run the current AST graph scanner against the branch and merged-main base.

Expected:
- the three target edges are absent;
- largest SCC `27 -> <=12`;
- no newly cyclic module;
- `session_titles` remains acyclic.

- [x] **Step 2: Run fresh-process import checks**

Import `persona_sync`, `input_flows`, `session_titles`, `session_naming`, `telegram`, `commands`, `help`, `worker_orchestration`, `composition`, and `main` in separate Python processes.

Expected: all imports succeed.

- [x] **Step 3: Run compile/whitespace checks**

Run:
`python -m compileall -q bridge tests`
and
`git diff --check`

Expected: exit 0.

- [x] **Step 4: Run the full suite on the exact branch tree**

Run:
`python -m pytest -q -n 2 --dist=loadfile`

Expected: zero failures. Record test/subtest counts and exit status.

- [x] **Step 5: Self-review the full diff**

Review against the spec with emphasis on the five Review Focus conditions. Any Critical/Important finding gets one TDD fix pass before publication. Record deferred minors.

- [x] **Step 6: Commit verification documentation**

Update this plan's local verification record and commit any documentation-only verification changes.

- [ ] **Step 7: Publish PR and verify exact-head CI**

Push the branch to the fork, open a PR to upstream `main`, and verify both GitHub `test` and `dependency-audit` jobs on the exact head.

- [ ] **Step 8: Merge and re-scan**

Because the user authorized completion through merge, merge only after exact-head CI is green. Fetch merged `main`, rerun the graph scanner, and select the next architecture slice from the new graph rather than this plan.


## Local Verification Record

- Base commit: 461d1525ecff673024987bc3be533c442aff1c23.
- TDD Persona ownership RED: 2 failed / 1 passed; GREEN: 39 passed + 6 subtests.
- TDD session-title ownership RED: 2 failed; GREEN focused: 15 passed.
- TDD image boundary RED: 4 failed / 2 passed; GREEN focused image checks: 4 passed.
- TDD document collaborator RED: 4 failed; GREEN: 4 passed.
- Review typing fix: document image callable contract RED to GREEN.
- Combined focused migration suite: 91 passed + 45 subtests.
- Full post-review suite: 777 passed + 476 subtests, exit 0.
- compileall: passed.
- git diff --check: passed.
- Fresh-process imports passed for Persona, input, session-title, Telegram,
  command, document, worker, composition, and main modules.
- Import graph: 71 to 72 modules, 420 to 419 edges, largest SCC 27 to 12,
  cyclic modules 27 to 23, reciprocal pairs 18 to 16.
- No previously acyclic module became cyclic; session_titles remains acyclic.
- Review: self-review using the Superpowers code-reviewer rubric; no remaining
  Critical, Important, or Minor findings after one TDD fix pass.

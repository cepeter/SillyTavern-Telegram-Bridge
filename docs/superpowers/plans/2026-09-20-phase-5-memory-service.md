# Phase 5B Memory Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract prompt-memory orchestration, retention, and purge delegation into an injected `MemoryService` while preserving the staged Hindsight safety/curation stack.

**Architecture:** Add an ordinary-import application service with explicit callable collaborators. Inject it through `BridgeServices`, use it in normal and recovery generation paths, and keep legacy direct-call fallbacks until later migration phases.

**Tech Stack:** Python 3, dataclasses, SQLite, unittest/pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-20-memory-service-design.md`

## Global Constraints

- Preserve current Hindsight recall, retention, purge, summary, stale-retain, Memory Curator, and Scene State behavior.
- Do not add `memory_service.py` to runtime stages.
- Do not introduce a service locator or a new override.
- Keep direct legacy callers working without `BridgeServices`.
- Keep Phase 6 backend-decorator work out of this slice.

## Review Focus

- Edited-turn regeneration must not reuse or regenerate a summary that already covers the edited row.
- Production retention must still resolve to the state-integrity guarded implementation.
- `/retry` and recovery helpers must not drop the injected memory service.
- Direct runtime tests that monkeypatch memory globals must still work.
- Memory Curator post-retain hooks must still run through the injected production retain provider.

---

### Task 1: Ordinary MemoryService

**Files:**
- Create: `bridge/memory_service.py`
- Create: `tests/test_memory_service.py`

**Interfaces:**
- Consumes: injected recall, summary, summary-state, retain, and purge callables.
- Produces: `MemoryPromptContext`, `MemoryService.prompt_context()`, `retain()`, and `purge_session()`.

- [ ] Write tests for normal prompt context, edit-covered suppression, edit-not-covered summary, retain delegation, and purge delegation.
- [ ] Run the tests and verify RED because `bridge.memory_service` does not exist.
- [ ] Implement the minimal ordinary-import service.
- [ ] Run the service tests and verify GREEN.

### Task 2: Composition root injection

**Files:**
- Modify: `bridge/composition.py`
- Modify: `bridge/main.py`
- Modify: `tests/test_composition.py`

**Interfaces:**
- Consumes: `MemoryService` from Task 1 and staged runtime memory collaborators.
- Produces: `BridgeServices.memory` and production startup construction.

- [ ] Add failing tests asserting `BridgeServices` exposes `memory` and startup builds a `MemoryService`.
- [ ] Verify RED.
- [ ] Add the optional memory field and builder parameter.
- [ ] Construct the production service in `_build_startup_services()`.
- [ ] Update the Phase 5 extraction guard to allow GroupDirectorService + MemoryService while still forbidding PersonaService, SyncService, and JobService.
- [ ] Verify GREEN.

### Task 3: Normal message workflow

**Files:**
- Modify: `bridge/message_commands.py`
- Modify: `tests/test_composition.py` or a focused integration test.

**Interfaces:**
- Consumes: `services.memory`.
- Produces: injected prompt-context and retention usage for ordinary generation.

- [ ] Add a failing test that supplies a fake memory service and proves prompt assembly receives its recall/summary and post-persist retain is invoked.
- [ ] Verify RED.
- [ ] Thread the optional memory service into `generate_and_store_reply()`.
- [ ] Use service context/retain when present and legacy functions otherwise.
- [ ] Verify the focused test and existing direct-call tests GREEN.

### Task 4: Recovery workflow

**Files:**
- Modify: `bridge/recovery.py`
- Modify: focused recovery/composition tests.

**Interfaces:**
- Consumes: injected `MemoryService`.
- Produces: service-backed context/retention for regen, continue, and edited-turn regeneration.

- [ ] Add failing tests or source-boundary assertions proving recovery receives and uses the injected service.
- [ ] Add a failing edited-turn test proving `edited_user_rowid` reaches the service and legacy summary branching is not duplicated.
- [ ] Verify RED.
- [ ] Add optional memory-service parameters to recovery helpers and propagate from the recovery `process_message` wrapper.
- [ ] Use service context/retain with legacy fallbacks.
- [ ] Verify recovery and state-integrity tests GREEN.

### Task 5: Image generation boundary

**Files:**
- Modify: `bridge/commands.py`
- Modify: `bridge/telegram.py`
- Modify: `bridge/main.py`
- Modify: `bridge/help.py`
- Modify: focused memory/composition tests.

**Interfaces:**
- Consumes: `services.memory` from image/document workers.
- Produces: service-backed prompt context and retention for photo and PNG-as-image generation.

- [x] Add failing tests for the image worker, document worker, Telegram adapters, and image generation owner.
- [x] Verify RED on all five missing service boundaries.
- [x] Propagate the optional service through image/document adapters.
- [x] Use service context/retain in `process_image_message()` with legacy fallback.
- [x] Verify full CI GREEN.

### Task 6: Runtime boundary and full verification

**Files:**
- Modify: `tests/test_runtime_loader.py`
- Modify docs only if implementation rulings require clarification.

**Interfaces:**
- Consumes: completed Phase 5B implementation.
- Produces: runtime-stage regression guard and release evidence.

- [ ] Add a regression assertion that `memory_service.py` is not a runtime stage.
- [ ] Run targeted memory/state-integrity/curator tests.
- [ ] Run the complete repository CI suite.
- [ ] Inspect the whole branch diff for Critical/Important issues.
- [ ] Fix any Important findings with a RED→GREEN regression test.
- [ ] Re-run exact-head CI before opening the PR.

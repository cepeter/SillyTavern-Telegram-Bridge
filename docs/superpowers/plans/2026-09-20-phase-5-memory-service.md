# Phase 5B Memory Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract prompt-memory orchestration, retention, and purge delegation into an injected `MemoryService` while preserving the staged Hindsight safety/curation stack.

**Architecture:** Add an ordinary-import application service with explicit callable collaborators. Inject it through `BridgeServices` and route legacy direct callers through a late-bound compatibility `MemoryService`, so application workflows never call backend memory functions directly.

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

- [x] Write tests for normal prompt context, edit-covered suppression, edit-not-covered summary, retain delegation, purge delegation, and summary status.
- [x] Run the tests and verify RED because `bridge.memory_service` does not exist.
- [x] Implement the minimal ordinary-import service.
- [x] Run the service tests and verify GREEN.

### Task 2: Composition root injection

**Files:**
- Modify: `bridge/composition.py`
- Modify: `bridge/main.py`
- Modify: `tests/test_composition.py`

**Interfaces:**
- Consumes: `MemoryService` from Task 1 and staged runtime memory collaborators.
- Produces: `BridgeServices.memory` and production startup construction.

- [x] Add failing tests asserting `BridgeServices` exposes `memory` and startup builds a `MemoryService`.
- [x] Verify RED.
- [x] Add the optional memory field and builder parameter.
- [x] Construct the production service in `_build_startup_services()`.
- [x] Update the Phase 5 extraction guard to allow GroupDirectorService + MemoryService while still forbidding PersonaService, SyncService, and JobService.
- [x] Verify GREEN.

### Task 3: Normal message workflow

**Files:**
- Modify: `bridge/message_commands.py`
- Modify: `tests/test_composition.py` or a focused integration test.

**Interfaces:**
- Consumes: `services.memory`.
- Produces: injected prompt-context and retention usage for ordinary generation.

- [x] Add a failing test that supplies a fake memory service and proves prompt assembly receives its recall/summary and post-persist retain is invoked.
- [x] Verify RED.
- [x] Thread the optional memory service into `generate_and_store_reply()`.
- [x] Route both injected and direct legacy callers through `MemoryService`.
- [x] Verify the focused test and existing direct-call tests GREEN.

### Task 4: Recovery workflow

**Files:**
- Modify: `bridge/recovery.py`
- Modify: focused recovery/composition tests.

**Interfaces:**
- Consumes: injected `MemoryService`.
- Produces: service-backed context/retention for regen, continue, and edited-turn regeneration.

- [x] Add failing tests or source-boundary assertions proving recovery receives and uses the injected service.
- [x] Add a failing edited-turn test proving `edited_user_rowid` reaches the service and legacy summary branching is not duplicated.
- [x] Verify RED.
- [x] Add optional memory-service parameters to recovery helpers and propagate from the recovery `process_message` wrapper.
- [x] Route recovery through the injected or compatibility `MemoryService` with no direct backend calls.
- [x] Verify recovery and state-integrity tests GREEN.

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

### Task 6: Review feedback — unify compatibility boundary

**Files:**
- Modify: `bridge/memory.py`
- Modify: `bridge/memory_service.py`
- Modify: `bridge/commands.py`
- Modify: `bridge/generation.py`
- Modify: `bridge/message_commands.py`
- Modify: `bridge/recovery.py`
- Modify: `bridge/telegram.py`
- Modify: focused memory tests.

- [x] Add RED source-boundary tests for the four files identified by review.
- [x] Add RED tests for compatibility service late binding and reset purge delegation.
- [x] Add a late-bound compatibility `MemoryService` adapter in `memory.py`.
- [x] Remove direct recall/summary/retain/purge/summary-state calls from reviewed application files.
- [x] Route reset and inactive-session deletion through `MemoryService.purge_session()`.
- [x] Preserve legacy direct callers and state-integrity overrides.
- [x] Verify GREEN on exact-head CI.

### Task 7: Runtime boundary and full verification

**Files:**
- Modify: `tests/test_runtime_loader.py`
- Modify docs only if implementation rulings require clarification.

**Interfaces:**
- Consumes: completed Phase 5B implementation.
- Produces: runtime-stage regression guard and release evidence.

- [x] Add a regression assertion that `memory_service.py` is not a runtime stage.
- [x] Run targeted memory/state-integrity/curator tests through CI.
- [x] Run the complete repository CI suite.
- [x] Inspect the whole branch diff for Critical/Important issues.
- [x] Fix Important findings with RED→GREEN regression tests.
- [x] Re-run exact-head CI before marking the PR Ready for review.

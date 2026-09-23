# GroupService Application Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Complete the required GroupService application boundary and remove direct group-core dependencies from application/UI modules.

**Architecture:** Add a pure injected GroupService delegating to canonical group-core backends. Compose it once in `main.py`, migrate conversation, worker, routing, group UI, session naming, and status/prompt consumers, and leave `sync_api.py` as the explicit backend exception.

**Tech Stack:** Python 3.11+, sqlite3, dataclasses, pytest/pytest-xdist, AST architecture guards.

**Spec:** `docs/superpowers/specs/2026-09-23-group-service-boundary-design.md`

## Global Constraints

- Base is merged `main` at `ff4420d5fb622a948eafbe725cebd9ce63f70206`.
- Preserve group commands, panels, manual-turn gating, generation, recovery,
  session setup, and persistence semantics.
- No optional service fallback, compatibility wrapper, service locator, or dynamic import.
- No provider/ModelRouter rewrite, route-update decomposition, broad transaction cleanup, or streaming fix.
- Direct `group_core` imports may remain only in `main.py` and `sync_api.py`.

## Review Focus

1. Manual-mode messages must still be rejected before queueing when another user owns the turn.
2. A committed/recovered group reply must advance the speaker exactly once.
3. Image replies must select/advance the same group speaker as text replies.
4. Group-session creation from pending name input must preserve session identity and default group state.
5. Group UI callbacks must use the injected GroupService instance, never an ambient backend.

---

### Task 1: Introduce pure GroupService and required composition

**Files:** create `bridge/group_service.py`, modify composition/main/test setup,
create `tests/test_group_service_boundary.py`, update explicit BridgeServices fixtures.

**Interfaces:** produces GroupService methods from the spec and required
`BridgeServices.group: GroupService`.

- [x] **Step 1:** Write failing tests for pure imports, delegation, required composition, and startup binding.
- [x] **Step 2:** Run `python -m pytest -q tests/test_group_service_boundary.py -k "service or composition"`; expect missing module/field failures.
- [x] **Step 3:** Implement GroupService, compose from group_core, use group methods for GroupDirector construction, add test factory.
- [x] **Step 4:** Run GroupService/composition focused tests; expect all pass.
- [x] **Step 5:** Commit `refactor: add required group application service`.
### Task 2: Migrate conversation, image, worker, and update routing

**Files:** modify `conversation_service.py`, `message_commands.py`,
`commands.py`, `worker_orchestration.py`, `update_routing.py`, affected tests.

**Interfaces:** `generate_and_store_reply(..., group_service: GroupService, ...)`;
`process_image_message(..., group_service: GroupService, ...)`; other paths use
`services.group`.

- [x] **Step 1:** Write failing forwarding tests for text/image speaker selection,
  turn advancement, committed recovery, and manual-turn routing.
- [x] **Step 2:** Run boundary tests filtered to conversation/image/recovery/update;
  expect direct group-core or missing-service failures.
- [x] **Step 3:** Replace direct group-core calls with required service parameters
  and `services.group`; update ConversationService and image worker forwarding.
- [x] **Step 4:** Run focused conversation/worker/image/update suites.
- [x] **Step 5:** Commit `refactor: route conversation group policy through service`.

---

### Task 3: Migrate Group UI, callbacks, pending input, and status panels

**Files:** modify `groups.py`, `command_routes.py`, `callback_dispatch.py`,
`panel_callback_routes.py`, `input_flows.py`, `session_naming.py`,
`status_panels.py`, and affected tests.

**Interfaces:** Group UI functions receive required
`group_service: GroupService`; pending/session-name/status/prompt paths propagate it.

- [x] **Step 1:** Add failing architecture tests that listed application/UI modules
  do not import group_core, plus behavior tests for group menu/command, session naming,
  and status/prompt through a fake service.
- [x] **Step 2:** Run focused group/panel/session/status tests plus boundary tests;
  expect import/signature failures.
- [x] **Step 3:** Propagate required GroupService through call chains and remove
  direct group-core imports.
- [x] **Step 4:** Run focused group, callback, panel, session naming, prompt/status suites.
- [x] **Step 5:** Commit `refactor: route group UI through application service`.
### Task 4: Architecture verification, PR, merge, and re-scan

**Files:** update this plan with verification record; architecture guards only if needed.

- [x] **Step 1:** AST scan must show group_core production importers are exactly
  `main.py` and `sync_api.py`; group_service imports no bridge modules.
- [x] **Step 2:** Run graph measurement and fresh-process imports of affected modules.
- [x] **Step 3:** Run compileall, `git diff --check`, and full
  `pytest -q -n 2 --dist=loadfile`.
- [x] **Step 4:** Whole-branch self-review against spec and Review Focus; one TDD
  fix pass for Critical/Important findings.
- [ ] **Step 5:** Publish exact head; require GitHub `test` and
  `dependency-audit` success.
- [ ] **Step 6:** Merge after CI green, fetch actual main, re-scan graph, and choose
  the ModelRouter/provider or other next slice from new evidence.


## Local Verification Record

- Base: ff4420d5fb622a948eafbe725cebd9ce63f70206.
- Task 1 service/composition RED: 4 failed; GREEN: 47 passed + 30 subtests.
- Task 2 forwarding/recovery focused: 73 passed + 19 subtests after canonical-owner migration.
- Task 3 UI/session/panel RED: 4 boundary failures; focused GREEN: 79 passed + 12 subtests.
- First full-suite migration run exposed 55 stale test ownership assumptions; no compatibility exports were restored.
- Canonicalized legacy test fixtures and reran full suite: 788 passed + 476 subtests, exit 0.
- group_core production importers are exactly main.py and sync_api.py.
- group_service.py imports no bridge modules and remains acyclic.
- Graph remains structurally neutral: largest SCC 12, cyclic modules 23, reciprocal pairs 16.
- Fresh-process imports, compileall, and git diff --check passed.
- Whole-branch review: self-review (no independent subagent tool); no Critical,
  Important, or Minor findings after verifying the five Review Focus behaviors.

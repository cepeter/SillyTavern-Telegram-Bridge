# Runtime Context Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ambient thread-local request state with explicit database/session/actor ownership, then delete `bridge/runtime_context.py`.

**Architecture:** Retire the ambient state in reviewable slices. First make panel-close persistence consume the caller-owned SQLite connection. Next make panel-send binding consume an explicit request context carrying database, session, and actor. After all consumers are explicit, delete `runtime_context.py`, fold its remaining one-constant companion `runtime_defaults.py` into the canonical schema owner, and only then decompose `bridge.main`.

**Tech Stack:** Python 3.11, sqlite3, unittest/pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-19-composition-root-runtime-context-design.md`

## Global Constraints

- Preproduction: do not preserve legacy/compatibility APIs.
- No service locator or replacement global context.
- Preserve panel owner/session security and durable job recovery.
- TDD for each behavior-changing slice.
- Each PR must be independently green and reviewable.

## Review Focus

- Panel close must mutate the caller-owned DB, never open a hidden second connection.
- Panel send must bind the correct session and actor after explicit-context migration.
- Callback panels must remain owner/session scoped.
- Recovered jobs must carry the same actor/session semantics as live jobs.
- Removing ambient state must not break injected temporary DB tests.

---

### Task 1: Explicit panel-close database ownership

**Files:**
- Modify: `bridge/callbacks.py`
- Modify callers: `bridge/help_details.py`, `bridge/panel_callback_routes.py`, `bridge/status_panels.py`, `bridge/input_flows.py`
- Test: `tests/test_panel_context_ownership.py`

**Interfaces:**
- Consumes: caller-owned `sqlite3.Connection`.
- Produces: `close_panel_message(db, token, chat_id, callback)`.

- [ ] **Step 1: Write the failing test**

```python
def test_close_panel_uses_explicit_database():
    callbacks.close_panel_message(db, "token", "chat", callback)
    assert panel_binding_is_deleted(db)
```

Patch `callbacks.db_connect` to raise so hidden connection discovery cannot satisfy the test.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_panel_context_ownership.py`

Expected: FAIL because `close_panel_message` does not yet accept `db`.

- [ ] **Step 3: Write minimal implementation**

Change the signature to require `db`, delete the `db_connection_context`/fallback connection logic, and update every caller to pass its existing DB argument.

- [ ] **Step 4: Run focused and full tests**

Run:
```text
python -m pytest -q tests/test_panel_context_ownership.py
python -m pytest -q -n auto --maxprocesses=4 --dist=loadfile
```

Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `refactor: make panel close database ownership explicit`.

### Task 2: Explicit panel-send request context

**Files:**
- Modify: `bridge/composition.py`, `bridge/telegram.py`, panel/menu modules and routing callers.
- Test: new explicit panel-binding context tests.

**Interfaces:**
- Produces immutable request context with `db`, `session_id`, and `actor_id`.
- `telegram_request` must not read thread-local state.

- [ ] Add RED tests proving explicit DB/session/actor binding.
- [ ] Thread context through message/callback routes and panel send helpers.
- [ ] Remove panel session/actor ambient reads from `telegram.py`.
- [ ] Run exact-head full CI.

### Task 3: Delete ambient runtime context

**Files:**
- Delete: `bridge/runtime_context.py`
- Update remaining imports/tests.

- [ ] Add source guard asserting no production import of `bridge.runtime_context`.
- [ ] Delete final setters/getters and module.
- [ ] Verify full suite and dependency audit.

### Task 4: Fold one-constant runtime defaults

**Files:**
- Delete: `bridge/runtime_defaults.py`
- Modify: `bridge/schema.py`
- Update: runtime import-boundary tests.

- [ ] Move the 30-day processed-update retention constant to schema ownership.
- [ ] Remove the one-constant compatibility-era module.
- [ ] Verify full suite.

### Task 5: Decompose the composition root

**Files:**
- Split focused startup/lifecycle, update routing, and worker/recovery orchestration from `bridge/main.py`.

- [ ] Preserve explicit `BridgeServices` construction.
- [ ] Move Telegram update classification/routing out of `main()`.
- [ ] Move durable worker/recovery orchestration to a focused owner.
- [ ] Keep `main()` as the small composition/lifecycle entrypoint.
- [ ] Verify full suite after each extraction.

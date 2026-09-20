# Phase 5C PersonaService Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Extract Persona lifecycle orchestration into an injected `PersonaService` while preserving native SillyTavern persistence behavior and the existing state-integrity safety overrides.

**Architecture:** Add an ordinary-import `PersonaService` with explicit store/session/repository collaborators. Production receives it through `BridgeServices`; legacy direct callers resolve a late-bound compatibility service so both paths cross one application boundary. Native settings, backup/readback, avatar handling, and serialized integrity wrappers remain infrastructure and are not rewritten in Phase 5C.

**Tech Stack:** Python 3.11, dataclasses, SQLite, unittest/pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-20-persona-service-design.md`

## Global Constraints

- Preserve native Persona backup, readback, cache, avatar cloning, and avatar-preservation semantics.
- Preserve `PERSONA_EDIT_LOCK`, logical-ID uniqueness, and the current `state_integrity.py` upsert/delete overrides.
- Do not add `persona_service.py` to `DEFAULT_RUNTIME_STAGES`.
- Do not add a new public-callable override, `_ORIGINAL_*` chain, global service locator, ORM, Persona import/export feature, or Live Sync behavior change.
- Persona UI text, callback tokens, and pending-input state remain outside the application service.
- Session selection must continue through the existing session persistence helper and operation/idempotency semantics.
- Full exact-head CI is required before the PR is marked Ready.
- Do not merge the PR as part of this plan.

## Review Focus

- A native create can succeed before session selection fails; Phase 5C must not invent destructive rollback of the native Persona.
- A delete racing with another session reference must still check current SQLite state immediately before native deletion.
- Compatibility service construction must resolve the final late-loaded `upsert_native_persona` / `delete_native_persona` functions, not capture pre-integrity implementations.
- A failed native save during pending input must leave the pending state and existing settings untouched so the user can retry.
- Missing Persona selection/edit callbacks must not mutate the session and must retain current user-visible feedback.

---

### Task 1: Persona repository primitive and application-service contract

**Files:**
- Create: `bridge/persona_service.py`
- Create: `tests/test_persona_service.py`
- Modify: `bridge/repositories.py`
- Modify: `tests/test_repository_transactions.py`

**Interfaces:**
- Consumes: native Persona load/default/upsert/delete callables, existing `update_session`-compatible callable, and `count_persona_references(db, persona_id)`.
- Produces: `PersonaService.list()`, `get()`, `name()`, `default_id()`, `create_and_select()`, `update()`, `select()`, `disable()`, and `delete_if_unused()`.

- [x] **Step 1: Add RED service tests for reads and lifecycle behavior**

Create `tests/test_persona_service.py` with a fake collaborator fixture and tests equivalent to:

```python
import sqlite3
import unittest

from bridge.persona_service import PersonaService


class PersonaServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.personas = {
            "native.png": {
                "name": "Native",
                "description": "Original",
                "sillytavern_avatar": "native.png",
            }
        }
        self.session_updates = []
        self.upserts = []
        self.deletes = []
        self.references = 0

        self.service = PersonaService(
            load_personas=lambda: dict(self.personas),
            load_default_persona=lambda: "native.png",
            upsert_persona=self._upsert,
            delete_persona=self._delete,
            update_session_persona=self._update_session,
            persona_reference_count=lambda _db, _persona_id: self.references,
        )

    def tearDown(self):
        self.db.close()

    def _upsert(self, persona_id, name, description):
        self.upserts.append((persona_id, name, description))
        avatar = persona_id if persona_id.endswith(".png") else f"bridge-{persona_id}.png"
        self.personas[avatar] = {
            "name": name,
            "description": description,
            "sillytavern_avatar": avatar,
        }
        return avatar

    def _delete(self, persona_id):
        self.deletes.append(persona_id)
        return self.personas.pop(persona_id, None) is not None

    def _update_session(self, db, chat_id, session_id, **kwargs):
        self.session_updates.append((db, chat_id, session_id, kwargs))

    def test_reads_delegate_without_mutation(self):
        self.assertEqual(self.service.get("native.png")["name"], "Native")
        self.assertEqual(self.service.name("native.png"), "Native")
        self.assertEqual(self.service.name("missing"), "")
        self.assertEqual(self.service.default_id(), "native.png")
        self.assertEqual(set(self.service.list()), {"native.png"})

    def test_create_and_select_persists_then_selects_native_avatar(self):
        avatar = self.service.create_and_select(
            self.db, "chat", "session", "writer", "Writer", "Writes notes",
            operation_id=42,
        )
        self.assertEqual(avatar, "bridge-writer.png")
        self.assertEqual(self.upserts, [("writer", "Writer", "Writes notes")])
        self.assertEqual(
            self.session_updates[-1][3],
            {
                "persona_id": "bridge-writer.png",
                "operation_id": 42,
                "operation_kind": "persona_create",
            },
        )

    def test_invalid_create_never_calls_store_or_session(self):
        with self.assertRaises(ValueError):
            self.service.create_and_select(
                self.db, "chat", "session", "bad id!", "Writer", "Description"
            )
        self.assertEqual(self.upserts, [])
        self.assertEqual(self.session_updates, [])

    def test_update_requires_existing_persona(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            self.service.update("missing.png", "Missing", "Description")
        self.assertEqual(self.upserts, [])

    def test_select_missing_returns_false_without_mutation(self):
        self.assertFalse(
            self.service.select(self.db, "chat", "session", "missing.png")
        )
        self.assertEqual(self.session_updates, [])

    def test_disable_clears_session_persona(self):
        self.service.disable(self.db, "chat", "session", operation_id=9)
        self.assertEqual(
            self.session_updates[-1][3],
            {
                "persona_id": "",
                "operation_id": 9,
                "operation_kind": "persona_select",
            },
        )

    def test_delete_refuses_referenced_persona(self):
        self.references = 1
        with self.assertRaisesRegex(ValueError, "used by another session"):
            self.service.delete_if_unused(self.db, "native.png")
        self.assertEqual(self.deletes, [])

    def test_create_does_not_delete_native_persona_when_session_select_fails(self):
        def failing_update(*_args, **_kwargs):
            raise RuntimeError("session write failed")

        service = PersonaService(
            load_personas=lambda: dict(self.personas),
            load_default_persona=lambda: "native.png",
            upsert_persona=self._upsert,
            delete_persona=self._delete,
            update_session_persona=failing_update,
            persona_reference_count=lambda _db, _persona_id: 0,
        )
        with self.assertRaisesRegex(RuntimeError, "session write failed"):
            service.create_and_select(
                self.db, "chat", "session",
                "writer", "Writer", "Writes notes",
            )
        self.assertIn("bridge-writer.png", self.personas)
        self.assertEqual(self.deletes, [])

    def test_delete_checks_current_reference_count_on_each_call(self):
        self.references = 0
        self.assertTrue(self.service.delete_if_unused(self.db, "native.png"))
        self.personas["native.png"] = {
            "name": "Native", "description": "Original",
            "sillytavern_avatar": "native.png",
        }
        self.references = 1
        with self.assertRaisesRegex(ValueError, "used by another session"):
            self.service.delete_if_unused(self.db, "native.png")
```

Also cover successful `update()`, successful `select()`, successful unused deletion, duplicate logical ID rejection when the current catalog already exposes that ID/avatar, and name/description bounds.

- [x] **Step 2: Verify service tests are RED**

Run:

```bash
python -m unittest tests.test_persona_service -v
```

Expected: import failure because `bridge.persona_service` does not exist.

- [x] **Step 3: Add a RED repository test for Persona references**

Extend the in-memory schema in `RepositoryPrimitiveTests.setUp()` with:

```sql
CREATE TABLE sessions(
    chat_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    PRIMARY KEY(chat_id, session_id)
);
```

Add:

```python
def test_count_persona_references_is_read_only(self):
    self.db.execute(
        "INSERT INTO sessions(chat_id,session_id,persona_id) VALUES(?,?,?)",
        ("chat", "one", "native.png"),
    )
    self.db.commit()
    traced = []
    self.db.set_trace_callback(traced.append)
    try:
        self.assertEqual(
            repositories.count_persona_references(self.db, "native.png"),
            1,
        )
    finally:
        self.db.set_trace_callback(None)
    self.assertFalse(self.db.in_transaction)
    self.assertFalse(
        any(sql.lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP")
        ) for sql in traced)
    )
```

- [x] **Step 4: Verify repository test is RED**

Run:

```bash
python -m unittest tests.test_repository_transactions.RepositoryPrimitiveTests.test_count_persona_references_is_read_only -v
```

Expected: `AttributeError` because `count_persona_references` does not exist.

- [x] **Step 5: Implement the repository primitive**

Add to `bridge/repositories.py`:

```python
def count_persona_references(
    db: sqlite3.Connection,
    persona_id: str,
) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM sessions WHERE persona_id=?",
        (str(persona_id),),
    ).fetchone()
    return int(row[0] or 0) if row else 0
```

It must contain no commit/rollback/transaction ownership.

- [x] **Step 6: Implement minimal `PersonaService`**

Create a frozen dataclass in `bridge/persona_service.py`. Validation rules:

```python
_PERSONA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

def _validated_text(name: str, description: str) -> tuple[str, str]:
    name = str(name)
    description = str(description)
    if not 1 <= len(name) <= 120:
        raise ValueError("Persona name must be 1–120 characters")
    if not 1 <= len(description) <= 4000:
        raise ValueError("Persona description must be 1–4,000 characters")
    return name, description
```

Behavior:

- `list()` returns a shallow-copy dictionary from `load_personas()`.
- `get()` reads from `list()`.
- `name()` returns an empty string for a missing Persona.
- `default_id()` delegates to `load_default_persona()`.
- `create_and_select()` validates ID/name/description and rejects a logical ID already represented either directly by a catalog key or by a native avatar whose stem is `bridge-<logical_id>`; then it calls `upsert_persona` and `update_session_persona(... persona_id=avatar, operation_id=..., operation_kind="persona_create")`.
- `update()` refuses a missing Persona and delegates upsert using the native Persona/avatar identifier.
- `select()` returns `False` for missing Persona and otherwise calls `update_session_persona(... operation_kind="persona_select")`.
- `disable()` writes `persona_id=""` with `operation_kind="persona_select"`.
- `delete_if_unused()` raises `ValueError("Persona is used by another session")` when the repository count is nonzero and otherwise delegates deletion.

Do not import Telegram, `bridge.runtime`, or native settings paths.

- [x] **Step 7: Verify Task 1 GREEN**

Run:

```bash
python -m unittest tests.test_persona_service tests.test_repository_transactions -v
```

Expected: all tests pass.

- [x] **Step 8: Commit Task 1**

Commit:

```text
refactor: add PersonaService application contract
```

---

### Task 2: Late-bound compatibility service and startup composition

**Files:**
- Modify: `bridge/persona_sync.py`
- Modify: `bridge/composition.py`
- Modify: `bridge/main.py`
- Modify: `tests/test_persona_service.py`
- Modify: `tests/test_composition.py`
- Modify: `tests/test_runtime_loader.py`

**Interfaces:**
- Consumes: `PersonaService` and `repositories.count_persona_references`.
- Produces: `compatibility_persona_service()`, `resolve_persona_service()`, `BridgeServices.persona`, and production startup injection.

- [x] **Step 1: Add RED compatibility tests**

In `tests/test_persona_service.py`, using `bridge.runtime as rt`, patch final runtime collaborators after runtime loading and assert the compatibility service sees the patches:

```python
def test_compatibility_service_late_binds_final_runtime_collaborators(self):
    calls = []
    with patch.object(
        rt, "load_personas",
        return_value={"native.png": {"name": "Native", "description": "D"}},
    ), patch.object(
        rt, "default_persona_id", return_value="native.png"
    ), patch.object(
        rt, "upsert_native_persona",
        side_effect=lambda *args: calls.append(("upsert", args)) or "native.png",
    ), patch.object(
        rt, "delete_native_persona",
        side_effect=lambda persona_id: calls.append(("delete", persona_id)) or True,
    ):
        service = rt.compatibility_persona_service()
        self.assertEqual(service.name("native.png"), "Native")
        self.assertEqual(service.default_id(), "native.png")
        service.update("native.png", "Updated", "D")
        service.delete_if_unused(sqlite3.connect(":memory:"), "native.png")
```

For the deletion call, patch the repository collaborator used by the compatibility service to return zero rather than relying on an uninitialized in-memory schema.

Also assert `resolve_persona_service(fake)` returns the injected fake unchanged.

- [x] **Step 2: Add RED composition tests**

Extend `tests/test_composition.py`:

```python
def test_bridge_services_accepts_persona_service(self):
    persona = object()
    services = build_bridge_services(
        config,
        db_factory=lambda: sqlite3.connect(":memory:"),
        telegram=telegram,
        background=background,
        persona=persona,
    )
    self.assertIs(services.persona, persona)
```

Add a startup test that patches the runtime-resolved Persona collaborators, calls `rt._build_startup_services(config)`, and asserts `services.persona` is a `PersonaService` whose collaborator identities are the patched final functions.

Update the Phase 5 source guard so `GroupDirectorService`, `MemoryService`, and `PersonaService` are allowed while `SyncService` and `JobService` remain absent.

- [x] **Step 3: Add RED runtime-loader guard**

Add:

```python
def test_persona_service_is_not_a_runtime_stage(self):
    loaded = {
        module for stage in DEFAULT_RUNTIME_STAGES for module in stage.modules
    }
    self.assertNotIn("persona_service.py", loaded)
```

- [x] **Step 4: Verify Task 2 tests are RED**

Run:

```bash
python -m unittest   tests.test_persona_service   tests.test_composition   tests.test_runtime_loader -v
```

Expected failures: missing compatibility helpers, missing `BridgeServices.persona`, missing startup construction, and/or Phase 5 guard rejecting the new service.

- [x] **Step 5: Implement the late-bound compatibility adapter**

In `bridge/persona_sync.py`, ordinary-import:

```python
from bridge.persona_service import PersonaService as _PersonaService
from bridge.repositories import (
    count_persona_references as _repo_count_persona_references,
)
```

Add:

```python
def compatibility_persona_service() -> _PersonaService:
    return _PersonaService(
        load_personas=load_personas,
        load_default_persona=default_persona_id,
        upsert_persona=upsert_native_persona,
        delete_persona=delete_native_persona,
        update_session_persona=update_session,
        persona_reference_count=_repo_count_persona_references,
    )


def resolve_persona_service(persona_service=None) -> _PersonaService:
    return (
        persona_service
        if persona_service is not None
        else compatibility_persona_service()
    )
```

These global names must be resolved inside the function body at call time so the later `state_integrity.py` overrides remain effective.

- [x] **Step 6: Wire composition**

In `bridge/composition.py`:

- import `PersonaService`,
- add `persona: PersonaService | None = None` after `memory`,
- add optional `persona` parameter to `build_bridge_services()`,
- pass it into `BridgeServices`.

In `bridge/main.py`:

- ordinary-import `PersonaService as _PersonaService`,
- import `count_persona_references as _count_persona_references`,
- construct the service in `_build_startup_services()` from final runtime globals:

```python
persona = _PersonaService(
    load_personas=load_personas,
    load_default_persona=default_persona_id,
    upsert_persona=upsert_native_persona,
    delete_persona=delete_native_persona,
    update_session_persona=update_session,
    persona_reference_count=_count_persona_references,
)
```

Pass `persona=persona` to `_build_bridge_services_value()`.

- [x] **Step 7: Verify Task 2 GREEN**

Run the same unittest command from Step 4. Expected: all pass.

- [x] **Step 8: Commit Task 2**

Commit:

```text
refactor: inject PersonaService at startup
```

---

### Task 3: Pending Persona create/edit through the service

**Files:**
- Modify: `bridge/message_commands.py`
- Modify: `bridge/input_flows.py`
- Modify: `tests/test_persona_editor.py`
- Modify: `tests/test_composition.py`

**Interfaces:**
- Consumes: injected or compatibility `PersonaService`.
- Produces: service-owned create/edit behavior for pending Persona input.

- [x] **Step 1: Add RED create/edit service-boundary tests**

In `tests/test_persona_editor.py`, create a fake service exposing `get()`, `create_and_select()`, `update()`, and `name()`. Start pending Persona state with the existing helper and call:

```python
rt.handle_pending_input(
    self.db,
    "token",
    "chat",
    self.session,
    "writer | Writer | I write concise notes.",
    persona_service=fake,
)
```

Assert:

- create invokes `fake.create_and_select(db, "chat", session_id, "writer", "Writer", description, operation_id=...)`,
- edit invokes `fake.update(native_id, name, description)`,
- the raw `rt.upsert_native_persona` is patched to raise if called,
- invalid input does not call the service and leaves pending state intact,
- a service exception keeps pending state and sends the existing retry feedback.

- [x] **Step 2: Add RED production propagation test**

In `tests/test_composition.py`, give `BridgeServices` a sentinel Persona service, patch `rt.handle_pending_input`, invoke `rt.process_message(..., services=services)`, and assert:

```python
self.assertIs(captured["persona_service"], services.persona)
```

Keep existing `memory_service` propagation assertion intact.

- [x] **Step 3: Verify RED**

Run:

```bash
python -m unittest   tests.test_persona_editor   tests.test_composition.WorkerInjectionTests -v
```

Expected: `handle_pending_input` rejects `persona_service` and/or production does not propagate it.

- [x] **Step 4: Thread the injected service into pending input**

In `bridge/message_commands.py`:

```python
persona_service = (
    getattr(services, "persona", None)
    if services is not None else None
)
```

Pass it to `handle_pending_input(..., persona_service=persona_service)`.

In `bridge/input_flows.py`:

- add optional keyword `persona_service=None` to `handle_pending_input`,
- pass it to `_handle_persona_input`,
- add optional keyword to `_handle_persona_input`,
- start `_handle_persona_input` with:

```python
persona_service = resolve_persona_service(persona_service)
```

Replace application decisions:

- `get_persona(persona_id)` -> `persona_service.get(persona_id)`,
- create raw upsert + session update -> `persona_service.create_and_select(...)`,
- edit raw upsert -> `persona_service.update(...)`.

Keep parsing, pending-state mutation, messages, and Telegram menu operations in the UI function.

- [x] **Step 5: Verify native-save failure compatibility**

Run the existing `test_save_failure_keeps_native_settings_and_pending_state` with no injected service. It must still pass because the compatibility service late-binds the patched final `upsert_native_persona`.

- [x] **Step 6: Verify Task 3 GREEN**

Run:

```bash
python -m unittest tests.test_persona_editor tests.test_composition -v
```

Expected: all pass.

- [x] **Step 7: Commit Task 3**

Commit:

```text
refactor: route Persona input through PersonaService
```

---

### Task 4: Persona callbacks through the service

**Files:**
- Modify: `bridge/main.py`
- Modify: `bridge/callbacks.py`
- Modify: `bridge/panel_callback_routes.py`
- Modify: `bridge/input_flows.py`
- Modify: `tests/test_composition.py`
- Modify: `tests/test_persona_editor.py`

**Interfaces:**
- Consumes: `BridgeServices.persona`.
- Produces: injected Persona selection, disable, and deletion through `PersonaService`.

- [x] **Step 1: Add RED callback-worker propagation test**

In `tests/test_composition.py`, patch `rt.process_callback`, call `rt.process_callback_job(self.services, "chat", callback)`, and assert the worker passes:

```python
services=self.services
```

without changing the worker's public positional signature.

- [x] **Step 2: Add RED callback application tests**

In `tests/test_persona_editor.py`, use a fake Persona service and call `handle_persona_callback(..., persona_service=fake)`.

Cover:

- selecting an existing Persona calls `fake.select(...)` and never direct `update_session`,
- Persona off calls `fake.disable(...)`,
- delete confirmation calls `fake.delete_if_unused(...)`,
- `ValueError("Persona is used by another session")` maps to the existing refusal message,
- missing select returns the existing "Persona not found" feedback and does not mutate session,
- direct legacy invocation without a service still uses the compatibility service and preserves current behavior.

Patch raw `rt.delete_native_persona` / direct session-update paths to raise in injected-service tests so accidental bypasses fail loudly.

- [x] **Step 3: Verify RED**

Run:

```bash
python -m unittest   tests.test_persona_editor   tests.test_composition.WorkerInjectionTests -v
```

Expected: callback signatures do not yet accept/propagate services.

- [x] **Step 4: Propagate services through callback dispatch**

In `bridge/main.py`:

```python
process_callback(
    db,
    token,
    callback,
    operation_id=job_id,
    services=services,
)
```

In `bridge/callbacks.py`:

- add keyword-only `services=None` to `process_callback`,
- compute `persona_service = getattr(services, "persona", None) if services else None`,
- pass it into `handle_entity_panel_callback(..., persona_service=persona_service)`.

In `bridge/panel_callback_routes.py`:

- add optional keyword `persona_service=None`,
- pass it only to `handle_persona_callback`; character/session/world paths remain unchanged.

- [x] **Step 5: Replace callback application decisions**

At the top of `handle_persona_callback`:

```python
persona_service = resolve_persona_service(persona_service)
```

Use:

- `persona_service.get()` for existence checks,
- `persona_service.select(...)`,
- `persona_service.disable(...)`,
- `persona_service.delete_if_unused(...)`.

Remove the direct SQL reference count from this function; that decision now belongs to the service. Preserve the exact current feedback strings.

- [x] **Step 6: Verify Task 4 GREEN**

Run:

```bash
python -m unittest tests.test_persona_editor tests.test_composition -v
```

Expected: all pass.

- [x] **Step 7: Commit Task 4**

Commit:

```text
refactor: route Persona callbacks through PersonaService
```

---

### Task 5: Persona UI reads and source-boundary enforcement

**Files:**
- Modify: `bridge/cards.py`
- Modify: `bridge/input_flows.py`
- Modify: `bridge/persona_delete_panel.py`
- Modify: `bridge/command_routes.py`
- Modify: `tests/test_persona_editor.py`
- Modify: `tests/test_persona_native_sync.py`
- Modify: `tests/test_composition.py`

**Interfaces:**
- Consumes: optional injected Persona service or `resolve_persona_service()`.
- Produces: menus/prompts backed by the same application read boundary and regression guards against raw lifecycle calls.

- [x] **Step 1: Add RED UI-read tests**

Add focused tests proving:

- `send_persona_menu(..., persona_service=fake)` uses `fake.list()` / `fake.name()`,
- `send_persona_edit_menu(..., persona_service=fake)` uses `fake.get()`,
- `send_persona_delete_menu(..., persona_service=fake)` uses `fake.list()`,
- `/persona` command routing forwards `services.persona` into the menu.

Use fake service data that differs from `rt.load_personas()` so the test proves the injected read boundary rather than merely matching existing global data.

- [x] **Step 2: Add RED source-boundary regression tests**

In `tests/test_persona_service.py`, inspect the migrated application/UI sources.

Assert the full `bridge/input_flows.py` source contains neither:

```python
"upsert_native_persona("
"delete_native_persona("
```

Extract the source chunks for `_handle_persona_input` and `handle_persona_callback` and assert they do not contain `"update_session("`.

Extract `send_persona_menu` and assert its body does not contain `"load_personas("` or `"persona_name("`.

These guards apply only to the migrated application/UI owners; `persona_sync.py` and `state_integrity.py` are expected to keep native backend calls in Phase 5C.

- [x] **Step 3: Verify RED**

Run:

```bash
python -m unittest   tests.test_persona_service   tests.test_persona_editor   tests.test_persona_native_sync   tests.test_composition -v
```

Expected: optional Persona-service parameters and source-boundary conditions are not yet satisfied.

- [x] **Step 4: Route Persona presentation reads through the service**

Change these functions to accept keyword-only `persona_service=None`, resolve it at call time, and use service reads:

- `send_persona_menu` in `cards.py`,
- `_persona_input_prompt`, `send_persona_edit_menu`, `start_persona_input`, and `send_persona_delete_confirm` in `input_flows.py`,
- `send_persona_delete_menu` in `persona_delete_panel.py`.

Propagate the same service from pending-input and callback workflows into these presentation helpers.

- [x] **Step 5: Propagate service through the `/persona` command route**

Change `_handle_entities(..., services=None)`, pass `services` from `handle_command_route`, and call:

```python
send_persona_menu(
    token,
    chat_id,
    current_persona,
    persona_service=(
        getattr(services, "persona", None)
        if services is not None else None
    ),
)
```

Do not change unrelated entity routes.

- [x] **Step 6: Verify native synchronization behavior remains unchanged**

Run:

```bash
python -m unittest   tests.test_persona_editor   tests.test_persona_native_sync   tests.test_persona_service   tests.test_composition -v
```

Expected: all pass, including existing native settings preservation and avatar tests.

- [x] **Step 7: Commit Task 5**

Commit:

```text
refactor: unify Persona UI service boundary
```

---

### Task 6: Whole-phase verification, documentation, and pull request

**Files:**
- Modify: `docs/superpowers/specs/2026-09-20-persona-service-design.md` only if implementation rulings changed the approved design.
- Modify: `docs/superpowers/plans/2026-09-20-phase-5-persona-service.md` to mark completed checklist items after evidence exists.

**Interfaces:**
- Consumes: completed Phase 5C branch.
- Produces: review evidence and a Ready-for-review PR; does not merge it.

- [x] **Step 1: Run focused Persona and integrity tests**

Run:

```bash
python -m unittest   tests.test_persona_service   tests.test_persona_editor   tests.test_persona_native_sync   tests.test_repository_transactions   tests.test_composition   tests.test_runtime_loader -v
```

Expected: zero failures/errors.

- [x] **Step 2: Run the complete repository test suite**

Run the repository's CI-equivalent commands used by `.github/workflows`:

```bash
python -m compileall bridge tests
python -m unittest discover -s tests -v
python -m pytest -q
pip-audit
```

Expected: compile success, all unittest/pytest tests pass, and no known dependency vulnerabilities.

- [x] **Step 3: Review the whole branch against the approved spec**

Compare the Phase 5C head against base `e73b3bb4e055a7f228cbdcb831caca934ce1139e`.

Review specifically for:

- bypasses around `PersonaService`,
- captured pre-integrity Persona collaborators,
- loss of existing feedback strings or pending-state retry behavior,
- new runtime stages/overrides/`_ORIGINAL_*` chains,
- unrelated Sync/Job/Phase 6 work.

If a Critical or Important issue is found, add a failing regression test first, verify RED, fix minimally, and rerun the focused and full suites. Minor findings may be documented for a later phase.

- [x] **Step 4: Update the plan checklist and spec only from verified reality**

Mark each completed plan item `[x]` only after the corresponding test/CI evidence exists. If implementation required a ruling that changed the design, update the spec with that exact ruling; otherwise leave the approved spec unchanged.

Commit documentation as:

```text
docs: close Phase 5C PersonaService checklist
```

- [ ] **Step 5: Obtain exact-head CI evidence**

Push/commit the final head to `refactor/phase-5-persona-service`. Create or update the PR against `cepeter/SillyTavern-Telegram-Bridge:main` with title:

```text
refactor: extract Persona application service
```

If a Draft PR was created earlier to obtain RED CI evidence, keep it Draft until this step.

Wait for the workflow attached to the exact final head and inspect every CI job. Do not use an earlier green run as release evidence.

- [ ] **Step 6: Final PR body**

The PR body must state:

- Persona lifecycle is owned by `PersonaService`,
- legacy direct callers use a late-bound compatibility service,
- final `state_integrity.py` Persona overrides remain effective,
- native Persona storage algorithms were not rewritten,
- `persona_service.py` is outside runtime stages,
- exact final head SHA and CI totals,
- design and plan document paths.

- [ ] **Step 7: Mark Ready only after exact-head GREEN**

Before changing Draft -> Ready, verify:

- PR is mergeable,
- no unresolved Critical/Important review issue exists,
- exact-head CI is green,
- source-boundary tests are green,
- full Persona/native/integrity regressions are green.

Then mark the PR Ready for maintainer review. Do not merge it.

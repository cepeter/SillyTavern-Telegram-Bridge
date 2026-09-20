# Phase 5C PersonaService Design

Date: 2026-09-20  
Status: Proposed implementation design for Phase 5C  
Parent: `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`  
Predecessor: Phase 5B MemoryService merged in PR #37

## 1. Purpose

Phase 5C extracts Persona lifecycle orchestration into one ordinary-import
`PersonaService` while preserving the existing native SillyTavern Persona
storage behavior and the late-loaded integrity hardening that currently
serializes whole-document writes and enforces logical Persona ID uniqueness.

The phase establishes one application owner for Persona use cases without
prematurely performing the Phase 6 safety/decorator migration.

## 2. Current architecture

Persona behavior is currently distributed across:

- `bridge/cards.py` for Persona reads, lookup, labels, default selection, and
  the Persona menu.
- `bridge/input_flows.py` for create/edit pending-input parsing, selection,
  disable, deletion checks, and callback orchestration.
- `bridge/persona_delete_panel.py` for delete-menu presentation.
- `bridge/persona_sync.py` for native SillyTavern settings/avatar persistence.
- `bridge/state_integrity.py` for late overrides of
  `upsert_native_persona()` and `delete_native_persona()`.
- session persistence helpers for the selected `persona_id`.

The important current safety behavior is:

1. native Persona settings are read-modify-written with backup/readback checks,
2. Persona writes are serialized by `PERSONA_EDIT_LOCK`,
3. bridge logical Persona IDs cannot be silently reused,
4. native avatar files are preserved on Persona deletion,
5. a Persona referenced by any session cannot be deleted through the bridge,
6. create selects the newly created Persona for the current session,
7. failed native writes do not clear the pending input or corrupt the existing
   native settings.

These properties must remain unchanged.

## 3. Target boundary

Phase 5C introduces:

```text
Telegram commands / callbacks / pending input
                    |
                    v
              PersonaService
                    |
          +---------+---------+
          |                   |
          v                   v
   session persistence     PersonaStore boundary
                              |
                              v
                 current integrity-protected
                   SillyTavern Persona I/O
```

`PersonaService` owns application decisions. Native SillyTavern settings,
backup, atomic replacement, avatar cloning, API/file access, cache management,
and readback verification remain infrastructure concerns.

## 4. Service responsibilities

Create `bridge/persona_service.py` as an ordinary Python module.

The service owns these use cases:

- list Personas,
- get one Persona,
- resolve a Persona display name,
- resolve the native default Persona,
- create a Persona and select it for the current session,
- update an existing Persona,
- select an existing Persona for a session,
- disable Persona for a session,
- delete an inactive Persona only when no session references it.

The service must not import `bridge.runtime` and must not contain Telegram UI
logic, callback-token logic, native SillyTavern file algorithms, or runtime
loader logic.

## 5. Proposed interface

Use a frozen dataclass with explicit callable collaborators.

```python
@dataclass(frozen=True)
class PersonaService:
    load_personas: Callable[[], dict[str, dict[str, object]]]
    load_default_persona: Callable[[], str]
    upsert_persona: Callable[[str, str, str], str]
    delete_persona: Callable[[str], bool]
    update_session_persona: Callable[..., None]
    persona_reference_count: Callable[[sqlite3.Connection, str], int]

    def list(self) -> dict[str, dict[str, object]]: ...
    def get(self, persona_id: str) -> dict[str, object] | None: ...
    def name(self, persona_id: str) -> str: ...
    def default_id(self) -> str: ...

    def create_and_select(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        logical_id: str,
        name: str,
        description: str,
        *,
        operation_id: int | str | None = None,
    ) -> str: ...

    def update(
        self,
        persona_id: str,
        name: str,
        description: str,
    ) -> str: ...

    def select(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        persona_id: str,
        *,
        operation_id: int | str | None = None,
    ) -> bool: ...

    def disable(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
        *,
        operation_id: int | str | None = None,
    ) -> None: ...

    def delete_if_unused(
        self,
        db: sqlite3.Connection,
        persona_id: str,
    ) -> bool: ...
```

The exact collaborator signatures may be narrowed during implementation, but
the public use-case ownership above is binding.

## 6. Validation semantics

The service owns Persona use-case validation currently duplicated in
`input_flows.py`.

Create:

- logical ID: `[A-Za-z0-9_-]{1,64}`,
- name: 1-120 characters,
- description: 1-4,000 characters.

Update:

- target Persona must exist,
- name and description use the same bounds.

Select:

- target Persona must exist,
- no session mutation occurs for a missing Persona.

Delete:

- target Persona may not be the active Persona of the current UI workflow
  before the service is called; the UI can reject that earlier for feedback,
- the service itself must refuse deletion whenever any row in `sessions`
  references the Persona,
- native deletion is delegated only after the reference count is zero.

The cross-session reference rule belongs in the application service because it
coordinates repository state with the external Persona store.

## 7. Store and integrity relationship

Phase 5C does not remove the current `state_integrity.py` Persona overrides.

Production startup constructs `PersonaService` after all runtime stages have
been composed and injects the final runtime-resolved collaborators:

- `load_personas`,
- `default_persona_id` or equivalent default resolver,
- final `upsert_native_persona`,
- final `delete_native_persona`,
- `update_session`,
- a narrow Persona-reference query.

Therefore production create/edit/delete continues to cross the
`state_integrity.py` wrappers that:

- serialize writes with `PERSONA_EDIT_LOCK`,
- reject reused logical bridge IDs,
- preserve native avatar extension behavior.

Phase 6 may later replace this with an explicit decorator such as:

```text
PersonaStore
    |
    v
IntegrityCheckedPersonaStore
    |
    v
SillyTavernPersonaStore
```

That conversion is explicitly out of scope for Phase 5C.

## 8. Compatibility service

Direct legacy callers and tests that do not receive `BridgeServices` must
still cross the same Persona application boundary.

Provide a late-bound compatibility resolver in a staged module that already
owns Persona runtime collaborators, or another location that does not create a
new runtime stage:

```python
def compatibility_persona_service() -> PersonaService:
    return PersonaService(
        load_personas=load_personas,
        load_default_persona=default_persona_id,
        upsert_persona=upsert_native_persona,
        delete_persona=delete_native_persona,
        update_session_persona=update_session,
        persona_reference_count=...,
    )
```

The collaborators must be resolved at call time, not captured before
`state_integrity.py` loads. This preserves the final runtime overrides.

A helper such as `resolve_persona_service(service=None)` may return the injected
service or a short-lived compatibility service.

## 9. Composition

Extend `BridgeServices` with:

```python
persona: PersonaService | None = None
```

Extend `build_bridge_services(...)` with an optional Persona service argument.

`_build_startup_services()` constructs the production `PersonaService` after
runtime composition, using the final runtime-resolved Persona collaborators.

No global current-service locator is introduced.

## 10. UI and callback propagation

Telegram presentation remains outside `PersonaService`.

Keep in UI modules:

- menu rendering,
- edit/delete confirmation text,
- callback tokens,
- pending-input state,
- parsing user text into requested fields,
- answer-callback/send-text behavior.

Change UI workflows so application decisions cross the service:

- pending create -> `create_and_select()`,
- pending edit -> `update()`,
- select callback -> `select()`,
- off callback -> `disable()`,
- delete confirmation -> `delete_if_unused()`,
- Persona reads shown in menus/edit prompts should use service reads where the
  service is available.

Production callback processing must carry `BridgeServices` or at minimum
`services.persona` into the Persona callback path. Production pending-input
processing must similarly receive the injected Persona service.

Legacy direct callers resolve the compatibility service.

## 11. Ordinary-import/runtime relationship

`persona_service.py` must never be listed in `DEFAULT_RUNTIME_STAGES`.

The following remain staged compatibility modules in Phase 5C:

- `cards.py`,
- `input_flows.py`,
- `persona_sync.py`,
- `state_integrity.py`.

The existing `state_integrity.py` allowlisted overrides of
`upsert_native_persona` and `delete_native_persona` remain until Phase 6.

Phase 5C must not add:

- a new runtime stage,
- a new public-callable override,
- another `_ORIGINAL_*` capture,
- a global service locator.

## 12. Transaction and failure semantics

Session selection changes continue through the existing session persistence
helper so current operation/idempotency behavior is preserved.

Create ordering:

1. validate,
2. persist native Persona through the final protected store,
3. select the returned native avatar for the current session,
4. return the native avatar ID.

If native persistence fails, session selection must not change.

If session selection fails after native creation, Phase 5C must preserve the
existing behavior unless a focused regression test demonstrates a safe rollback
mechanism already exists. Do not introduce destructive native rollback merely
for architectural symmetry.

Update:

- persist through the protected store,
- return the native Persona/avatar identifier.

Delete:

1. count session references,
2. refuse when count is nonzero,
3. delegate native deletion when zero.

## 13. Testing

Use strict RED -> GREEN TDD.

Add ordinary `PersonaService` tests for:

- list/get/name/default reads,
- valid create-and-select,
- create validation failure,
- update of an existing Persona,
- update of a missing Persona,
- valid select,
- missing select leaves session unchanged,
- disable,
- delete when unused,
- delete refusal when referenced.

Add composition/integration tests proving:

- `BridgeServices.persona` is injected,
- startup constructs `PersonaService` from final runtime collaborators,
- callback selection/off/delete uses the injected service,
- pending create/edit uses the injected service,
- direct legacy callback/input callers use the compatibility service,
- native save failure keeps pending Persona input and current settings,
- integrity-protected duplicate logical IDs remain rejected,
- `persona_service.py` is not a runtime stage.

Add a source-boundary regression guard for the application/UI files migrated in
this phase so they do not directly call native Persona upsert/delete or directly
own session Persona selection where the service is supposed to own it.

The complete CI suite remains the release gate.

## 14. Out of scope

Phase 5C does not:

- remove `state_integrity.py` Persona overrides,
- rewrite native Persona storage,
- change SillyTavern settings/API formats,
- introduce an ORM,
- redesign Persona UI,
- change Persona avatar deletion policy,
- add Persona import/export features,
- change Live Sync behavior,
- remove the compatibility runtime loader.

## 15. Acceptance criteria

Phase 5C is complete when:

- `PersonaService` is ordinary-importable,
- Persona lifecycle decisions have one application-service owner,
- create/edit/select/off/delete production workflows use the injected service,
- direct legacy callers cross a late-bound compatibility service,
- current native Persona backup/readback/cache/avatar behavior is unchanged,
- current state-integrity serialized-write and logical-ID protections remain
  effective,
- no new runtime stage or override is introduced,
- `persona_service.py` is outside runtime stages,
- focused Persona, state-integrity, composition, and runtime tests are green,
- the complete exact-head CI suite passes.

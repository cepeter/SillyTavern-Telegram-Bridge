# Group Director Application Service — Design

Date: 2026-09-20
Status: Phase 5A implementation design
Parent architecture: `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`
Migration phase: Phase 5 — Application service extraction
Repository: `cepeter/SillyTavern-Telegram-Bridge`
Baseline: synchronized upstream/fork `main` at `538ac6ee8ec4a786f68980e5f466a51f62f0a204`

## 1. Purpose

Extract the Group Director decision and prompt-coordination workflow from the compatibility runtime into one ordinary-import application service without changing user-visible behavior.

The service becomes the canonical owner of:

- selecting a Director-mode speaker,
- applying the current Director customization policy,
- validating bounded model/token overrides,
- constructing the invisible Director model request,
- parsing and validating the Director decision,
- falling back deterministically to round robin,
- constructing Director-mode speaker prompt context.

The existing runtime functions remain only as compatibility adapters for callers not yet migrated.

## 2. Constraints

This slice must:

- preserve current Group Director behavior and fallbacks,
- preserve Director Goals as the current policy provider,
- preserve the extension registry as a transitional Phase 5 policy boundary,
- preserve runtime-stage ordering,
- add no runtime override,
- add no service locator or mutable global service registry,
- keep `bridge/group_director_service.py` out of `DEFAULT_RUNTIME_STAGES`,
- keep direct callers of `group_director_plan()` and `group_prompt_context()` working,
- inject the production service from the existing startup composition root,
- pass the same `BridgeServices` object from `process_message_job()` into the message workflow,
- use the injected service on the live/recovered worker path,
- keep direct legacy test/caller behavior through compatibility adapters,
- make the new service independently unit-testable with fake dependencies.

## 3. Service boundary

Create `bridge/group_director_service.py` with an ordinary-importable `GroupDirectorService`.

The constructor receives explicit collaborators rather than importing `bridge.runtime`:

- group-state loader,
- safe-character predicate,
- member-label resolver,
- character-field loader,
- generation-settings loader,
- text-generation callable,
- Director customization provider,
- default model.

Public interface:

```python
class GroupDirectorService:
    def plan(
        self,
        db,
        api_key: str,
        chat_id: str,
        session: dict[str, str],
        user_text: str,
    ) -> tuple[str, dict[str, object], str] | None:
        ...

    def prompt_context(
        self,
        db,
        chat_id: str,
        session: dict[str, str],
        speaker_file: str,
        director_instruction: str = "",
    ) -> str:
        ...
```

Parsing and validation helpers are private implementation details of the service.

## 4. Compatibility adapter

`bridge/groups.py` stops owning Director workflow logic.

Its existing public functions:

- `group_director_plan(...)`
- `group_prompt_context(...)`

remain with the same signatures but construct a short-lived compatibility `GroupDirectorService` from the existing shared-runtime collaborators and delegate immediately.

This preserves existing direct callers while ensuring there is only one implementation of Director workflow semantics.

## 5. Composition

Extend `BridgeServices` with a `group_director` field.

The startup composition root in `bridge/main.py` constructs the production `GroupDirectorService` from the compatibility implementations already available at startup and passes it to `build_bridge_services()`.

`bridge/composition.py` may import the ordinary service type. It must not import `bridge.runtime`.

No global `CURRENT_SERVICES`, getter, setter, or equivalent locator is introduced.

## 6. Worker-to-workflow propagation

The live and recovered message-worker path already receives the same `BridgeServices` object.

Phase 5A extends the compatibility `process_message(...)` signature with an optional keyword-only `services` argument and propagates it through the recovery wrapper to the original message workflow.

The production worker always supplies `services=services`.

Inside `bridge/message_commands.py`:

- when an injected group-director service is available, use it for `plan()` and `prompt_context()`;
- direct legacy callers that do not supply services continue through `groups.py` compatibility adapters.

This keeps the migration behaviorally transparent while making the worker path explicit.

## 7. Director policy

The existing extension registry remains the transitional DirectorPolicy boundary.

The production `GroupDirectorService` receives `get_director_customization` as an injected callable.

The service must preserve current defensive behavior:

- policy failure falls back to ordinary Director behavior,
- invalid/blank model values use the session/default model,
- invalid token values use the base token budget,
- valid token values clamp to generation limits,
- a forced speaker bypasses policy and generation,
- invalid model output falls back to deterministic round robin.

## 8. Runtime-loader relationship

`group_director_service.py` is an ordinary Python module and must never be listed in `DEFAULT_RUNTIME_STAGES`.

`groups.py` and `director_goals.py` remain staged compatibility modules in this slice.

Removing Director Goals from staged loading or replacing the registry belongs to a later Phase 5/6 slice because `director_goals.py` still depends on the shared compatibility namespace.

## 9. Testing

Add ordinary service unit tests using fake callables and an in-memory SQLite connection.

Keep the existing `tests/test_group_director.py` suite green to prove compatibility behavior.

Add composition tests proving:

- startup constructs a `GroupDirectorService`,
- message workers propagate the same service graph into `process_message`,
- no service locator is introduced,
- the new service module is outside runtime stages.

Full repository CI remains the release gate.

## 10. Acceptance criteria

Phase 5A is complete when:

- `GroupDirectorService` is ordinary-importable,
- Director decision/prompt behavior has one implementation owner,
- `groups.py` Director functions are thin compatibility adapters,
- production startup injects the service through `BridgeServices`,
- live/recovered message workers propagate that same services object,
- the production message path uses the injected service,
- existing direct callers continue to work,
- existing Group Director and Director Goals behavior tests pass,
- no new runtime override or load-order dependency is introduced,
- `group_director_service.py` is not a runtime stage,
- the complete CI suite passes.

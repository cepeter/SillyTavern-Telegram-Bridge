# Phase 7B4 — Application / UI Ordinary-Import Boundary Design

Date: 2026-09-21  
Baseline upstream `main`: `f13fca5bb8eeaa4a2f3cfaf7280e772f03215737`  
Feature branch: `refactor/phase-7b4-application-ui-import-boundary`

## Status

Approved for execution after Phase 7B3 merged. This phase is the final domain/application migration before Phase 7C removes the production compatibility loader.

## Goal

Convert every remaining runtime-loaded domain, adapter, generation, command, callback, and UI module to ordinary Python imports, while preserving behavior and leaving only `main.py` in the compatibility loader.

Phase 7B4 includes retirement of the residual `cards.py` exec-loaded shell.

At the end of this phase:

- `common.py` is an ordinary shared runtime-support module, not exec-loaded;
- card/persona/character/session UI is ordinary-imported;
- memory/RAG/group shells are ordinary-imported;
- Telegram/session transport helpers are ordinary-imported;
- language/help/catalog/input/update/media/image/expression UI is ordinary-imported;
- generation, commands, command routes, message commands, callbacks, and panel callback routes are ordinary-imported;
- sync, Persona storage, identity/session naming, and safety-extension modules are ordinary-imported;
- extension registration is explicit and deterministic rather than dependent on source execution order;
- `bridge.runtime` remains the compatibility facade and re-exports canonical module objects;
- `DEFAULT_RUNTIME_STAGES` contains only `main.py`.

## Phase 7C boundary

Phase 7B4 does **not**:

- delete `runtime_loader.py`;
- remove the compatibility `bridge.runtime` facade;
- convert `main.py` to the final composition entry point;
- remove the last production `exec()` call;
- redesign user-visible behavior or feature semantics.

Those are Phase 7C responsibilities.

## Ordinary-import rules

1. A migrated module must import all collaborators explicitly from canonical owners.
2. No migrated module may import `bridge.runtime`.
3. Import order must not alter function identity or behavior.
4. Existing services (`MemoryService`, `PersonaService`, `SyncService`, `JobService`, `GroupDirectorService`) remain the preferred workflow seams.
5. Shared low-level helpers may be imported from `bridge.common` during this transitional phase; new domain ownership must not be added there.
6. Existing canonical modules from 7A–7B3 remain authoritative and must not be duplicated.

## Explicit extension registration

`scene_state.py`, `director_goals.py`, and `memory_curator.py` currently register hooks as import/exec side effects.

In 7B4 each exposes an idempotent explicit registration function. `bridge.runtime` resets the compatibility extension registry and invokes those registration functions in deterministic order before loading `main.py`.

This keeps behavior stable on runtime reload and removes dependence on exec ordering.

## Runtime facade

`bridge.runtime` ordinary-imports migrated modules and republishes their compatibility surface. Existing tests and callers may continue importing `bridge.runtime` during 7B4.

The facade must expose the exact canonical function objects for migrated public APIs.

## Loader endpoint

The final 7B4 loader shape is:

```python
DEFAULT_RUNTIME_STAGES = (
    RuntimeStage("core", ("main.py",)),
)
```

No other production source file is exec-loaded.

## TDD strategy

Use RED boundary tests first, then migrate in dependency order:

1. common/runtime support + simple pure/UI helpers;
2. Telegram/session/card/persona/catalog/media adapters;
3. memory/RAG/group/UI feature shells;
4. generation + commands + routing + callbacks;
5. sync/persona/identity/safety extension modules;
6. runtime facade + loader contraction;
7. full exact-head verification and whole-branch review.

A static global-dependency diagnostic is used during the migration to expose names that previously came implicitly from the shared exec namespace.

## Acceptance criteria

1. Every former runtime module except `main.py` imports successfully as an ordinary module.
2. None of those modules imports `bridge.runtime`.
3. `cards.py` is absent from runtime stages and has ordinary-import identity through `bridge.runtime`.
4. Explicit extension registration remains deterministic across runtime reloads.
5. Runtime facade exports are object-identical to canonical ordinary owners.
6. `DEFAULT_RUNTIME_STAGES` contains only `main.py`.
7. No new public-callable override allowlist exists.
8. Full unittest/audit, pytest, dependency validation, and dependency audit pass.
9. Whole-branch review finds no Critical or Important issue.

## Later sequencing

Phase 7C performs the final `main.py`/composition cutover, removes production shared `exec()`, and deletes or retires `runtime_loader.py`.

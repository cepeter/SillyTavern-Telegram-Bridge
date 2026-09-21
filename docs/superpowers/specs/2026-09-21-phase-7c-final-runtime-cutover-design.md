# Phase 7C — Final Runtime Cutover Design

Date: 2026-09-21  
Baseline upstream `main`: `e6cb2c4de6ce7b401ad6c92b6646ca8aa6d0a8cc`  
Feature branch: `refactor/phase-7c-final-runtime-cutover`

## Goal

Remove the last production shared-`exec()` runtime boundary and make startup ordinary Python composition.

At the end of Phase 7C:

- `bridge.main` is an ordinary importable module and the production startup owner;
- `sillytavern_telegram_bridge.py` imports `main` directly from `bridge.main`;
- no production module executes repository source with `exec()`;
- `bridge/runtime_loader.py` is deleted;
- `bridge.runtime` is, at most, a plain import/re-export compatibility facade with no loader, no custom module type, and no mutation-propagation behavior;
- application dependency completion is explicit ordinary composition, not a shared global namespace;
- extension registration remains explicit and deterministic.

## Non-goals

- No feature behavior redesign.
- No database schema change.
- No provider, Telegram, memory, RAG, sync, persona, group, or job semantic change.
- No removal of `bridge.runtime` import compatibility if a plain facade can preserve read-only API compatibility cheaply.
- No new service architecture beyond what is necessary to remove the loader/runtime mutation bridge.

## Final composition model

`bridge.main` participates in the existing explicit dependency declaration mechanism. Its module-level collaborators are declared against canonical ordinary owners. A composition completion pass imports and completes ordinary modules deterministically before production startup executes.

This is not the old shared namespace:

- each dependency has one declared owner;
- bindings are module-local;
- there is no source execution into another module's globals;
- no declaration may source from `bridge.runtime`;
- production does not depend on mutation of the compatibility facade.

## Runtime compatibility

`bridge.runtime` remains temporarily importable because tests and possible external callers may import names from it. It becomes a plain compatibility facade that republishes canonical objects, including startup functions from `bridge.main`.

It must not:

- subclass or replace its module object;
- intercept reads or writes;
- mirror monkey-patches into other modules;
- import or invoke a runtime loader;
- own production startup execution.

Tests that intentionally depended on runtime mutation semantics move to a test-only compatibility proxy or patch canonical owners directly.

## Extension registration

The production composition path resets/initializes extension registration explicitly, then registers Scene State, Director Goals, and Memory Curator in deterministic order. Runtime import compatibility must not be responsible for production extension state.

## Acceptance criteria

1. `import bridge.main` succeeds without importing `bridge.runtime` or `bridge.runtime_loader`.
2. The repository entry point imports `main` from `bridge.main`.
3. `bridge/runtime_loader.py` is absent.
4. No production file under `bridge/` calls built-in `exec()` on repository source.
5. `bridge.runtime` has no custom module class or mutation propagation and republishes the canonical `bridge.main.main` object.
6. Every unresolved global used by `bridge.main` is explicitly imported or declared against a canonical ordinary owner.
7. Application dependency completion remains deterministic and has no source edge to `bridge.runtime`.
8. Existing runtime-patching tests are migrated away from production mutation semantics.
9. Full unittest/audit, pytest, compile, dependency validation, and dependency audit pass.
10. Whole-branch review finds no Critical or Important issue.

## Resulting architecture

Phase 7C completes the migration begun in Phase 7:

ordinary modules → explicit module-local dependency composition → `bridge.main` startup.

The legacy path

`bridge.runtime` → `runtime_loader.py` → shared `exec()`

no longer exists.

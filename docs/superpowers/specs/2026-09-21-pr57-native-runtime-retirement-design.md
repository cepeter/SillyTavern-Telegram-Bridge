# PR 57 — Native Runtime Compatibility Retirement

Date: 2026-09-21
Baseline upstream `main`: `9e187b23a296a7f4382e057906904bdf53e12bea`

## Goal

Remove the final legacy runtime compatibility surface now that production starts
through ordinary `bridge.main` composition.

At the end of this PR:

- `bridge/runtime.py` is deleted;
- `tests/runtime_test_facade.py` is deleted;
- no production or test source imports `bridge.runtime`;
- no test imports or references `runtime_test_facade`;
- tests patch/import the module that actually owns or executes the behavior
  under test;
- production behavior is unchanged.

## Why now

Phase 7 removed shared source execution and made `bridge.runtime` a read-only
re-export facade. PR 56 removed the last import-time extension-composition side
effect. The facade now exists only for legacy imports and legacy test ergonomics.

Keeping it makes API ownership ambiguous and preserves a compatibility concept
the application no longer needs.

## Migration rule

Do not replace `bridge.runtime` with another aggregate compatibility module.

For each test reference:

1. Use the concrete module that currently owns the function/state under test.
2. For monkey-patched collaborators, patch the consumer module's local binding
   where execution actually reads it. This preserves behavior while
   `ordinary_dependencies.py` still exists.
3. Use canonical foundational modules directly for database, config, card
   content, schema, cache, performance, and runtime-context functions.
4. Import standard-library modules directly instead of reaching them through a
   compatibility namespace.
5. While `ordinary_dependencies.py` remains, tests may use
   `tests/dependency_patch.py` as a per-module patch utility. It must be given
   an explicit canonical module name, follows declared dependency edges, and
   updates ordinary direct-import copies only when object identity matches the
   canonical source. It rejects `bridge.runtime`, never propagates merely by
   attribute name, and exposes no aggregate runtime namespace. It is
   transitional test tooling to be removed with `ordinary_dependencies.py`.

## Non-goals

- Do not remove `bridge/ordinary_dependencies.py` in this PR.
- Do not redesign application modules or break cycles.
- Do not change feature behavior.
- Do not introduce a renamed replacement for the runtime facade.

## Acceptance criteria

1. `bridge/runtime.py` does not exist.
2. `tests/runtime_test_facade.py` does not exist.
3. No Python source contains `import bridge.runtime`,
   `from bridge.runtime import`, or `runtime_test_facade`.
4. Launcher and production startup remain unchanged from merged PR 56.
5. Existing behavioral tests retain equivalent patch points against concrete
   modules.
6. Full compile, unittest/audit, pytest, dependency validation, and dependency
   audit pass.
7. Whole-branch review finds no Critical or Important issue.

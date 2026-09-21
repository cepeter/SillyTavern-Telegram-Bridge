# Phase 7C — Final Runtime Cutover Implementation Plan

Date: 2026-09-21
Baseline: `e6cb2c4de6ce7b401ad6c92b6646ca8aa6d0a8cc`
Branch: `refactor/phase-7c-final-runtime-cutover`

## Execution method

Native GitHub/TDD workflow. Open a Draft PR from RED tests, implement the cutover, verify exact-head CI, then stop at explicit merge approval.

## Task 1 — RED final-boundary tests

- Assert direct ordinary import of `bridge.main`.
- Assert production entry point uses `bridge.main`.
- Assert runtime loader is gone and no production `exec()` remains.
- Diagnose unresolved globals in `bridge.main`.

## Task 2 — Make `bridge.main` ordinary

- Add postponed annotations and explicit import-time stdlib dependencies.
- Declare remaining startup collaborators against canonical ordinary owners.
- Complete the application/main dependency graph deterministically.
- Preserve startup and job behavior.

## Task 3 — Cut production entry point over

- Change `sillytavern_telegram_bridge.py` to import `main` from `bridge.main`.
- Ensure startup no longer imports `bridge.runtime`.

## Task 4 — Simplify runtime compatibility

- Replace loader-driven `bridge.runtime` with plain ordinary reexports.
- Remove custom `ModuleType` mutation/read interception.
- Preserve canonical object identity for public APIs.

## Task 5 — Retire loader

- Delete `bridge/runtime_loader.py`.
- Remove loader-specific tests and replace them with final architecture assertions.
- Assert zero production built-in `exec()` calls.

## Task 6 — Migrate legacy test patching

- Retarget production-owner tests to canonical modules, or use a test-only compatibility proxy where broad legacy tests still need shared-patch ergonomics.
- Production `bridge.runtime` must remain free of mutation propagation.

## Task 7 — Verification and review

- Full compile.
- Full unittest/audit suite.
- Full pytest suite.
- `pip check`.
- `pip-audit`.
- Exact-head source architecture audit.
- Review diff against merged 7B4 baseline.
- Mark ready only after all exact-head gates are green; do not merge automatically.

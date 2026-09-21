# PR 56 — Explicit Extension Composition

Date: 2026-09-21  
Baseline upstream `main`: `d208870ac4483880f8af216832de940a7605f0c6`

## Goal

Remove the remaining import-time mutation of the extension registry from
`bridge.main`.

Importing `bridge.main` should define startup behavior only. Extension
registration should happen explicitly when application startup is invoked.

## Scope

- Add a small ordinary composition module for deterministic extension setup.
- Move registry reset + Scene State / Director Goals / Memory Curator
  registration into an explicit function.
- Call that function from `main()` before startup uses extension-driven
  services or command routing.
- Add regression tests proving:
  - importing `bridge.main` does not mutate extension registry state;
  - importing the composition helper also has no mutation side effect;
  - explicit composition produces the expected registration order;
  - repeated explicit composition is deterministic.

## Non-goals

- Do not expand `tests/runtime_test_facade.py` to database/config/card-content
  patch propagation.
- Do not change feature behavior.
- Do not redesign the extension registry API.
- Do not alter Phase 7 dependency composition.

## Expected architecture

module import → no registry mutation  
explicit startup composition → deterministic registry setup → application run

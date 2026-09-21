# PR 56 — Explicit Extension Composition Plan

1. Add RED tests for import purity and deterministic explicit composition.
2. Add `bridge/application_composition.py` with one explicit initializer.
3. Remove import-time extension reset/registration from `bridge.main`.
4. Call the initializer at the beginning of `main()`.
5. Run full CI and review the focused diff.
6. Mark Ready for Review only after exact-head CI passes.

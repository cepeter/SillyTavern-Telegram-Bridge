# PR 57 — Native Runtime Compatibility Retirement Plan

1. Add RED architecture guards requiring both runtime compatibility files to be
   absent and banning runtime-facade imports.
2. Inventory every legacy `rt.<name>` test reference and resolve it to its
   current concrete module owner/execution binding.
3. Migrate tests in batches to explicit module imports and patch points.
4. Rewrite old Phase 7 boundary tests that asserted runtime-facade identity.
5. Delete `bridge/runtime.py` and `tests/runtime_test_facade.py`.
6. Update README to remove the legacy facade from the architecture description.
7. Run full CI, inspect failures for incorrect patch ownership, and fix only
   concrete regressions.
8. Review exact-head diff and stop at explicit merge approval.

# SDD ledger — plan: docs/superpowers/plans/2026-09-19-phase-2-versioned-migrations.md

Workspace: remote GitHub branch refactor/phase-2-versioned-migrations (GitHub-only harness; no local worktree filesystem available).
Pre-flight: Task 1 produces Migration/MigrationError/run_migrations consumed by Tasks 2-5 — interfaces match.
Pre-flight: Task 2 produces SCHEMA_MIGRATIONS/initialize_database_schema consumed by Tasks 3-5 — interfaces match.
Pre-flight: Tasks 3-4 extend the same ordered SCHEMA_MIGRATIONS tuple; Task 5 consumes the final 001-004 registry — interfaces match.
Ruling: use isolated remote feature branch plus temporary tracked ledger instead of local git worktree — harness exposes repository writes through GitHub only; ledger and plan will be removed before PR readiness — cost if wrong: branch isolation remains equivalent, but scratch history is visible until cleanup.
Task 1: complete (commits 992cfd0..82f8475, RED CI #173: missing bridge.migrations; GREEN CI #174: full workflow success)
Task 2: complete (commits e784602..92a1d05, RED CI #176: schema.py direct-import NameError; GREEN CI #177: full workflow success)
Task 3: Ruling: current baseline still had the Scene test schema guard, but the first multi-file edit removed definitions without removing call sites because the replacement pattern was over-escaped; verified exact remaining lines and removed all guards — spec requires zero request-time feature DDL/lazy schema ownership — cost if wrong: startup schema would be missing or request paths would fail.
Task 3: Ruling: Task 2 tests hardcoded a one-row ledger, which is only true before later migrations exist; changed those general bootstrap/cleanup assertions to compare against SCHEMA_MIGRATIONS while retaining the dedicated explicit 001-003 ordering test — spec requires all missing migrations at startup — cost if wrong: a registry ordering defect could be hidden only if the dedicated ordering test also failed to catch it.
Task 3: complete (commits 8db09fe..a4638b3, RED CI #179: migrations 002-003 absent/request schema missing; intermediate CI #185 exposed stale one-migration test expectations; GREEN CI #187: full workflow success)
Task 4: Ruling: the planned sync no-DDL trace treated the generic schema_migrations ledger bootstrap as sync-owned structural DDL; narrowed the assertion to forbid CREATE TRIGGER sessions_delete_sync_binding while still requiring orphan cleanup — the spec allows startup migration infrastructure and only moves sync trigger ownership out of sync_safety.py — cost if wrong: unrelated future startup migration DDL will not fail this sync-specific test, but migration-engine tests cover migration ownership separately.
Task 4: complete (commits 23ee0c3..4d97743, corrected RED CI #192: migration 004 absent and sync_safety still creates trigger; GREEN CI #195: full workflow success)
Task 5: complete (commits 0a1bf21..c45b3b3, integration tests added; GREEN CI #198: full workflow success)
Final review: self-review (no subagent tool; installed requesting-code-review skill does not expose its referenced code-reviewer.md support file)
Final: Ruling: independent reviewer/template unavailable in this harness — performed a distinct review against the approved spec, plan Review Focus, current-main diff, and full CI evidence — cost if wrong: author self-review has less independence than a fresh reviewer.
Final: minor (deferred): bridge/schema.py _ensure_job_tables() docstring still says "and clean old rows" although cleanup moved to _run_startup_database_cleanup().
Final: minor (deferred): tests/test_migrations.py test_scene_and_director_migrations_create_expected_structures now also asserts migration 004 sync trigger, so the test name is narrower than its assertions.

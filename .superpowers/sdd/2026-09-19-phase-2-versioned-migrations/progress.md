# SDD ledger — plan: docs/superpowers/plans/2026-09-19-phase-2-versioned-migrations.md

Workspace: remote GitHub branch refactor/phase-2-versioned-migrations (GitHub-only harness; no local worktree filesystem available).
Pre-flight: Task 1 produces Migration/MigrationError/run_migrations consumed by Tasks 2-5 — interfaces match.
Pre-flight: Task 2 produces SCHEMA_MIGRATIONS/initialize_database_schema consumed by Tasks 3-5 — interfaces match.
Pre-flight: Tasks 3-4 extend the same ordered SCHEMA_MIGRATIONS tuple; Task 5 consumes the final 001-004 registry — interfaces match.
Ruling: use isolated remote feature branch plus temporary tracked ledger instead of local git worktree — harness exposes repository writes through GitHub only; ledger and plan will be removed before PR readiness — cost if wrong: branch isolation remains equivalent, but scratch history is visible until cleanup.
Task 1: complete (commits 992cfd0..82f8475, RED CI #173: missing bridge.migrations; GREEN CI #174: full workflow success)
Task 2: complete (commits e784602..92a1d05, RED CI #176: schema.py direct-import NameError; GREEN CI #177: full workflow success)

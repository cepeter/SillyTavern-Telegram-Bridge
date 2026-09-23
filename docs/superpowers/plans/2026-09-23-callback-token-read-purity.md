# Callback Token Read Purity Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans task-by-task.

**Goal:** Remove database mutations and transaction ownership from token resolution.
**Architecture:** Lookup validates cache or stored values; startup maintenance owns pruning.
**Tech Stack:** Python 3.11+, sqlite3, pytest.
**Spec:** docs/superpowers/specs/2026-09-23-callback-token-read-purity-design.md

## Global Constraints
No changes to token issuance durability, schemas, routing, or production deployment.
No compatibility wrappers. Work only on the feature branch.

## Review Focus
Expired tokens must not commit unrelated pending application writes.
Wrong-chat lookup must not destroy a valid owner's token.
Wrong-kind lookup must not destroy a token for its correct route.
Cold-cache resolution must retain the same validation as warm-cache resolution.
Expiry equality keeps the existing `< now` comparison semantics.

## Task 1: Read-only resolution
**Files:** bridge/callback_tokens.py; tests/test_callback_token_read_purity.py.
**Interfaces:** Existing resolve_dynamic_callback_token(token, kind, chat_id, *, db).
- [x] Add real-SQLite regression tests with a pending probe-table insert.
  `resolve_dynamic_callback_token(...); db.rollback(); assert probe_count == 0`
- [x] Run `python -m pytest -q tests/test_callback_token_read_purity.py`.
  Expected: mutation/rollback and valid-token preservation failures on the base.
- [x] Remove the resolver's nested DELETE/commit action. Evict only expired cache
  entries; reject binding mismatches without discarding a live token.
- [x] Run the focused tests plus tests/test_card_foundations_import_island.py.
  Expected: all pass, including persistence-backed cold-cache resolution.
- [x] Run `python -m pytest -q -n 2 --dist=loadfile`, compileall, and diff --check.
  Expected: all tests pass; no compilation or whitespace failures.
- [x] Self-review against the spec and prepare the verified commit.
- [ ] Publish a focused PR from the fork and record exact-head CI status; do not merge main automatically.

## Local verification record
- RED: 8 failed, 4 passed before production edits.
- Focused: 36 passed, 65 subtests passed; process exit 0.
- Full: 751 passed, 475 subtests passed; process exit 0.
- compileall and git diff --check: passed.
- Import topology unchanged: 70 modules, 419 edges, largest SCC 30.
- Review: self-review (no independent reviewer tool in this session).
- Existing tests requiring deletion during lookup were updated to the new
  read-only contract. Token issuance and startup cleanup were not modified.

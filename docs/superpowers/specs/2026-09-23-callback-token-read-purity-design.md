# Callback token read-purity design

Date: 2026-09-23
Base: a06a25a14887702265c37374c7b3bf1ae1dc2652
Parent: Runtime Architecture Migration master design, transaction ownership rules.

## Intent and scope
Make `resolve_dynamic_callback_token()` a database read. Resolving an expired,
wrong-kind, or wrong-chat token returns None without deleting rows, committing,
rolling back, or changing a caller-owned transaction. Keep valid token resolution,
chat binding, TTL comparison, and persistence-backed recovery unchanged.

## Ownership
Token issuance remains the existing explicit write path in this small PR.
Expired cache entries may be evicted from the in-memory cache. A binding mismatch
must not invalidate a still-valid token for its legitimate caller.
Persisted expiration cleanup remains owned by `schema._run_startup_database_cleanup()`;
no request-path maintenance API or schema change is required.

## Options considered
Removing only commit leaves an unexpected DELETE in a getter: rejected.
Moving deletion to every caller duplicates maintenance and touches routing: rejected.
A read-only resolver with existing maintenance ownership is selected.

## Independent delivery
Only `bridge/callback_tokens.py`, its new focused tests, the two superseded assertions in
`tests/test_card_foundations_import_island.py`, and this PR's documents
change. This work does not touch the ConversationService branch. No broad helper
commit removal is authorized without use-case-specific durability evidence.

## Acceptance
Real SQLite tests must cover cold/warm caches, expiry, kind/chat mismatches,
an unrelated pending write that still rolls back, valid and missing tokens,
and the existing inclusive expiry boundary. Observe regression failures before
editing production. Run focused and full tests, compileall, and diff checks.
Compare import graph metrics to the baseline; this change should not alter edges.

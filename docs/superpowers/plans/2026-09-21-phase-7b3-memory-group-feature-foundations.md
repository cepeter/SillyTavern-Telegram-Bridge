# Phase 7B3 — Memory / Group Feature Foundations Implementation Plan

Date: 2026-09-21
Baseline: `2b340b23dc48149de9e9a58c064e375ff6d2f0e0`
Branch: `refactor/phase-7b3-memory-group-feature-foundations`

## Execution method

Native GitHub workflow. Use the feature branch plus Draft PR GitHub Actions as the exact-SHA RED/GREEN runner. Do not merge automatically.

## Task 1 — Config and network security

1. Add RED architecture tests for canonical memory/RAG config and `bridge.network_security`.
2. Move approved constants into `bridge.config`; make `common.py` re-export them.
3. Extract `validate_provider_endpoint` and `strict_urlopen` from `generation.py`.
4. Explicitly export canonical functions through `bridge.runtime`.
5. Run CI to GREEN.

## Task 2 — Hindsight memory backend

1. Add RED tests for independent `bridge.memory_backend` import, single lock-state ownership, and runtime object identity.
2. Move Hindsight backend/recall functions and state from `memory.py`.
3. Keep stale-guard composition, retain/purge facade, command UI, and summary generation in `memory.py`.
4. Verify Memory Curator still resolves the imported backend helpers through the shared compatibility namespace.
5. Run CI to GREEN.

## Task 3 — RAG core

1. Add RED tests for independent `bridge.rag_core` import and shell ownership.
2. Move every non-command Data Bank/RAG function into `rag_core.py`.
3. Keep only `handle_data_bank_command` in `rag.py`, importing canonical core APIs.
4. Retarget tests that patch legacy RAG config seams to canonical ownership where required.
5. Run CI to GREEN.

## Task 4 — Group core

1. Add RED tests for independent `bridge.group_core` import and canonical runtime exports.
2. Move persistence/turn/character-resolution foundations into `group_core.py`.
3. Keep Telegram panels, callbacks, session wizard execution, Director model composition, and command messaging in `groups.py`.
4. Verify manual turn gating, group persistence, contextual speaker selection, and idempotent group writes.
5. Run CI to GREEN.

## Task 5 — Permanent boundary guards and review

1. Assert none of the four ordinary modules is exec-loaded.
2. Assert no moved definitions remain in legacy shells.
3. Assert no new runtime/common/Telegram/generation dependency enters ordinary foundations.
4. Run full CI and dependency audit on the exact final head.
5. Review the whole branch against the design and document any residual minor issue.
6. Mark PR ready only when exact-head verification is green; leave merge to explicit user approval.

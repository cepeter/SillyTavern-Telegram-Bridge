# Phase 7B4 — Application / UI Ordinary-Import Boundary Implementation Plan

Date: 2026-09-21
Baseline: `f13fca5bb8eeaa4a2f3cfaf7280e772f03215737`
Branch: `refactor/phase-7b4-application-ui-import-boundary`

## Execution method

Native GitHub/TDD workflow. Keep the PR Draft through RED and intermediate migration steps. Do not merge automatically.

## Task 1 — RED boundary and dependency inventory

- Add Phase 7B4 boundary tests.
- Add a static global-name diagnostic for remaining loader modules.
- Record the intentional RED CI checkpoint.

## Task 2 — Runtime support and leaf UI modules

- Remove `common.py` from exec loading and expose it through normal imports.
- Convert leaf modules with small dependency surfaces: cards/persona panel, language, greetings, help details, update, expression helpers.
- Preserve exact function behavior.

## Task 3 — Telegram/session/content UI adapters

- Convert `telegram.py`, `help.py`, `input_flows.py`, `catalog.py`, `image_generation.py`, `media.py`, memory/RAG/group shells, and related panels.
- Resolve collaborators from canonical 7A–7B3 owners or explicit ordinary modules.

## Task 4 — Generation and command routing

- Convert `generation.py`, `commands.py`, `status_panels.py`, `command_routes.py`, `message_commands.py`, `callbacks.py`, and `panel_callback_routes.py`.
- Retarget tests that intentionally monkey-patch implementation seams to canonical modules where lookup ownership changes.

## Task 5 — Remaining adapters and extension registration

- Convert `sync_core.py`, `sync_api.py`, `persona_sync.py`, `character_identity.py`, and `session_naming.py`.
- Convert Scene State, Director Goals, and Memory Curator to explicit deterministic registration functions.
- Ensure runtime reload restores the same registry snapshot.

## Task 6 — Runtime facade and loader contraction

- Ordinary-import and republish all migrated public APIs from `bridge.runtime`.
- Reduce `DEFAULT_RUNTIME_STAGES` to `main.py` only.
- Assert no migrated file is exec-loaded and no migrated module imports `bridge.runtime`.

## Task 7 — Verification/review

- Full compile.
- Full unittest/audit regression suite.
- Full pytest suite.
- `pip check`.
- `pip-audit`.
- Compare moved function bodies where practical to baseline.
- Whole-branch architecture review.
- Mark ready only on exact-head green CI; leave merge to explicit user approval.

# Phase 7B3 — Memory / Group Feature Foundations Ordinary-Import Boundary Design

Date: 2026-09-21
Baseline upstream `main`: `2b340b23dc48149de9e9a58c064e375ff6d2f0e0`
Feature branch: `refactor/phase-7b3-memory-group-feature-foundations`

## Status

Approved for execution by the user's explicit instruction to proceed after the Phase 7B2 merge. This phase uses the same Native GitHub/TDD migration discipline as 7A–7B2.

## Context

Phase 7B2 established ordinary ownership for card/content, panel helpers, callback tokens, and runtime context while intentionally leaving UI/generation-bound shells in the shared runtime.

Fresh dependency inventory after the 7B2 merge shows three feature areas with substantial logic that can be detached without pulling Telegram routing or text generation into this phase:

- Hindsight memory backend and recall/persistence mechanics in `memory.py`;
- Data Bank parsing/indexing/retrieval in `rag.py`;
- group state, turn ownership, character resolution, and turn advancement in `groups.py`.

The same inventory shows that command rendering, Telegram panels, summary generation, Group Director model execution, session-wizard UI, Scene State extraction, Memory Curator generation, and callback routing still depend on later loader modules. Those remain for Phase 7B4.

RAG and Hindsight also share provider-URL validation currently defined in `generation.py`. That helper is infrastructure policy rather than generation behavior and is therefore extracted here as a small ordinary dependency.

## Goal

Create canonical ordinary-import owners for memory backend, RAG core, group state/turn logic, and outbound provider URL safety while preserving all current runtime behavior.

At the end of Phase 7B3:

- `bridge.config` owns Hindsight, summary, and RAG startup/default values;
- `bridge.network_security` owns `validate_provider_endpoint` and `strict_urlopen`;
- `bridge.memory_backend` owns Hindsight client/replay/recall/persistence foundations and its process-level session-lock state;
- `bridge.rag_core` owns Data Bank extraction, indexing, retrieval, embedding, versioning, and RAG prompt/citation helpers;
- `bridge.group_core` owns group state persistence, manual-user turn gating, character resolution/labels, current-speaker selection, and turn advancement;
- `memory.py`, `rag.py`, and `groups.py` import/re-export those canonical foundations and retain only their generation/UI/command compatibility responsibilities;
- `generation.py` consumes canonical URL-security helpers rather than defining them;
- `bridge.runtime` explicitly re-exports moved public APIs as the canonical function objects;
- none of the four new ordinary modules is a runtime stage.

## Non-goals

Phase 7B3 does not:

- retire `memory.py`, `rag.py`, or `groups.py` from `DEFAULT_RUNTIME_STAGES`;
- migrate Telegram request/sending code;
- migrate summary generation or `generate_text`;
- migrate Group Director model execution;
- migrate group setup/session wizard UI;
- migrate Scene State or Memory Curator model execution;
- migrate Director Goals command UI;
- migrate general command/callback/panel routing;
- retire the residual `cards.py` shell;
- delete `runtime_loader.py`;
- change memory, RAG, group, provider-security, or user-visible behavior.

## Canonical configuration

Move these existing startup values from local ownership in `common.py` to `bridge.config`, preserving values and environment semantics:

`HINDSIGHT_DEFAULT_URL`, `HINDSIGHT_RECALL_MAX_TOKENS`, `HINDSIGHT_CONTEXT_MAX_CHARS`, `HINDSIGHT_RETAIN_MAX_MESSAGES`,
`SUMMARY_TRIGGER_MESSAGES`, `SUMMARY_RECENT_MESSAGES`, `SUMMARY_MAX_CHARS`, `SUMMARY_MAX_OUTPUT_TOKENS`, `SUMMARY_UPDATE_INTERVAL`,
`RAG_MAX_FILE_BYTES`, `RAG_CHUNK_CHARS`, `RAG_CHUNK_OVERLAP`, `RAG_MAX_CONTEXT_CHARS`, `RAG_SUPPORTED_SUFFIXES`,
`RAG_EMBEDDING_URL`, `RAG_EMBEDDING_MODEL`, `RAG_EMBEDDING_DIMENSIONS`, `RAG_MAX_EXTRACTED_CHARS`, `RAG_MAX_PDF_PAGES`, `RAG_PDF_PARSE_TIMEOUT_SECONDS`.

`common.py` re-exports them for the remaining legacy runtime.

## Network-security ownership

Create `bridge/network_security.py` with the current `validate_provider_endpoint` and `strict_urlopen` implementations. It may depend only on stdlib `os`, `urllib.parse`, and `urllib.request`.

`generation.py` imports those functions. `memory_backend.py` and `rag_core.py` consume them directly.

## Memory backend boundary

Create `bridge/memory_backend.py` for the Hindsight-specific backend and state:

- bank/tag/document-id helpers;
- short-lived Hindsight client creation and cleanup;
- per-session Hindsight lock ownership;
- document mapping persistence;
- session-scoped purge backend;
- memory mode/scope and recall helpers;
- retain backend mechanics;
- stale-guard support callbacks;
- explicit `remember_fact`.

The stale guard itself remains composed in `memory.py` because its background-submission collaborator is still owned by the legacy runtime. `retain_session_memory` and `purge_hindsight_session` therefore remain shell-owned in this phase.

There must be exactly one `_HINDSIGHT_SESSION_LOCKS` owner.

## RAG core boundary

Create `bridge/rag_core.py` for every current `rag.py` function except the Telegram-facing `handle_data_bank_command`.

The core imports canonical RAG config, canonical URL-security helpers, database transaction/meta helpers, and `bridge.rag_retrieval`. It must not import `bridge.runtime`, `bridge.common`, or Telegram modules.

`rag.py` becomes a thin command shell that imports/re-exports the canonical core API.

## Group core boundary

Create `bridge/group_core.py` for:

- `group_state`;
- `group_user_turn_allowed`;
- `claim_group_user_turn`;
- `pass_group_user_turn`;
- `group_setup_state`;
- `group_character_option_label`;
- `save_group_state`;
- `resolve_character_file`;
- `group_member_labels`;
- `group_current_speaker`;
- `advance_group_turn`.

The module depends on `bridge.repositories`, `bridge.database.write_transaction/get_meta/set_meta` as required, and canonical `bridge.card_content`. It does not import Telegram, generation, runtime, or common.

The session-wizard operations that call `create_session`, `load_session`, or Telegram UI remain in `groups.py`.

## Runtime facade

`bridge.runtime` explicitly imports/re-exports moved public functions from `network_security`, `memory_backend`, `rag_core`, and `group_core` before legacy loading. Shell imports must bind the same objects so no new public-callable override appears.

Private mutable state remains canonical only in its owning ordinary module.

## Loader boundary

The new modules:

- `network_security.py`
- `memory_backend.py`
- `rag_core.py`
- `group_core.py`

must never appear in `DEFAULT_RUNTIME_STAGES`.

The relative ordering of existing legacy runtime files remains unchanged in 7B3.

## Acceptance criteria

1. All four new modules import independently without loading `bridge.runtime` or `bridge.common`.
2. `memory_backend.py` has one Hindsight session-lock registry and no Telegram/generation dependency.
3. `rag_core.py` contains no Telegram/runtime/common dependency and `rag.py` no longer defines migrated functions.
4. `group_core.py` contains no Telegram/generation/runtime/common dependency and `groups.py` no longer defines migrated functions.
5. `generation.py` no longer defines the URL-security functions.
6. Runtime facade exports are object-identical to canonical ordinary owners.
7. Existing memory/RAG/group behavior tests remain green.
8. The full CI suite, dependency check, and dependency audit pass.
9. Whole-branch review finds no Critical or Important issue.

## Later sequencing

- Phase 7B4: generation/commands/callback/UI routing and retirement of residual feature/UI shells, including `cards.py`.
- Phase 7C: final main/runtime composition cutover and deletion of the production shared `exec()` loader.

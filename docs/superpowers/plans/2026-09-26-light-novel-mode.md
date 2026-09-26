# Light Novel Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** Ship the approved Normal/Light Novel setup wizard, strict opening lifecycle, and durable 2–4 action choices through a reviewed PR.
**Architecture:** SQL-only repositories own state, services own transactions and provider calls, and Telegram adapters own rendering/ingress. Existing story, memory, RAG, Humanizer, provider routing and durable message workers remain authoritative. New/reset conversation epochs prevent callbacks and queued work crossing reset boundaries.
**Tech Stack:** Python 3.11, SQLite, existing provider and Telegram ports; no new dependencies.
**Spec:** `docs/superpowers/specs/2026-09-26-light-novel-mode-design.md`

## Global Constraints
- `/character`: Character → Normal/Light Novel → A/B/C only for Light Novel → Persona → World → System Prompt → Session → Apply.
- `/start` only opens greetings; after `/new` or `/reset`, dialogue returns `Please use /start command.` without a model call or transcript entry.
- Reset preserves configuration; it invalidates choices and returns to unstarted. Retire plain `start` alias.
- Choice count is selected once uniformly from 2, 3, 4 and survives retries/restarts.
- Normal mode makes no Light Novel model calls; group-specific Light Novel is out of scope.
- Network calls never run inside write transactions; consume-choice plus enqueue is atomic.
- No changes to protected system-prompts examples, dependencies, release tags or live data during development.
- User approved continuous native execution, pushes, PR creation and merges; report only at finish.

## Review Focus
- Old opening/choice callbacks after reset or another session/configuration selection: reject via epoch/identity checks.
- Empty or malformed structured model output: never leak protocol text or duplicate committed narrative.
- Choice text that resembles a slash command: reject; clicking a narrative action cannot execute administration.
- Pending persona/session-name/optimizer input while unstarted: allow management input but block ordinary dialogue.
- Network failure after durable commit: preserve greeting/story and choice counts; retry delivery/generation only, not narrative.

## File map and shared interfaces
- `conversation_schema.py`: migration 2, lifecycle backfill, durable choices and cleanup triggers.
- `conversation_lifecycle.py`: `conversation_state(db,chat_id,session_id)->ConversationState`, `reset_conversation`, `require_started`, pending-input classification; no Telegram imports.
- `light_novel_repository.py`: immutable ChoiceSet reads and transactional reserve/attach/claim/finish/consume/invalidate; no commits/network.
- `light_novel_format.py`: bounded structured envelope and choices parsing/prompt instructions.
- `light_novel_service.py`: `prepare_turn(...)`, `attach_turn(...)`, `ensure_choices(...)`; provider and task routing, no Telegram.
- `light_novel_panels.py`: render/close/restore choice panels, errors remain cosmetic.
- `light_novel_callbacks.py` and `light_novel_jobs.py`: authorized durable selection/retry via existing JobService and workers; no fabricated Telegram updates.
- `conversation_setup.py`: actor-scoped draft coordinator and atomic validated Apply.
- `conversation_setup_panels.py` / `conversation_setup_callbacks.py`: selectors using native resource inventories, Back/Cancel/Apply.
- Existing session, greeting, generation, reset, ingress, recovery, command, help, and static-policy owners receive narrow integration hooks.

### Task 1: Lifecycle and durable storage
**Files:** create lifecycle/schema/repository modules; modify `schema.py`, `session_core.py`; tests `test_light_novel_storage.py`.
**Interfaces:** ConversationState(mode,strategy,started,epoch); ChoiceSet nonce/count/actor/epoch/turn key; writes join caller transactions.
- [ ] Write tests for fresh session unstarted, one-time transcript backfill, reset preservation, rollback, random-count reservation, stale/duplicate consumption, deletion cleanup.
- [ ] Run `pytest -q tests/test_light_novel_storage.py`; observe missing-feature failures.
- [ ] Implement migration, typed state, repository operations. Reserve by stable turn key before provider execution; synthetic message IDs use negative choice-set IDs.
- [ ] Re-run storage and migration/session tests; expected pass.
- [ ] Commit task and log evidence.

### Task 2: Choice contracts and A/B/C provider strategies
**Files:** create format/service modules; tests `test_light_novel_generation.py`.
**Interfaces:** `prepare_turn(db,chat_id,session,turn_key,actor_id='')->ChoiceSet|None`; `attach_turn(db,record,assistant_rowid,choices=None)`; `ensure_choices(db,record,session,fields,provider_port,app_settings, retry=False)->ChoiceSet`.
- [ ] Write tests forcing counts 2/3/4, valid envelope, malformed choice tail with usable story, slash-command rejection, bounded JSON, preserved count, A inline zero second calls, B utility, C story, provider failure/retry and transaction purity.
- [ ] Run tests and observe missing-feature failures.
- [ ] Implement structured story + choices extraction before prose transforms; choice-only calls receive bounded history, card and language context; use lease/CAS for concurrent retries.
- [ ] Verify all generation/storage tests; commit.

### Task 3: Setup wizard and dedicated command
**Files:** create setup/service/panel/callback modules; modify character callback, command routing, callback dispatch; tests `test_conversation_setup.py`.
**Interfaces:** draft state bound to actor, source session/epoch, nonce and stage; validate resources at final apply; target session must be unstarted.
- [ ] Test Normal skip, A/B/C required, world multiselect/Off, no intermediate mutation, apply atomicity, stale actor/stage/resource/session rejection, Back/Cancel and session creation.
- [ ] Observe red tests, then implement native inventory selection without invoking existing mutating entity callbacks.
- [ ] Expose `/lightnovel` as the dedicated current-session mode/status panel, not `/settings`, retaining the user's earlier command constraint; setup remains `/character`.
- [ ] Verify setup tests and existing character/group management; commit.

### Task 4: Strict opening and reset integration
**Files:** modify command routes, greetings, conversation callbacks, message ingress/prepare, image/voice paths, reset; tests `test_conversation_opening.py` and updated onboarding fixtures.
**Interfaces:** opening commit and started flag in one transaction; greeting callback epoch; management input exemption; no story calls before start.
- [ ] Test pre-start dialogue and media blocked; slash management/name inputs usable; Default/Alternate once; stale chooser after reset rejected; delivered greeting failure recoverable without second insertion.
- [ ] Observe red, implement strict guards both ingress and worker boundary; group behavior remains unchanged.
- [ ] Reset invalidates sets and increments epoch in existing reset transaction; new session initializes false; aliases and old readiness calls retired.
- [ ] Verify lifecycle/onboarding/management/reset tests; commit.

### Task 5: Story integration, durable choice ingress and recovery
**Files:** generation/message/media/recovery owners, new panel/callback/job adapters; tests `test_light_novel_flow.py`.
**Interfaces:** story + choice metadata commit together; selection consumes set and enqueues one ordinary generation job atomically; queued job fixes selected session/model/epoch/actor.
- [ ] Test opening immediate choices, all A/B/C turns, choice → one user row, manual input invalidation, double-click, nonce spoof/actor/session mismatch, restart before execution, immutable retry count, panel/network failures, regeneration and reset invalidation.
- [ ] Observe red and integrate after existing language/Humanizer render boundaries, with A raw streaming suppressed.
- [ ] Reuse existing generation worker and recovery; do not recurse from callback into model. Retry Choices gets a durable choice-only job and claim.
- [ ] Verify integrated tests plus existing job/worker/media/session regressions; commit.

### Task 6: Documentation, architecture policy and whole-branch review
**Files:** README, help, CHANGELOG, static policy, relevant fixtures; no release/tag changes.
- [ ] Update help and docs for strict start, dedicated `/lightnovel`, strategies, reset and retry limitations.
- [ ] Add new focused owners to type/import policy; preserve zero cycles.
- [ ] Run dependency-lock check, pip check, Ruff lint/format, static architecture, mypy and full pytest with coverage; expected all pass with unchanged 68% floor.
- [ ] Audit full diff and acceptance matrix, including negative synthetic IDs and command bypass prevention. Record review evidence and all residual limitations.
- [ ] Commit, push, create PR, inspect exact-head CI/CodeQL and review feedback. Fix blockers with tests.
- [ ] Merge only the verified head; verify merge tree, post-merge checks, clean up own temporary branch/workspace. Leave live deployment unchanged unless explicitly requested.

# Phase 6G — Durable Operation Recovery Design

Date: 2026-09-21  
Baseline upstream `main`: `49c2796f66581ad4ecd1cb85c89b70c6c2b618bf`  
Feature branch: `refactor/phase-6g-durable-operation-recovery`

## Status

Approved architecture and behavior design. This document defines the Phase 6G cutover only. Implementation planning and code changes follow after explicit review of this spec.

## Context

Phases 6A–6F progressively retired late runtime overrides by moving ownership into canonical modules or explicit ordinary-import adapters. After Phase 6F, `bridge/recovery.py` is the remaining runtime override module with eight public callable replacements:

- `begin_operation`
- `regenerate_last`
- `continue_last`
- `regenerate_edited_turn`
- `process_message`
- `sync_status_text`
- `send_sync_menu`
- `handle_sync_callback`

Phase 6G retires the first five durable-operation overrides. The three Sync UI overrides remain intentionally in `recovery.py` for Phase 6H.

The current late override contains behavior that is stricter than the earlier canonical implementations. The goal is therefore not to delete `recovery.py` mechanically, but to move the currently effective behavior into explicit canonical owners without changing crash recovery semantics.

## Goal

Make durable-operation crash recovery explicit, ordinarily imported, and canonically owned while preserving the currently effective behavior exactly.

After Phase 6G:

- `database.py` canonically owns durable operation persistence primitives.
- `generation.py` canonically owns regenerate and continue recovery behavior.
- `commands.py` canonically owns edited-turn recovery behavior.
- `message_commands.py` canonically owns message-level recovery routing and reset recovery.
- `bridge/operation_recovery.py` provides only shared ordinary-import recovery mechanics.
- `recovery.py` retains only the three Sync UI overrides reserved for Phase 6H.
- `runtime_loader.py` no longer allowlists any durable-operation override.
- `_ORIGINAL_PROCESS_MESSAGE` and `_OPERATION_CONTEXT` are removed with no replacement.

## Non-goals

Phase 6G does not redesign:

- `JobService` or its queued/scheduled/running/done/failed state machine.
- background scheduling, admission, executor ordering, or backlog recovery.
- callback operation ownership.
- native edit worker fallback rules.
- schema or migrations.
- Sync UI, SyncService, or Sync callback behavior.
- memory retention/purge internals.
- Persona behavior.
- Telegram delivery APIs.
- provider generation APIs.
- command syntax or routing semantics.

No new global service locator, ContextVar, late override, `_ORIGINAL_*` capture, schema phase, or application-level `OperationService` is introduced.

## Two distinct durability state machines

Phase 6G preserves the separation between durable jobs and durable operations.

### JobService lifecycle

```text
queued -> scheduled -> running -> done/failed
```

This lifecycle is owned by `JobService` and worker orchestration. Phase 6G must not move or reinterpret these transitions.

### Operation recovery lifecycle

For regen, continue, edit, and generic message delivery:

```text
in_progress -> local_committed -> external delivery -> applied
                         |
                         +-- crash/failure -> retry delivery
```

Here, "external delivery" is a behavioral boundary, not a phase that every operation persists. Generic message recovery currently records `external_delivered` before `applied`; regen, continue, and edit successfully deliver and then finish directly as `applied`. Phase 6G preserves those existing differences rather than normalizing them.

For reset:

```text
in_progress -> memory_purged -> local_committed -> applied
```

These operation states exist to make local state mutation idempotent across process crashes and external-delivery failures. They are not job states.

## Architectural choice

Use distributed canonical ownership with one small ordinary-import helper adapter.

Rejected alternatives:

1. Centralize all recovery orchestration in `operation_recovery.py`. This would mechanically remove the runtime override but create a new cross-domain orchestration owner and weaken the canonical ownership pattern established in earlier phases.
2. Duplicate recovery helpers inside each canonical module. This removes a shared helper but duplicates payload persistence, Telegram-ID cleanup, phase guards, and finish behavior.

The selected design keeps domain orchestration in its natural owner and only shares recovery mechanics.

## Component boundaries

### `bridge/database.py`

Canonical owner of durable operation persistence primitives:

- `operation_phase(db, operation_id)`
- `set_operation_phase(db, operation_id, kind, phase)`
- `begin_operation(db, operation_id, kind)`
- `operation_was_applied(db, operation_id)`
- `record_operation(db, operation_id, kind)`

The currently effective hardened `begin_operation` implementation from `recovery.py` becomes the canonical implementation here.

Required behavior:

- `operation_id is None` returns `True`.
- insertion uses `INSERT OR IGNORE`.
- the insertion and immediate commit execute through `run_write_txn`.
- an inserted operation is `in_progress`.
- if the row already exists, return `False` only when the operation is already `applied`; otherwise return `True`.
- no provider or Telegram I/O occurs while the short write lock is held.

`database.py` must not import `operation_recovery.py`.

### `bridge/operation_recovery.py`

New ordinary-import helper. It must not be listed in `DEFAULT_RUNTIME_STAGES`.

It owns only shared crash-recovery mechanics and receives external behavior through explicit collaborators rather than importing shared-runtime implementation modules.

A focused frozen adapter is preferred:

```python
@dataclass(frozen=True)
class OperationRecovery:
    operation_phase: Callable[..., str]
    begin_operation: Callable[..., bool]
    record_operation: Callable[..., None]
    run_write_txn: Callable[..., object]
    get_meta: Callable[..., str]
    telegram_request: Callable[..., object]
    delete_outgoing_message_row: Callable[..., None]
    log_info: Callable[..., None]
```

The exact constructor typing may be adjusted for clarity during implementation, but the dependency direction is fixed: canonical runtime modules configure the helper; the helper does not own domain routing.

Expected mechanical methods:

- `begin_or_recover(db, operation_id, kind, deliver_recovered) -> bool`
- `set_payload(db, operation_id, payload) -> None`
- `get_payload(db, operation_id) -> dict`
- `finish(db, operation_id, kind) -> None`
- `outgoing_ids_after(db, chat_id, session_id, rowid) -> list[str]`
- `prepare_delivery(db, token, chat_id, assistant_rowid, operation_id) -> None`
- `selected_variant_index(db, chat_id, session_id, user_rowid) -> int`
- `latest_user_row(db, chat_id, session_id)`
- `latest_assistant_row(db, chat_id, session_id)`

Private helpers may decode stored Telegram message IDs and perform best-effort Telegram deletion.

The helper must not know about:

- slash commands,
- prompts,
- provider generation,
- MemoryService,
- PersonaService,
- JobService,
- Sync UI,
- reset semantics.

Each canonical consumer creates a private configured instance rather than depending on an instance left in the shared runtime namespace by another module. This prevents a replacement load-order dependency.

### `bridge/generation.py`

Canonical public owner of:

- `regenerate_last`
- `continue_last`

These functions absorb the currently effective behavior from `recovery.py`.

They continue to resolve `memory_service` as today and continue to accept `persona_service` where the effective implementation does.

A small private generation helper may preserve the current shared generate + RAG citation + response-render sequence if it keeps behavior unchanged.

### `bridge/commands.py`

Canonical public owner of:

- `regenerate_edited_turn`

The canonical implementation must preserve the current effective atomic local mutation boundary:

- invalidate session summary,
- update the edited user row,
- delete later branch rows,
- insert the replacement assistant row,
- persist the selected response variant,
- set `local_committed`,
- commit.

These changes must remain within one short serialized local write boundary before external cleanup, memory retention, and Telegram delivery.

`edit_last_user` and `edit_telegram_user_message` remain callers of this canonical function.

### `bridge/message_commands.py`

Canonical public owner of:

- `process_message`
- `reset_session`

`process_message` absorbs the currently effective late-wrapper recovery ordering before normal message processing.

`reset_session` retains its existing recovery sequence; it is not rewritten into the regen/continue/edit flow.

### `bridge/recovery.py`

After Phase 6G this file is a temporary Sync UI compatibility module only:

- `sync_status_text`
- `send_sync_menu`
- `handle_sync_callback`

The durable-operation definitions and helpers are removed.

### `bridge/runtime_loader.py`

Keep the existing `recovery_overrides` stage only because Phase 6H still needs the Sync UI overrides.

Its `recovery.py` allowlist becomes exactly:

- `sync_status_text`
- `send_sync_menu`
- `handle_sync_callback`

No durable-operation callable remains allowlisted.

## Dependency direction

```text
database.py
   ^
   | operation persistence primitives
   |
operation_recovery.py
   ^                 ^                    ^
   |                 |                    |
generation.py     commands.py      message_commands.py
regen/continue       edit          routing/reset
```

This diagram describes logical dependency direction. The ordinary helper consumes injected collaborators and must not create a reverse import from `database.py`.

## `_OPERATION_CONTEXT` and `_ORIGINAL_PROCESS_MESSAGE`

Current-main tracing shows:

- `_OPERATION_CONTEXT` is created in `recovery.py`.
- it is set/reset only by `recovery.py::process_message`.
- no other code reads it.

It is therefore dead coupling for the durable-recovery behavior and is removed without replacement.

`_ORIGINAL_PROCESS_MESSAGE` exists only to preserve the pre-override function while the late wrapper replaces it. Once the wrapper logic is moved into canonical `message_commands.py::process_message`, the capture is unnecessary and is removed.

## Recovery payload

The existing metadata key format is preserved:

```text
operation_payload:<operation_id>
```

The helper stores compact JSON in `meta`.

Required semantics:

- `operation_id is None`: setting is a no-op and reading returns `{}`.
- missing payload returns `{}`.
- malformed JSON returns `{}`.
- a non-object decoded JSON value returns `{}`.
- finishing an operation marks it applied and deletes the payload within the same short serialized write operation.

Payload contents remain command-specific. For example:

- regen/edit: old Telegram IDs plus user row ID.
- continue: old Telegram IDs plus assistant row ID.

No new table or migration is added.

## Shared phase guard

The shared phase guard preserves the current effective rules:

1. `operation_id is None` -> normal path.
2. phase `applied` -> skip.
3. phase `local_committed` -> invoke the caller-supplied recovery delivery hook and skip normal mutation/generation.
4. otherwise call canonical `begin_operation`.

The helper returns whether the caller should execute the normal path.

If a recovery delivery hook raises, the exception propagates and the operation remains retryable.

## Regen flow

Normal path:

1. Resolve MemoryService.
2. Run shared operation guard.
3. Load transcript and latest user turn.
4. Build RAG/memory/persona-aware prompt exactly as today.
5. Generate and render while no SQLite write transaction is held.
6. Collect Telegram IDs belonging to assistant rows after the target user row.
7. Persist the recovery payload.
8. In one short serialized local write:
   - delete later message rows,
   - insert regenerated assistant row,
   - save/select the new response variant,
   - set operation phase to `local_committed`,
   - commit.
9. Best-effort delete old stored Telegram deliveries.
10. Retain memory.
11. Send the regenerated response with the existing visible prefix and variant number.
12. Finish the operation and delete its payload.

Recovery from `local_committed`:

1. Load latest user and assistant rows.
2. If either is missing, raise `RuntimeError("regen recovery state is incomplete")`.
3. Prepare delivery recovery:
   - best-effort delete delivery currently recorded on the assistant row,
   - best-effort delete old IDs from the operation payload.
4. Resolve the currently selected response-variant index.
5. Redeliver the persisted assistant content without provider generation.
6. Finish the operation.

The visible message remains:

```text
♻️ Regenerated response (variant N)

<content>
```

## Continue flow

Normal path:

1. Resolve MemoryService.
2. Run shared operation guard.
3. Load transcript and latest assistant row.
4. Build the existing continuation prompt.
5. Generate and render outside any SQLite write transaction.
6. Combine existing assistant content with the generated continuation exactly as today.
7. Capture currently stored Telegram IDs for the assistant row.
8. Persist operation payload.
9. In one short serialized local write:
   - update the assistant message content,
   - update the selected response variant by `user_rowid`,
   - set `local_committed`,
   - commit.
10. Prepare delivery recovery/cleanup.
11. Retain memory.
12. Send the combined persisted content with the existing visible prefix.
13. Finish the operation.

Recovery from `local_committed`:

- never call the provider,
- load the latest assistant row,
- raise `RuntimeError("continue recovery state is incomplete")` if absent,
- clean recorded/stored old Telegram IDs best-effort,
- redeliver persisted combined content,
- finish only after successful delivery.

The visible message remains:

```text
↪️ Continued response

<combined content>
```

## Edited-turn flow

Normal path:

1. Resolve MemoryService.
2. Run shared operation guard.
3. Validate target user row.
4. Build the existing memory/RAG/persona-aware prompt.
5. Generate and render outside the local write transaction.
6. Capture outgoing Telegram IDs after the edited user row.
7. Persist operation payload.
8. In one short serialized local write:
   - delete the session summary,
   - update edited user content,
   - delete later messages,
   - insert replacement assistant row,
   - save/select response variant for the edited user row,
   - set `local_committed`,
   - commit.
9. Best-effort delete old stored Telegram deliveries.
10. Retain memory.
11. Send the replacement response with the existing visible prefix.
12. Finish the operation.

Recovery from `local_committed`:

- never call the provider,
- require latest user and assistant rows,
- raise `RuntimeError("edit recovery state is incomplete")` if either is missing,
- clean old delivery state best-effort,
- redeliver persisted assistant content,
- finish only after successful delivery.

The visible message remains:

```text
✏️ Edited message regenerated.

<content>
```

## Generic `process_message` recovery ordering

The canonical `message_commands.py::process_message` must preserve command-specific recovery before the existing generic committed-response shortcut.

Required order when `operation_id` is `local_committed`:

1. Normalize command using the current operation-command behavior.
2. Resolve the queued/active session.
3. For `/regen`, dispatch to canonical `regenerate_last`.
4. For `/continue`, dispatch to canonical `continue_last`.
5. For `/edit`, extract the replacement text and dispatch through `edit_last_user`.
6. Only for all other inputs, continue into the existing generic `local_committed` recovery behavior already present in `message_commands.py`.
7. If not recovered, continue with the normal command/message flow.

This ordering is mandatory. Generic recovery first would lose regen/continue/edit-specific cleanup and response-variant behavior.

Command normalization must preserve the currently effective handling of:

- `/regen`
- `/regen@BotName`
- `@mention /regen`-style normalized queued text where supported by the current parser
- corresponding continue/edit forms.

No command syntax expansion is part of 6G.

## Generic committed-response recovery

The existing generic recovery behavior in `message_commands.py` remains unchanged for non-special operations:

- load latest committed assistant response for the selected session,
- redeliver it,
- set the operation to `external_delivered`,
- record the operation as applied,
- commit,
- return.

Phase 6G only changes where the command-specific pre-routing happens.

## Reset recovery

`reset_session` remains canonical in `message_commands.py` and preserves its current phases:

```text
in_progress -> memory_purged -> local_committed -> applied
```

Required behavior:

- applied operation -> no-op.
- first execution purges the active session's Hindsight memory.
- after successful purge, persist `memory_purged`.
- a retry from `memory_purged` does not purge memory again.
- local session state deletion then runs:
  - messages,
  - response variants,
  - failed turns,
  - session summary,
  - swipe metadata.
- then persist `local_committed`.
- a retry from `local_committed` only records the operation applied.
- database optimization remains after completion.
- no new reset phases are introduced.

## Failure semantics

### Provider failure before local commit

The exception propagates through existing worker handling.

No `local_committed` state is written, so a later durable retry may regenerate.

### Failure writing local state

The exception propagates. The short serialized write boundary remains authoritative.

No external delivery should be treated as complete.

### Telegram cleanup failure

Deleting old Telegram messages is best-effort.

Failures are logged with the existing recovery cleanup messages and do not prevent recovery or completion after the new response is successfully delivered.

### Telegram delivery failure after `local_committed`

The exception propagates.

The operation must remain `local_committed`; it must not be marked applied and its payload must remain available.

A later durable retry redelivers committed local state without repeating provider generation or local mutation.

### Missing recovery rows

Command-specific recovery preserves the existing explicit failures:

- `regen recovery state is incomplete`
- `continue recovery state is incomplete`
- `edit recovery state is incomplete`

These failures must not silently mark the operation applied.

### Malformed operation payload

Treat as empty payload. Recovery continues with the persisted local message state.

### Native edit worker behavior

`process_edit_job` is unchanged.

Its current `native_edit_committed_after_failure` check continues to use `operation_phase(...) == "local_committed"` to suppress rollback fallback after local edit state has committed.

## Memory and Persona behavior

The effective dependency injection remains unchanged.

- regen and continue resolve the supplied/compatibility MemoryService.
- edited-turn regeneration resolves MemoryService.
- prompt construction continues to accept PersonaService where currently effective.
- recovery redelivery does not rerun provider generation.
- normal successful paths retain memory at the same point relative to local commit and Telegram delivery as the effective implementation.

No memory or Persona algorithm is redesigned.

## Telegram message-ID cleanup

The helper preserves both legacy and multi-message storage formats.

When decoding message rows:

- include non-empty `telegram_message_id`.
- parse `telegram_message_ids` as JSON.
- malformed/non-list JSON contributes no IDs.
- include non-empty list entries.
- preserve first-seen order while de-duplicating.

Deleting stored IDs remains best-effort per ID and continues after individual deletion failures.

## Runtime loader cutover

Before Phase 6G, `recovery.py` may override eight public callables.

After Phase 6G its allowed public callable overrides are exactly:

```text
sync_status_text
send_sync_menu
handle_sync_callback
```

The following are no longer allowed or reported as overrides from `recovery.py`:

```text
begin_operation
regenerate_last
continue_last
regenerate_edited_turn
process_message
```

`operation_recovery.py` is not a runtime stage.

## Source ownership invariants

After cutover:

- exactly one canonical runtime definition of `begin_operation`: `database.py`.
- exactly one canonical runtime definition of `regenerate_last`: `generation.py`.
- exactly one canonical runtime definition of `continue_last`: `generation.py`.
- exactly one canonical runtime definition of `regenerate_edited_turn`: `commands.py`.
- exactly one canonical runtime definition of `process_message`: `message_commands.py`.
- `recovery.py` contains none of those definitions.
- `_ORIGINAL_PROCESS_MESSAGE` does not exist.
- `_OPERATION_CONTEXT` does not exist.

No compatibility aliases for the retired definitions are retained.

## TDD strategy

Implementation must follow RED -> GREEN task slices. Characterization tests are written first for behavior currently supplied by `recovery.py`, followed by ownership/source-boundary tests that fail until the cutover.

### Database primitive characterization

Cover:

- `begin_operation(None)` success behavior.
- first insert creates `in_progress`.
- existing non-applied operation remains runnable.
- applied operation is rejected.
- write is routed through the short serialized write primitive.
- no broad writer transaction is held across external I/O.

### Recovery payload characterization

Cover:

- set/get round-trip.
- compact JSON persistence.
- missing payload -> `{}`.
- malformed JSON -> `{}`.
- non-object JSON -> `{}`.
- finishing records applied and deletes payload atomically within the short serialized write helper.

### Regen recovery characterization

Cover:

- `local_committed` never calls the provider.
- persisted assistant response is reused.
- selected variant number is preserved.
- old Telegram IDs are cleaned.
- current assistant delivery is cleaned.
- cleanup failures are tolerated.
- delivery failure leaves operation `local_committed`.
- successful redelivery marks applied and removes payload.
- incomplete persisted state raises the existing RuntimeError.

### Continue recovery characterization

Cover:

- `local_committed` never calls the provider.
- persisted combined response is reused.
- selected response variant is updated by user row identity on normal path.
- recorded/stored old Telegram delivery is cleaned.
- delivery failure remains retryable.
- successful redelivery finishes operation.
- incomplete persisted state raises the existing RuntimeError.

### Edit recovery characterization

Cover:

- summary invalidation, user edit, branch deletion, assistant insert, response variant selection, and `local_committed` share the intended local durability boundary.
- `local_committed` recovery never calls the provider.
- old deliveries are cleaned.
- delivery failure remains retryable.
- successful redelivery finishes operation.
- incomplete persisted state raises the existing RuntimeError.

### `process_message` routing characterization

Cover:

- special local-committed regen routes before generic recovery.
- special local-committed continue routes before generic recovery.
- special local-committed edit routes before generic recovery.
- operation-command normalization variants currently supported by the effective wrapper.
- non-special local-committed input preserves generic recovery.
- normal non-recovery processing remains unchanged.

### Reset characterization

Cover:

- first reset purge then local deletion.
- retry at `memory_purged` skips a second purge.
- retry at `local_committed` skips deletion and records applied.
- applied operation is a no-op.
- active-session-only deletion semantics remain unchanged.

### Architecture/source guards

Cover:

- `operation_recovery.py` exists and is ordinarily importable.
- it is absent from `DEFAULT_RUNTIME_STAGES`.
- no `_OPERATION_CONTEXT`.
- no `_ORIGINAL_PROCESS_MESSAGE`.
- no retired durable definitions in `recovery.py`.
- canonical public owner files contain the expected definitions.
- `runtime_loader.py` allowlists only the three Sync UI names for `recovery.py`.
- runtime load report contains no durable-operation override from `recovery.py`.
- the three Sync UI overrides remain present and unchanged for Phase 6H.

## Cutover sequence

The implementation plan should use small TDD slices, but the architectural cutover is logically:

1. Characterize final durable recovery behavior.
2. Add ordinary `operation_recovery.py` mechanics behind tests.
3. Harden canonical `database.py::begin_operation`.
4. Move effective regen/continue behavior into `generation.py`.
5. Move effective edited-turn behavior into `commands.py`.
6. Move wrapper recovery ordering into canonical `message_commands.py::process_message`.
7. Verify reset behavior remains canonical and unchanged.
8. Remove durable-operation code from `recovery.py`.
9. Shrink the runtime override allowlist to the three Sync UI functions.
10. Remove `_ORIGINAL_PROCESS_MESSAGE` and `_OPERATION_CONTEXT`.
11. Run exact-head full verification and source-boundary checks.
12. Leave Sync UI retirement for Phase 6H.

The implementation plan may split these into finer RED/GREEN tasks, but must preserve this dependency order so no intermediate green state silently loses effective behavior.

## Verification requirements

Before Phase 6G is considered ready:

- compile succeeds.
- full `unittest` suite succeeds.
- full `pytest` suite succeeds.
- dependency checks used by the repository CI succeed.
- exact branch head CI succeeds.
- branch is not behind the approved upstream baseline due to relevant code drift, or any drift is reviewed and documented.
- no unexpected runtime public callable override is reported.
- no durable-operation public override remains in `recovery.py`.
- whole-branch review finds no Critical or Important issue.
- PR remains unmerged until separate user approval.

## Phase 6H boundary

Phase 6G deliberately stops with:

```text
recovery.py
├── sync_status_text
├── send_sync_menu
└── handle_sync_callback
```

Phase 6H may then retire these remaining Sync UI late overrides and, if appropriate, remove the `recovery_overrides` runtime stage entirely.

Phase 6G must not pre-empt that work.

## Acceptance criteria

Phase 6G is successful when all of the following are true:

1. Durable recovery behavior is equivalent to the current effective `recovery.py` behavior.
2. No provider regeneration or duplicate local mutation occurs after `local_committed`.
3. failed external delivery remains retryable.
4. reset preserves `memory_purged` idempotency.
5. JobService behavior is unchanged.
6. the five durable public callables are canonically owned outside `recovery.py`.
7. `recovery.py` exposes only the three Sync UI overrides.
8. `operation_recovery.py` is an ordinary explicit helper with no runtime-stage role.
9. `_ORIGINAL_PROCESS_MESSAGE` and `_OPERATION_CONTEXT` are gone.
10. no unrelated Phase 6H or architectural work is included.

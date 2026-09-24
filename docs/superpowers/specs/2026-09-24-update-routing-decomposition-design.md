# Telegram Update Routing Decomposition Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `61486fc43e5677d5e4c54595359d37a1602eafef`

## Purpose

Decompose the remaining monolithic Telegram ingress function after import-cycle and
service-boundary retirement.

Current state:

- import graph is fully acyclic;
- `bridge/update_routing.py` is ~450 lines;
- `route_update()` owns callback routing, edited-message routing, voice/image/
  document intake, Help fast paths, command/generation classification, durable job
  enqueue/submit, and processed-update completion;
- route completion and application job routing are therefore coupled in one large
  control-flow function.

The roadmap explicitly deferred `route_update` decomposition until higher-value
service ownership and dependency cleanup were complete. Those preconditions are now
satisfied.

## Target ownership

Split Telegram update routing into three focused adapter modules:

```text
update_routing.py
    |
    +-- update_callback_routing.py
    |
    +-- update_message_routing.py
```

### update_routing.py — coordinator / transaction boundary

Owns only:

- `complete_update(db, update_id, offset)`;
- `route_update(services, db, fields, update, offset, permitted)`.

Responsibilities:

1. compute `update_id`, prior safe offset, and next offset;
2. detect already-processed updates;
3. select the update class:
   - callback query;
   - edited message;
   - ordinary message;
4. delegate to the focused routing helper;
5. mark the update complete only after successful delegation;
6. return the next offset.

It must not:

- enqueue or submit jobs;
- send Telegram messages/callback answers;
- inspect media/document/text payload details beyond selecting the update class;
- own command classification;
- own Help fast-path behavior.

### update_callback_routing.py — callback ingress

Owns:

```python
def route_callback_update(
    services: BridgeServices,
    db: sqlite3.Connection,
    callback: dict,
    update_id: int,
    permitted: frozenset[str],
) -> None:
    ...
```

Preserves current behavior:

- sender/chat/topic extraction;
- Help callback fast path;
- Help RequestContext construction;
- non-Help callback durable job enqueue/submit;
- queued callback marker;
- callback answer `Queued`;
- unauthorized/invalid callback is silently ignored by the helper.

The helper never calls `complete_update`.

### update_message_routing.py — edited/ordinary message ingress

Owns:

```python
def route_edited_message_update(
    services: BridgeServices,
    db: sqlite3.Connection,
    edited_message: dict,
    update_id: int,
    permitted: frozenset[str],
) -> None:
    ...
```

and:

```python
def route_message_update(
    services: BridgeServices,
    db: sqlite3.Connection,
    fields: dict,
    message: dict,
    update_id: int,
    permitted: frozenset[str],
) -> bool:
    ...
```

`route_message_update` returns whether the coordinator should mark the update
complete.

The only current non-completing case is preserved exactly:

- ordinary message has no resolvable chat id -> return `False`.

All other ordinary-message cases return `True`, including:

- unauthorized sender;
- voice;
- photo;
- image document;
- non-image document;
- no text;
- Help text fast path;
- manual group-turn rejection;
- long-running command;
- ordinary slash command;
- generation message.

The module also owns:

- `LONG_RUNNING_COMMANDS`;
- `is_long_running_command(text)`;
- durable job enqueue/submit for edited/message/media/text jobs;
- user-visible queue/saved/rejection feedback.

The helper never calls `complete_update`.

## Transaction and idempotency semantics

The coordinator remains the only owner of `processed_updates` and Telegram offset
persistence.

### Already processed update

Current behavior is preserved:

- call `complete_update` with the advanced offset;
- return immediately;
- do not call a routing helper.

### Successful callback / edited update

- focused helper returns normally;
- coordinator calls `complete_update` once;
- return next offset.

### Successful ordinary message

- if helper returns `True`, coordinator calls `complete_update` once;
- if helper returns `False`, coordinator does not mark the update processed;
- return next offset.

### Routing exception

Any exception from a focused helper propagates.

The coordinator must not call `complete_update` after an exception. This preserves
retry behavior and prevents silently advancing durable intake state after failed
routing.

## Job-service ownership

Focused helpers continue to use the injected `services.jobs` application service:

- `services.jobs.enqueue(...)`;
- `services.jobs.submit(...)`;
- `JobSubmission` for worker dispatch.

No direct repository/job helper is introduced.

The existing source-boundary test moves from requiring job calls inside
`route_update` to requiring:

- no direct enqueue/submit in `route_update`;
- direct JobService use only in the focused routing modules;
- no `enqueue_job` or `submit_durable_chat_job` compatibility paths.

## Dependency direction

The new modules are Telegram ingress adapters. They may depend on:

- composition service types;
- JobService submission record;
- Telegram/domain application helpers already used by the current function;
- worker orchestration entry points.

They must not import `update_routing`; dependency direction is one-way:

```text
update_routing -> focused routing modules
```

The import graph must remain acyclic.

## Behavior invariants

Preserve exact current behavior for:

- topic-scoped chat ids;
- authorized/unauthorized handling;
- Help callback/text fast paths;
- callback queued marker and acknowledgement;
- edited-message truncation to 12,000 chars;
- voice/image/document job kinds, payloads, workers and user feedback;
- image-document detection;
- active-session resolution semantics;
- Help RequestContext actor/session identity;
- manual group-turn gating;
- command/generation classification;
- long-running command classification;
- queue-vs-restart feedback text;
- durable update completion and offset persistence;
- exception retry semantics.

## Non-goals

- no JobService redesign;
- no Telegram transport extraction;
- no worker behavior changes;
- no update payload schema redesign;
- no transaction model changes beyond clarifying ownership already present;
- no Ruff/type rollout in this PR;
- no streaming continuation fix.

## Acceptance

1. `route_update()` becomes a small coordinator with no job enqueue/submit or
   Telegram delivery.
2. callback routing has a focused owner.
3. edited/ordinary message routing has a focused owner.
4. `is_long_running_command` moves to message routing with no compatibility alias.
5. focused helpers never call `complete_update`.
6. duplicate, no-chat-id, successful, and exception completion semantics are
   regression-covered.
7. existing Help fast-path and JobService behavior remains green.
8. import graph remains fully acyclic.
9. full exact-head local and GitHub CI pass.
10. merge and verify actual main before beginning Ruff/static enforcement.

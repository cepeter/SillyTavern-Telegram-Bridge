# Phase 6H — Sync UI Recovery Retirement Design

Date: 2026-09-21  
Baseline upstream `main`: `d5be29779fe0da0f681c9da22f1d675cb196e38a`  
Feature branch: `refactor/phase-6h-sync-ui-recovery-retirement`

## Status

Approved in-chat architecture and behavior design. This document defines the Phase 6H cutover only. Implementation planning and code changes follow after explicit review of this written spec.

## Context

Phase 6G moved all durable-operation recovery behavior out of `bridge/recovery.py`. After that merge, the file contains only three Sync UI compatibility callables:

- `sync_status_text`
- `send_sync_menu`
- `handle_sync_callback`

The runtime still carries a dedicated `recovery_overrides` stage only to load that file. These functions are not durable-recovery behavior and no longer justify a compatibility stage.

Phase 6H finishes the recovery-layer retirement by moving the remaining Sync UI behavior into canonical panel modules, deleting `bridge/recovery.py`, and removing the `recovery_overrides` runtime stage entirely.

## Goal

Remove the final recovery compatibility layer without changing Sync UI behavior.

After Phase 6H:

- `status_panels.py` canonically owns Sync status rendering and Sync menu presentation.
- `panel_callback_routes.py` canonically owns Sync callback handling.
- `sync_api.py` continues to own SyncService construction, fallback resolution, realtime polling, and backend operations.
- `bridge/recovery.py` no longer exists.
- `runtime_loader.py` has no `recovery_overrides` stage.
- `RUNTIME_LOAD_REPORT` has no recovery-stage or recovery-file entry.

## Non-goals

Phase 6H does not redesign:

- `SyncService`.
- `sync_core.py` or `sync_api.py` backend behavior.
- realtime polling or worker lifecycle.
- SillyTavern loopback HTTP transport.
- conflict detection or Sync integrity logic.
- panel-session ownership, expiry, or authorization.
- `/sync` command semantics.
- Sync callback data values or visible labels.
- JobService or callback job durability.
- schema or migrations.
- service composition outside the existing optional `sync_service` injection path.

No new adapter, compatibility shim, service locator, runtime stage, or import cycle is introduced.

## Architectural choice

Use distributed canonical ownership.

### Selected approach

- `status_panels.py` owns:
  - `sync_status_text`
  - `send_sync_menu`
- `panel_callback_routes.py` owns:
  - `handle_sync_callback`
- `sync_api.py` remains the backend/service-resolution owner.

This matches the existing domain boundaries:

- status rendering and panel presentation belong with other panel UI functions;
- callback mutation/routing belongs with callback route handlers;
- Sync backend behavior remains separate.

### Rejected alternative: new `sync_ui.py`

A dedicated Sync UI module would make naming explicit, but it adds another abstraction and module solely to remove a compatibility module. It would also require additional dependency/import plumbing with no behavioral benefit.

### Rejected alternative: put all three functions in `status_panels.py`

This would be mechanically simple but would mix callback mutation/routing into a read-oriented panel module and weaken established callback ownership boundaries.

## Canonical component boundaries

### `bridge/status_panels.py`

Canonical public owner of:

```python
def sync_status_text(
    db,
    chat_id,
    session,
    *,
    sync_service=None,
) -> str

def send_sync_menu(
    token,
    chat_id,
    db,
    session,
    message_id=None,
    *,
    sync_service=None,
) -> None
```

Responsibilities:

- resolve the explicit/fallback SyncService through `resolve_sync_service`;
- render current Sync status text;
- build the existing Sync inline keyboard;
- deliver/update the panel through `send_panel_message`.

It must not own Sync backend operations, conflict logic, polling, or worker lifecycle.

### `bridge/panel_callback_routes.py`

Canonical public owner of:

```python
def handle_sync_callback(
    db,
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    message,
    session,
    session_id,
    operation_id,
    *,
    sync_service=None,
) -> bool
```

Responsibilities:

- return `False` for non-`sync:` callbacks;
- handle Sync panel close/status/realtime/manual-sync actions;
- call the supplied/fallback SyncService;
- answer Telegram callbacks;
- refresh the Sync menu after applicable mutations.

It must not duplicate panel ownership checks, session binding validation, or Sync backend implementation.

### `bridge/sync_api.py`

Remains canonical owner of:

- `compatibility_sync_service()`
- `resolve_sync_service()`
- `phase3_sync_now()`
- `phase3_toggle_realtime()`
- `phase3_sync_poll()`
- realtime worker lifecycle and backend state.

Phase 6H does not alter these APIs or their semantics.

### `bridge/recovery.py`

Deleted entirely.

There is no forwarding shim, deprecated alias module, empty placeholder, or compatibility wrapper after Phase 6H.

### `bridge/runtime_loader.py`

Delete the complete `recovery_overrides` stage.

The canonical core stage is followed directly by `sync_extensions`.

No replacement stage is added.

## Dependency and load-order semantics

The shared runtime executes core modules before `sync_api.py`, but global collaborator lookup occurs when functions are called rather than when they are defined.

Therefore the canonical functions may reference:

- `resolve_sync_service`
- `send_panel_message`
- `close_panel_message`
- `send_sync_menu`

without constructing a new adapter solely for load-order reasons.

Conceptually:

```text
core stage
├── status_panels.py
│   ├── sync_status_text
│   └── send_sync_menu
├── panel_callback_routes.py
│   └── handle_sync_callback
├── callbacks.py
└── main.py
        |
        v
sync_extensions
├── sync_core.py
└── sync_api.py
    └── resolve_sync_service + SyncService backend
```

The design relies only on the existing shared-runtime late global lookup model. It introduces no new hidden load-order state.

## Existing call flow

### `/sync`

```text
command_routes.py
    |
    └── /sync
         |
         └── send_sync_menu(...)
              |
              ├── sync_status_text(...)
              │    └── SyncService.status(...)
              |
              └── send_panel_message(...)
```

The command route already passes the injected SyncService from `services.sync` when available. Phase 6H preserves that behavior.

### Sync callbacks

```text
callbacks.py
    |
    └── handle_primary_panel_callback(...)
          |
          └── handle_sync_callback(...)
               |
               ├── toggle_realtime(...)
               ├── sync_now(...)
               ├── send_sync_menu(...)
               └── close_panel_message(...)
```

`callbacks.py` continues to own:

- panel-session binding lookup;
- owner authorization;
- expired-panel handling;
- service extraction from the runtime service bundle.

Phase 6H does not duplicate those responsibilities.

## Sync status behavior

`sync_status_text` preserves the current output exactly.

Required behavior:

- resolve `sync_service` using `resolve_sync_service(sync_service)`;
- call `status(db, chat_id, session["session_id"])`;
- display `last_synced_at` as local time using the existing timestamp format;
- display `never` when no prior sync exists;
- display realtime as `on` or `off`;
- display API state as `configured` or `not configured`;
- preserve the existing title, explanatory line, labels, spacing, and line breaks.

No fallback status is invented when the service raises; errors propagate as they do today.

## Sync menu behavior

`send_sync_menu` preserves the current panel text and keyboard exactly.

The inline keyboard remains:

```text
🚀 Realtime API: toggle  -> sync:realtime
🔁 Sync now              -> sync:now
🔄 Refresh status        -> sync:status
❌ Close                 -> sync:close
```

Required behavior:

- use the same injected/fallback SyncService instance for status rendering;
- pass `message_id` unchanged to `send_panel_message`;
- keep existing panel delivery/edit behavior delegated to the panel helper;
- do not add retries or Sync-specific Telegram error handling.

## Sync callback behavior

`handle_sync_callback` preserves all existing behavior.

### Non-Sync callback

If `data` does not start with `sync:`, return `False`.

### `sync:close`

- answer callback with `Closed`;
- close the panel through `close_panel_message`;
- return `True`.

### `sync:menu` and `sync:status`

- answer callback with `Sync status`;
- refresh the Sync menu for the current panel message;
- return `True`.

### `sync:realtime`

- call `sync_service.toggle_realtime(db, chat_id, session_id)`;
- answer with the first 200 characters of the result;
- refresh the Sync menu;
- return `True`.

### `sync:now`

- call `sync_service.sync_now(db, chat_id, session_id)`;
- answer with the first 200 characters of the result;
- refresh the Sync menu;
- return `True`.

### Unknown Sync action

- answer callback with `Unknown sync action`;
- return `True`.

The `operation_id` parameter remains in the public signature for callback-route compatibility even though this handler does not use it.

## Service injection semantics

All three canonical functions preserve the existing optional injection API:

```python
sync_service=None
```

Rules:

1. explicit service supplied -> use it;
2. otherwise resolve through `resolve_sync_service(None)`;
3. fallback service construction remains in `sync_api.py`.

Tests and production composition therefore retain the same injection surface.

## Runtime-loader cutover

Before Phase 6H:

```text
core
recovery_overrides
sync_extensions
native_adapter_overrides
identity_extensions
safety_overrides
```

After Phase 6H:

```text
core
sync_extensions
native_adapter_overrides
identity_extensions
safety_overrides
```

The runtime loader must not contain:

- a stage named `recovery_overrides`;
- `recovery.py` in any stage;
- an allowlist for `recovery.py`.

The runtime report must not contain any entry whose module is `recovery.py` or whose stage is `recovery_overrides`.

## TDD strategy

Implementation follows RED -> GREEN slices.

### Characterize Sync status rendering

Cover:

- realtime enabled/disabled rendering;
- API configured/not-configured rendering;
- never-synced rendering;
- last-sync direction/timestamp rendering;
- explicit `sync_service` injection honored.

### Characterize Sync menu rendering

Cover:

- exact title/body reuse;
- exact four callback data values;
- exact visible button labels;
- `message_id` forwarding;
- injected service reuse.

### Characterize Sync callback behavior

Cover:

- non-Sync callback -> `False`;
- close;
- menu/status refresh;
- realtime toggle;
- manual sync;
- unknown action;
- callback answer text;
- 200-character result truncation;
- injected service use.

### Source ownership guards

Require:

- `status_panels.py` defines `sync_status_text`;
- `status_panels.py` defines `send_sync_menu`;
- `panel_callback_routes.py` defines `handle_sync_callback`;
- `recovery.py` is absent after final cutover.

### Runtime composition guards

Require:

- no `recovery_overrides` stage in `DEFAULT_RUNTIME_STAGES`;
- no `recovery.py` module in any runtime stage;
- no recovery-stage entry in `RUNTIME_LOAD_REPORT`;
- no recovery-file entry in `RUNTIME_LOAD_REPORT`.

## Cutover sequence

1. Characterize current Sync UI behavior before moving code.
2. Add failing source-ownership tests for the three canonical destinations.
3. Move `sync_status_text` and `send_sync_menu` byte-equivalently into `status_panels.py`.
4. Move `handle_sync_callback` byte-equivalently into `panel_callback_routes.py`.
5. Run focused behavioral tests while `recovery.py` still exists but no longer supplies those public functions.
6. Delete `bridge/recovery.py`.
7. Remove the complete `recovery_overrides` stage from `runtime_loader.py`.
8. Replace legacy loader assertions with absence assertions.
9. Run full exact-head verification.
10. Review the whole branch against this design and the implementation plan.
11. Open the Phase 6H PR for user review.
12. Do not merge automatically.

## Verification requirements

Before Phase 6H is ready for review:

- compile succeeds;
- full `unittest` suite succeeds;
- full `pytest` suite succeeds;
- `pip check` succeeds;
- dependency audit succeeds;
- exact branch-head CI succeeds;
- branch is not behind current upstream `main`, or any drift is reviewed;
- no recovery runtime stage/file remains;
- whole-branch review finds no Critical or Important issue;
- PR remains unmerged until separate user approval.

## End state

```text
status_panels.py
├── existing status/prompt panel functions
├── sync_status_text
└── send_sync_menu

panel_callback_routes.py
├── existing callback route handlers
└── handle_sync_callback

sync_api.py
└── SyncService resolution/backend

recovery.py
└── deleted

runtime_loader.py
└── no recovery_overrides stage
```

## Acceptance criteria

Phase 6H is complete when all of the following are true:

1. `/sync` behavior and visible output are unchanged.
2. Sync callback behavior, labels, callback data, and answer text are unchanged.
3. explicit/fallback SyncService injection behavior is unchanged.
4. `sync_api.py` backend and worker behavior are unchanged.
5. panel ownership/expiry behavior is unchanged.
6. `sync_status_text` and `send_sync_menu` are canonically owned by `status_panels.py`.
7. `handle_sync_callback` is canonically owned by `panel_callback_routes.py`.
8. `bridge/recovery.py` no longer exists.
9. `recovery_overrides` no longer exists.
10. no replacement compatibility stage or shim is introduced.

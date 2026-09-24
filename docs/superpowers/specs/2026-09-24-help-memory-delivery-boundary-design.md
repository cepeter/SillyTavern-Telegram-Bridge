# Help and Memory Delivery Boundary Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `2abaceb6884ab3e9837cca52e867ce6eff9c424c`

## Purpose

Retire one reciprocal dependency edge set from each of the two remaining 7-module
strongly connected components:

- the `help <-> help_details` presentation cycle;
- the `memory -> telegram` delivery dependency.

Fresh merged-main graph:

- 83 bridge modules
- 445 internal edges
- largest SCC: 6
- cyclic modules: 12
- reciprocal pairs: 8
- concrete Telegram importers: 28
- SCC sizes: 6 + 6

The graph model predicts that full Help decoupling plus removal of
`memory -> telegram` yields SCC sizes 5 + 5 and cyclic modules 10.

## Cut A — Help ownership and DeliveryPort

### Canonical Help owner

`help_details.py` becomes the canonical owner of:

- `HELP_CATEGORIES`;
- editable command detail loading;
- help text rendering;
- help markup rendering;
- help command normalization/lookup;
- help menu delivery;
- help callback handling.

`help.py` retains unrelated system/settings/voice/memory/data-bank menu helpers and
document processing. It must not import `help_details.py` after this change.

`help_details.py` must not import `help.py`.

### Help menu delivery

Add required `DeliveryPort` to the Help UI entry points:

```python
send_help_menu(
    token,
    chat_id,
    category=None,
    message_id=None,
    command_index=None,
    page=0,
    *,
    delivery_port: DeliveryPort,
    request_context,
) -> None

send_help_command(
    token,
    chat_id,
    text,
    message_id=None,
    *,
    delivery_port: DeliveryPort,
    request_context,
) -> bool

handle_help_callback(
    ...,
    *,
    delivery_port: DeliveryPort,
    request_context,
) -> bool
```

`send_help_menu` constructs the same `sendMessage` / `editMessageText`
payload currently produced by `cards.send_panel_message`, then calls
`delivery_port.send_panel_request`. This preserves panel binding because the
DeliveryPort is composed from the same canonical Telegram panel request backend.

### Command router

`command_routes._handle_basic()` already receives `services` and
`request_context`.

Required behavior:

- `/help` calls `send_help_menu(..., delivery_port=services.delivery, ...)`;
- `/help <command>` calls `send_help_command(token, chat_id, stripped, ...)`
  with the correct positional order and explicit DeliveryPort.

The current malformed call order for `/help <command>` is corrected as part of
making this boundary explicit.

### Panel callback router

`panel_callback_routes.handle_primary_panel_callback()` already receives
`delivery_port`. It forwards that exact object to `handle_help_callback`.

### Fast-path update routing

`update_routing.route_update()` has the complete `BridgeServices` graph.

For Help callback fast-paths:

1. resolve/ensure the callback session using the existing canonical
   `ensure_session`;
2. construct `RequestContext(db, session_id, sender)`;
3. call `handle_help_callback(..., delivery_port=services.delivery,
   request_context=...)`.

For text Help fast-paths, forward the already-created message `RequestContext`
and `services.delivery` to `send_help_command`.

This also removes the current missing-request-context defect in the callback fast path.

### Help catalog compatibility

No production compatibility alias remains in `help.py`.
Tests and consumers use `help_details.HELP_CATEGORIES` as the canonical owner.

## Cut B — Memory text delivery

`memory.py` imports Telegram solely for `send_text`.

Change:

```python
handle_memory_command(
    db,
    token,
    chat_id,
    session,
    fields,
    command_text,
    *,
    send_text_fn: Callable[[str, str, str], object],
) -> None
```

Every current user-visible memory response uses `send_text_fn`.

Callers:

- `command_routes._handle_memory_media()` passes
  `services.delivery.send_text`;
- `input_flows._handle_text_action_input()` passes its existing canonical
  `send_text` collaborator.

This keeps the pending-input contract narrow and does not widen InputFlowService.

`memory.py` no longer imports `bridge.telegram`.

## Dependency direction

After this wave:

- `help -> help_details` absent;
- `help_details -> help` absent;
- `memory -> telegram` absent;
- HelpDetails depends on pure DeliveryPort;
- Memory receives a text-delivery callable explicitly.

Expected graph:

- largest SCC: 6 -> 5
- cyclic modules: 12 -> 10
- reciprocal pairs: 8 -> 7 (measured; memory -> telegram is not reciprocal)
- Telegram importers: 28 -> 27
- SCC sizes: 5 + 5
- no newly cyclic module

## Behavior invariants

Help:

- exact category membership and command summaries;
- exact help text and inline keyboard callback data;
- same pagination;
- same command-detail JSON fallback;
- same close/back behavior;
- same panel binding/session ownership;
- `/help <command>` renders the intended detail panel;
- callback fast path does not enqueue a worker.

Memory:

- same on/off/status/scope/search behavior;
- same Hindsight session scoping;
- same user-visible text;
- no persistence semantics change.

## Non-goals

- no rewrite of memory backend/Hindsight logic;
- no Help document-processing changes;
- no removal of `input_flows -> telegram`;
- no route_update decomposition beyond the required Help collaborator forwarding;
- no transaction changes;
- no streaming continuation fix.

## Acceptance

1. `help.py` and `help_details.py` do not import each other.
2. `HELP_CATEGORIES` and `send_help_menu` have one canonical owner:
   `help_details.py`.
3. Help UI paths require and forward the exact DeliveryPort.
4. Help fast-path command/callback routing is regression-covered.
5. `memory.py` imports no `bridge.telegram`.
6. all memory command text goes through required `send_text_fn`.
7. focused Help/Memory/routing tests are green.
8. graph largest SCC <=5 and cyclic modules <=10 with no new cyclic module.
9. full exact-head local and GitHub CI pass.
10. merge and re-scan actual main before the next cut.

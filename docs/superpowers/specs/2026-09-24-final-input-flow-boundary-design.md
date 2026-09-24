# Final InputFlow Boundary Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `33bfdd9a5007ee16db185579281cef2a4596b1d0`

## Purpose

Eliminate the remaining 4-module UI strongly connected component:

- `bridge.help`
- `bridge.input_flows`
- `bridge.session_naming`
- `bridge.status_panels`

Fresh merged-main graph:

- 85 bridge modules
- 446 internal edges
- largest SCC: 4
- cyclic modules: 7
- reciprocal pairs: 6
- SCC sizes: 4 + 3

The UI SCC contains these internal edges:

- `help -> input_flows`
- `input_flows -> help`
- `input_flows -> session_naming`
- `input_flows -> status_panels`
- `session_naming -> input_flows`
- `status_panels -> help`
- `status_panels -> input_flows`

The graph only requires retiring three outward dependencies to collapse the entire
component:

- `help -> input_flows`
- `input_flows -> session_naming`
- `input_flows -> status_panels`

Expected result: the UI SCC disappears entirely; only the final
`cards/persona_sync/telegram` 3-cycle remains.

## Cut A — Help uses InputFlowService for text-action startup

`help.py` imports `input_flows.start_text_action_input` only for enum-panel
actions such as memory search and Data Bank search.

Extend pure `InputFlowService` with a required backend:

```python
start_text_action_backend: Callable[..., None]
```

and method:

```python
def start_text_action(self, *args, **kwargs) -> None:
    self.start_text_action_backend(*args, **kwargs)
```

`help.handle_enum_callback(..., *, input_flow_service: InputFlowService,
request_context)` uses the service instead of importing InputFlows.

`callback_dispatch` already owns the full `BridgeServices` graph and forwards
`services.input_flow`.

Startup/test composition binds the canonical
`input_flows.start_text_action_input`.

No optional/default backend is allowed in the service dataclass.

## Cut B — InputFlowService injects session-name handling

`input_flows.handle_pending_input` currently imports
`session_naming.handle_session_name_input`.

Extend `InputFlowService` with a required backend:

```python
handle_session_name_backend: Callable[..., bool]
```

`InputFlowService.handle_pending()` always injects that backend into the canonical
pending handler:

```python
return bool(
    self.handle_pending_backend(
        *args,
        handle_session_name=self.handle_session_name_backend,
        **kwargs,
    )
)
```

`input_flows.handle_pending_input(..., *, handle_session_name: Callable[..., bool],
...)` calls the supplied collaborator when a session-name pending state exists.

`input_flows.py` no longer imports `bridge.session_naming`.

Direct tests of `handle_pending_input` must either:
- use `InputFlowService.handle_pending`, or
- explicitly pass the canonical/fake session-name handler.

No fallback import is retained.

## Cut C — Director Goal pending input uses pure panel data

`input_flows.py` imports `status_panels.send_director_goal_menu` only after the
`director_goal` text action saves the new goal.

The Director Goal presentation already has a pure canonical owner:
`director_goal_panel.director_goal_panel(goal)`.

After saving the goal:

1. render text/markup with `director_goal_panel(value)`;
2. send the panel through the existing canonical `send_panel_request` collaborator
   already owned by InputFlows;
3. use `sendMessage` with the same chat_id, text, markup and request_context as the
   previous `send_director_goal_menu` call.

`input_flows.py` no longer imports `bridge.status_panels`.

## InputFlowService final contract

The pure service owns four required backend collaborators:

```python
@dataclass(frozen=True)
class InputFlowService:
    handle_pending_backend: Callable[..., bool]
    start_session_name_backend: Callable[..., None]
    start_text_action_backend: Callable[..., None]
    handle_session_name_backend: Callable[..., bool]
```

Existing behavior remains:

- `start_session_name` delegates to `start_session_name_backend`;
- `start_text_action` delegates to `start_text_action_backend`;
- `handle_pending` delegates to `handle_pending_backend` while injecting
  `handle_session_name_backend`.

Startup composition:

- pending backend -> `input_flows.handle_pending_input`;
- session-name starter -> `session_naming.start_session_name_input`;
- text-action starter -> `input_flows.start_text_action_input`;
- session-name handler -> `session_naming.handle_session_name_input`.

The test factory mirrors these canonical owners unless an explicit fake backend is
provided.

## Dependency direction after the wave

Removed:

- `help -> input_flows`
- `input_flows -> session_naming`
- `input_flows -> status_panels`

Allowed remaining one-way dependencies may include:

- `input_flows -> help`
- `session_naming -> input_flows`
- `status_panels -> help`
- `status_panels -> input_flows`

These no longer form a cycle because InputFlows does not depend back on
SessionNaming/StatusPanels and Help does not depend on InputFlows.

Expected graph:

- largest SCC: 4 -> 3
- cyclic modules: 7 -> 3
- only cyclic component:
  `cards/persona_sync/telegram`
- no newly cyclic module

## Behavior invariants

Help enum panels:
- memory-search and Data Bank search prompts/messages remain identical;
- callback panel close/binding behavior unchanged;
- exact RequestContext is preserved.

Pending session naming:
- same state keys/TTL/cancel semantics;
- same GroupService instance;
- same operation_id/request_context forwarding;
- same standard/group creation behavior.

Director Goal pending action:
- same 1,200 character validation;
- same persistence;
- same panel text/markup/callback data;
- same pending-state cancellation after success.

## Non-goals

- no SessionNaming implementation rewrite;
- no Help/status-panel feature redesign;
- no Telegram decomposition;
- no final cards/persona cycle work in this PR;
- no transaction or route_update changes;
- no streaming continuation fix.

## Acceptance

1. InputFlowService stays pure and all four backends are required.
2. `help.py` imports no `bridge.input_flows`.
3. `input_flows.py` imports neither `bridge.session_naming` nor
   `bridge.status_panels`.
4. callback dispatch forwards exact `services.input_flow` to Help enum callbacks.
5. pending session naming uses the service-injected handler.
6. Director Goal pending action preserves exact panel behavior.
7. focused Help/InputFlow/SessionNaming/Director Goal tests are green.
8. graph cyclic modules <=3 with no newly cyclic module.
9. full exact-head local + GitHub CI pass.
10. merge and re-scan actual main before final native/Telegram cycle retirement.

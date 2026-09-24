# UI Runtime Cycle Wave Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `f8342ffbf817225826c119c8d2df49c5b1c20ed9`

## Purpose

Retire one reciprocal dependency edge from each of the two remaining 8-module
strongly connected components:

- `expressions -> telegram`
- `groups -> session_naming`

Fresh merged-main graph:
- 81 bridge modules
- 442 internal edges
- largest SCC: 8
- cyclic modules: 16
- reciprocal pairs: 12
- concrete Telegram importers: 29
- SCC sizes: 8 + 8

Removing these two edges together produces two 7-module SCCs in the graph model,
with cyclic modules 16 -> 14.

## Cut A — Expressions panel delivery

`expressions.py` imports Telegram only for `send_panel_request`.
The existing pure `DeliveryPort` already owns that application boundary.

Required result:

```python
send_expression_menu(
    ...,
    *,
    delivery_port: DeliveryPort,
    request_context,
) -> None
```

The function uses `delivery_port.send_panel_request` with the exact current
method/payload semantics.

`command_routes._handle_generation_panels()` already receives DeliveryPort and
passes it into `send_expression_menu`.

`panel_callback_routes.handle_expression_callback(...)` receives required
DeliveryPort; `handle_primary_panel_callback` forwards its existing
`delivery_port`.

`expressions.py` no longer imports `bridge.telegram`.

### Explicit non-goal

Expression photo upload currently uses a direct fixed Telegram HTTPS request inside
`_send_expression_photo`. That transport hardening is not part of this PR because
it requires a broader media-delivery design. This PR removes the concrete module
dependency that participates in the SCC while preserving photo behavior exactly.

## Cut B — Group session-name startup

`groups.handle_group_panel_callback()` imports
`session_naming.start_session_name_input` solely for `group:new_session`.

Extend the pure existing `InputFlowService` with a second required backend:

```python
@dataclass(frozen=True)
class InputFlowService:
    handle_pending_backend: Callable[..., bool]
    start_session_name_backend: Callable[..., None]

    def handle_pending(...): ...
    def start_session_name(...): ...
```

Both backend callables are required; no optional/default compatibility path exists.

Startup composes:

- `handle_pending_backend=input_flows.handle_pending_input`
- `start_session_name_backend=session_naming.start_session_name_input`

The test factory composes the same canonical owners unless an explicit fake backend
is supplied.

`handle_group_panel_callback(..., *, group_service, input_flow_service,
request_context)` calls:

```python
input_flow_service.start_session_name(
    db,
    token,
    chat_id,
    session,
    kind="group",
    message=message,
    group_service=group_service,
)
```

`callback_dispatch` forwards `services.input_flow`.

`groups.py` no longer imports `bridge.session_naming`.

## Dependency direction

After the wave:

- `expressions -> telegram` is absent;
- `telegram -> expressions` may remain until later Telegram decomposition;
- `groups -> session_naming` is absent;
- `session_naming -> groups` may remain until later session/group decomposition;
- new dependencies target pure ports/services only.

Expected graph:
- largest SCC: 8 -> 7
- cyclic modules: 16 -> 14
- reciprocal pairs: 12 -> 10
- Telegram importers: 29 -> 28
- SCC sizes: 7 + 7
- no newly cyclic module

## Behavior invariants

Expressions:
- same menu text
- same pagination
- same callback data
- same send/edit method
- same request_context forwarding
- expression image upload unchanged

Group new-session:
- same pending-state key
- same group naming kind
- same source message propagation
- same GroupService instance
- same prompt cleanup / pending-input semantics

## Non-goals

- no expression photo transport extraction;
- no session-name implementation rewrite;
- no route_update work;
- no persistence/transaction changes;
- no broader Telegram decomposition;
- no streaming continuation fix.

## Acceptance

1. `expressions.py` imports no `bridge.telegram`.
2. `groups.py` imports no `bridge.session_naming`.
3. InputFlowService remains pure and both backends are required.
4. command/callback expression UI forwards the exact DeliveryPort.
5. group callback forwards the exact InputFlowService and GroupService.
6. focused behavior suites are green.
7. graph largest SCC <=7 and cyclic modules <=14 with no new cyclic module.
8. full exact-head local and GitHub CI pass.
9. merge and re-scan actual main before selecting the next cut.

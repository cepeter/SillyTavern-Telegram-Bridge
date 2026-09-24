# InputFlowService Boundary Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `71651ed37a5ba0cb3ef50229908fde29059e356b`

## Purpose

Retire the direct `message_commands -> input_flows` dependency. Fresh merged-main
graph measurements are 78 modules, 438 internal edges, largest SCC 11, cyclic
modules 19, reciprocal pairs 15, and 30 concrete Telegram importers.

Removing this single edge in the graph model changes the cyclic components from
11 + 8 to 9 + 8 + 2. It is the strongest remaining single-edge cut.

## Design

Create pure `bridge/input_flow_service.py`:

```python
@dataclass(frozen=True)
class InputFlowService:
    handle_pending_backend: Callable[..., bool]

    def handle_pending(self, *args, **kwargs) -> bool:
        return bool(self.handle_pending_backend(*args, **kwargs))
```

`BridgeServices.input_flow` is required. Startup composes it with the canonical
`input_flows.handle_pending_input` implementation.

`message_commands.prepare_message()` calls
`services.input_flow.handle_pending(...)` with the exact existing db/token/chat/
session/text/api-key/fields/operation/services/request-context arguments.

`message_commands.py` must no longer import `bridge.input_flows`.

No compatibility alias, optional fallback, service locator, dynamic import, or
pending-input behavior change is introduced.

## Non-goals

- no rewrite of individual pending-input handlers;
- no removal of `input_flows -> message_commands` in this PR;
- no command-route or route_update decomposition;
- no transaction behavior changes.

## Acceptance

1. InputFlowService imports no bridge modules.
2. BridgeServices requires input_flow.
3. main composes canonical handle_pending_input into InputFlowService.
4. message_commands imports no bridge.input_flows.
5. pending input forwarding is behavior-preserving.
6. graph largest SCC <=9, no newly cyclic module.
7. full local + GitHub CI green, merge, then re-scan actual main.

# Generation DeliveryPort Boundary Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `74bd498036465f7f083e0d3aa17835f506eeefed`
Parent: `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`

## Purpose

Retire the strongest remaining post-ModelRouter application back edge:
`generation -> media`. At the same time remove `generation -> telegram`, because
both imports exist for the same concern: delivering and recovering Telegram-visible
generation results.

Fresh merged-main graph:

- 77 bridge modules;
- 435 internal edges;
- largest SCC: 12;
- cyclic modules: 23;
- reciprocal pairs: 16;
- concrete `bridge.telegram` importers: 32.

Removing only `generation -> media` models as largest SCC 12→11 and cyclic
modules 23→21. Removing `generation -> telegram` alone is graph-neutral, but
combining both changes is one coherent dependency-direction correction and removes
one concrete Telegram importer.

## Goals

1. Add a pure required DeliveryPort.
2. Bind concrete Telegram/media delivery functions only at startup composition.
3. Remove all `bridge.media` and `bridge.telegram` imports from generation.py.
4. Preserve regeneration/continuation recovery semantics exactly.
5. Preserve reply, text, typing, swipe-panel and recovery cleanup behavior.
6. Make BridgeServices.delivery required and propagate it explicitly.
7. Preserve ProviderPort behavior and the known streaming-continuation defect.
8. Re-scan actual merged main before choosing the next Telegram cut.

## Non-goals

- no broad media.py or telegram.py decomposition;
- no change to provider transport;
- no response-language policy change;
- no route_update decomposition;
- no transaction-purity sweep;
- no fix to the known streaming visible-length continuation defect;
- no compatibility re-export or global DeliveryPort singleton.
## DeliveryPort

Create pure `bridge/delivery_port.py`:

```python
@dataclass(frozen=True)
class DeliveryPort:
    request: Callable[..., object]
    send_text: Callable[..., list[int]]
    send_reply: Callable[..., None]
    send_typing: Callable[..., None]
    send_panel_request: Callable[..., dict]
    delete_outgoing_message_row: Callable[..., None]
```

The module imports only stdlib typing/dataclass primitives.

Startup composes it from canonical concrete owners:

- `telegram.telegram_request`
- `telegram.send_text`
- `media.send_reply`
- `media.send_typing`
- `telegram.send_panel_request`
- `media.delete_outgoing_message_row`

`BridgeServices.delivery` is required. Tests use an explicit
`make_test_delivery_port()`.

## Generation ownership

`generation.py` remains the application owner of:

- response-language rendering;
- prompt assembly;
- response variants;
- regenerate/continue workflows;
- swipe-state persistence.

It no longer imports media.py or telegram.py.

Functions causing delivery receive required DeliveryPort:

- `regenerate_last(..., delivery_port=...)`
- `continue_last(..., delivery_port=...)`
- `send_swipe_menu(..., delivery_port=...)`
- `edit_swipe_menu(..., delivery_port=...)`
- internal reply-generation helper receives DeliveryPort for typing/reply.

Plain persistence helpers such as `save_response_variant`,
`last_user_variants`, `keep_swipe_variant`, and `swipe_state_key` do not
receive it.

## Recovery construction

The current module-global `_GENERATION_OPERATION_RECOVERY` binds concrete
Telegram/media functions at import time. Replace it with a local factory:

```python
def _generation_operation_recovery(delivery_port: DeliveryPort) -> OperationRecovery:
    ...
```

The factory binds:

- `telegram_request=delivery_port.request`
- `delete_outgoing_message_row=delivery_port.delete_outgoing_message_row`

and keeps the existing database/repository/logging collaborators.

Each regenerate/continue invocation creates one recovery helper from the injected
DeliveryPort and uses that object consistently for recovery, payload, delivery
cleanup and completion. No service locator or global mutable registration is added.
## Caller propagation

### Command routes

`/regen`, `/continue`, and `/swipe` pass `services.delivery`.

### Message recovery

`message_commands.prepare_message()` passes `services.delivery` when replaying
committed regen/continue operations.

### Swipe callbacks

`handle_swipe_callback()` receives required `delivery_port` for
`edit_swipe_menu`. `handle_primary_panel_callback()` forwards it and
`callback_dispatch.process_callback()` supplies `services.delivery`.

This PR does not migrate the other panel callback Telegram calls because they belong
to later adapter decomposition.

## Behavior invariants

- committed regen/continue recovery must not call ProviderPort generation;
- delivery cleanup order and operation phases remain unchanged;
- send_reply metadata persistence/TTS behavior remains in media.py;
- typing indicator behavior remains in media.py;
- panel request scoping remains in telegram.py;
- send_text chunking/thread behavior remains in telegram.py;
- swipe selection persistence remains unchanged;
- Telegram delivery failures continue to propagate/retain recovery state exactly as
  today.

## Testing

TDD architecture guards first fail on merged main:

- DeliveryPort module missing;
- BridgeServices.delivery missing;
- generation imports media and telegram;
- module-global generation recovery object exists.

Behavior coverage:

- pure DeliveryPort required composition;
- regenerate/continue use injected delivery and preserve no-generation recovery;
- typing/reply use exact injected delivery object;
- swipe menu/edit use injected panel delivery;
- operation recovery delete/request use DeliveryPort collaborators;
- callback dispatch forwards the same services.delivery instance.

Full validation:

- focused recovery/swipe/composition tests;
- compileall and diff-check;
- fresh-process imports;
- full pytest-xdist;
- graph measurement;
- exact-head GitHub test + dependency-audit;
- merge and post-merge re-scan.

## Acceptance criteria

1. generation.py imports neither bridge.media nor bridge.telegram.
2. DeliveryPort is pure and acyclic.
3. BridgeServices.delivery is required.
4. No module-global delivery binding or fallback exists.
5. Recovery and swipe behavior remain green.
6. Largest SCC is no greater than 11 and cyclic modules no greater than 21.
7. Telegram importer count drops by at least one on this lineage.
8. Exact-head local and GitHub CI pass.

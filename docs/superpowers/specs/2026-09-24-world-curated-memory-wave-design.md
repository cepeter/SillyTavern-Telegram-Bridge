# World Storage and Curated Memory Boundary Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `e2d9f9134d349d6aeb53dcaf9d2858c3119c8c6f`

## Purpose

Retire one dependency edge from each remaining 7-module SCC:

- `telegram -> catalog`
- `memory_curator -> status_panels`

Fresh merged-main graph:
- 81 bridge modules
- 443 internal edges
- largest SCC: 7
- cyclic modules: 14
- reciprocal pairs: 10
- concrete Telegram importers: 28
- SCC sizes: 7 + 7

The graph model shows that retiring both edges produces two 6-module SCCs and
cyclic modules 14 -> 12.

## Cut A — World Info storage ownership

`telegram.py` imports `catalog.install_world_info_document` only to validate and
atomically install an uploaded World Info JSON file. That is storage ownership,
not catalog-panel ownership.

Create `bridge/world_storage.py` as a low-level storage adapter. It owns:

```python
def install_world_info_document(filename: str, raw: bytes) -> Path:
    ...
```

Behavior is moved verbatim:

- simple `.json` filename only;
- UTF-8/UTF-8-SIG JSON parsing;
- payload must contain an `entries` object;
- create World Info directory;
- reject overwrite;
- fsync temporary file;
- atomic `os.replace`;
- clean temporary file on error.

`world_storage.py` may import `WORLD_DIR` from configuration and Python stdlib,
but must not import Telegram, Catalog UI, Cards, or application services.

`telegram.py` imports the canonical function from `world_storage`.
`catalog.py` no longer defines or re-exports it.
Tests move to the canonical storage owner.

## Cut B — Curated Memory delivery ownership

Create pure `bridge/curated_memory_panel.py` with no `bridge.*` imports.

It owns:

```python
def curated_memory_panel(text: str) -> tuple[str, dict]:
    ...
```

The builder preserves the current visible text and inline keyboard exactly:

- title: `Curated memory`
- empty state: `No curated durable memories yet.`
- Refresh -> `curated:refresh`
- Memory -> `curated:back`
- Close -> `curated:close`

### Memory Curator command

`handle_curated_memory_command(..., *, provider_port: ProviderPort,
delivery_port: DeliveryPort, request_context)` uses:

- `delivery_port.send_panel_request` for status menu;
- `delivery_port.send_text` for off/refresh/help responses;
- `delivery_port.send_typing` before a refresh;
- existing ProviderPort for model generation.

A local `memory_curator.send_curated_memory_menu(..., delivery_port, ...)`
reads the curated text, builds canonical panel data, and performs send/edit delivery.

`_memory_curator_command_route(..., services=...)` forwards both
`services.provider` and `services.delivery`.

`memory_curator.py` no longer imports `bridge.status_panels`.
It may continue importing Telegram's `load_session` for the background worker;
that broader runtime boundary is explicitly deferred.

### StatusPanels

`status_panels.send_curated_memory_menu` remains the callback/UI wrapper.
It reads curated state as today, uses the pure panel builder, and delivers through
its existing `send_panel_message` boundary.

## Dependency direction

After the wave:

- `telegram -> catalog` is absent;
- Telegram depends on lower-level `world_storage` instead;
- `memory_curator -> status_panels` is absent;
- `status_panels -> memory_curator` may remain until later feature-panel
  decomposition;
- new panel-data dependency points to a pure module only.

Expected graph:
- largest SCC: 7 -> 6
- cyclic modules: 14 -> 12
- reciprocal pairs: 10 -> 8
- SCC sizes: 6 + 6
- no newly cyclic module

## Behavior invariants

World storage:
- same validation errors;
- same file bytes;
- same overwrite behavior;
- same atomic-write semantics;
- same returned path.

Curated memory:
- same text/markup/callback data;
- same send/edit behavior;
- same memory-off response;
- same refresh generation flow;
- same provider and request-context identity.

## Non-goals

- no Memory Curator worker/session loading refactor;
- no broader Catalog UI decomposition;
- no Memory command delivery refactor;
- no route_update or transaction changes;
- no expression-photo transport work;
- no streaming continuation fix.

## Acceptance

1. `telegram.py` imports no `bridge.catalog`.
2. `catalog.py` no longer owns/re-exports World Info installation.
3. `memory_curator.py` imports no `bridge.status_panels`.
4. `curated_memory_panel.py` is pure.
5. exact world-install and curated-menu behavior is regression-covered.
6. focused world/memory/status/extension suites are green.
7. graph largest SCC <=6 and cyclic modules <=12 with no new cyclic module.
8. full exact-head local and GitHub CI pass.
9. merge and re-scan actual main before selecting the next cut.

# Native API and Scene Boundary Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `022adaa9ae8bf8f220774a1feb688163680cd838`

## Purpose

Retire three misplaced dependency edges across the two remaining 5-module SCCs:

- `persona_sync -> sync_api`
- `persona_sync -> sync_core`
- `scene_state -> status_panels`

Fresh merged-main graph:

- 83 bridge modules
- 443 internal edges
- largest SCC: 5
- cyclic modules: 10
- reciprocal pairs: 7
- Telegram importers: 27
- SCC sizes: 5 + 5

The graph model predicts the three coordinated ownership corrections produce SCC
sizes 4 + 3 and cyclic modules 7.

## Cut A — Neutral SillyTavern API infrastructure

Create `bridge/sillytavern_api.py` as the canonical infrastructure owner for the
loopback SillyTavern HTTP API.

It owns:

- API URL / handle / password / request-timeout configuration;
- `SillyTavernApiError`;
- `SillyTavernApiClient`;
- redirect refusal and request transport internals;
- API URL validation;
- `phase3_api_configured()`;
- `phase3_client()`;
- API client cache and lock;
- `refresh_sillytavern_api_config()`.

The module may import only lower-level/configuration infrastructure. In particular it
must not import:

- `sync_api`
- `sync_core`
- `persona_sync`
- Telegram/application UI modules.

Payload limits use canonical `config.SYNC_MAX_BYTES` directly.

### Sync API ownership

`sync_api.py` retains sync application behavior:

- transcript/snapshot conversion;
- conflict/realtime state;
- polling worker and interval;
- sync now/toggle/poll orchestration;
- SyncService composition helper.

`sync_api.refresh_phase3_config()` remains the public sync-worker configuration
entry point. It:

1. refreshes only `PHASE3_SYNC_INTERVAL_SECONDS`;
2. calls `sillytavern_api.refresh_sillytavern_api_config()`.

`sync_api.py` imports the neutral API module as a module namespace and calls
`_st_api.phase3_client()`, `_st_api.phase3_api_configured()`, and
`_st_api.SillyTavernApiError`. It does not re-export moved client/error/config
symbols.

### Persona Sync ownership

`persona_sync.py` imports `bridge.sillytavern_api as _st_api` and uses:

- `_st_api.phase3_api_configured()`;
- `_st_api.phase3_client()`;
- `_st_api.SillyTavernApiError`.

For payload validation it imports `SYNC_MAX_BYTES` from `bridge.config`
directly. It no longer imports either `sync_api` or `sync_core`.

### Startup composition

`main.py` uses the canonical neutral API owner for:

- API-configured state;
- startup client authentication;
- expected API error type.

It continues calling `sync_api.refresh_phase3_config()` as the single startup
refresh entry point so polling interval and client config are refreshed together.

### Test ownership migration

Tests must patch/read the new canonical owner:

- API URL/credentials/timeout globals -> `bridge.sillytavern_api`;
- client constructor and `phase3_client` -> `bridge.sillytavern_api`;
- API exception -> `bridge.sillytavern_api`.

No compatibility aliases remain in `sync_api` or `persona_sync`.

## Cut B — Scene State panel delivery

Create pure `bridge/scene_panel.py` with no `bridge.*` imports.

It owns:

```python
def scene_panel(
    state: dict[str, object] | None,
    covered_until_rowid: int,
) -> tuple[str, dict]:
    ...
```

The output must preserve current panel text/markup exactly:

- title includes covered message row;
- same JSON rendering with sorted keys / UTF-8;
- same empty-state text;
- Refresh / Clear / Status / Close callbacks unchanged.

### Scene command delivery

`scene_state.py` owns a command-side `send_scene_menu(..., delivery_port,
request_context)` wrapper:

1. read scene state through `get_scene_state`;
2. render through pure `scene_panel`;
3. call `delivery_port.send_panel_request` using send/edit method semantics.

`handle_scene_command(...)` gains required `delivery_port: DeliveryPort`.

For command UI effects it uses:

- `delivery_port.send_panel_request` for status;
- `delivery_port.send_text` for clear/refresh/error text;
- `delivery_port.send_typing` for refresh activity.

The extension command route forwards exact `services.delivery` and
`services.provider`.

Background Scene State refresh behavior remains unchanged. The module may retain its
Telegram dependency for background session loading; this PR specifically retires the
cycle-forming StatusPanels dependency.

### StatusPanels

`status_panels.send_scene_menu` remains the callback/UI wrapper and renders via the
same pure `scene_panel` function, then uses its existing `send_panel_message`
delivery path.

`scene_state.py` no longer imports `status_panels`.

## Dependency direction

After this wave:

- `persona_sync -> sync_api` absent;
- `persona_sync -> sync_core` absent;
- `scene_state -> status_panels` absent;
- both Sync API and Persona Sync depend downward on `sillytavern_api`;
- both Scene State and StatusPanels depend downward on pure `scene_panel`.

Expected graph:

- largest SCC: 5 -> 4
- cyclic modules: 10 -> 7
- reciprocal pairs: reduced accordingly
- SCC sizes: 4 + 3
- no newly cyclic module

## Behavior invariants

Native API:

- same loopback-only URL validation;
- same auth/cookie/CSRF behavior;
- same allowed routes;
- same request timeout bounds;
- same max response size;
- same HTTP/protocol error text/status/transient fields;
- same settings/persona/chat read/write behavior;
- same polling interval bounds and startup refresh behavior.

Persona:

- same local-file fallback when API is not configured;
- same readback verification and error behavior;
- same payload limit.

Scene:

- same scene extraction/persistence;
- same panel text and callbacks;
- same clear/refresh/status command behavior;
- same provider and request context forwarding.

## Non-goals

- no SyncService behavior redesign;
- no PersonaService rewrite;
- no Scene State background-session transport extraction;
- no StatusPanels callback decomposition beyond shared pure presentation;
- no transaction changes;
- no route_update changes;
- no streaming continuation fix.

## Acceptance

1. `sillytavern_api.py` is independent of sync/persona/application modules.
2. `persona_sync.py` imports neither `sync_api` nor `sync_core`.
3. `sync_api.py` does not define/re-export moved API client/error/config ownership.
4. startup uses canonical API infrastructure.
5. native API tests migrate to canonical ownership and remain green.
6. `scene_panel.py` is pure.
7. `scene_state.py` imports no `status_panels`.
8. Scene command route forwards exact DeliveryPort and ProviderPort.
9. panel text/markup and API behavior are regression-covered.
10. graph largest SCC <=4 and cyclic modules <=7 with no new cyclic module.
11. full exact-head local and GitHub CI pass.
12. merge and re-scan actual main before selecting the next cut.

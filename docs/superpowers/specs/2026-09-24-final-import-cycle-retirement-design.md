# Final Import Cycle Retirement Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `f42bb2557c8a0eeee8560622a657291f4418f42c`

## Purpose

Retire the final strongly connected component in the bridge import graph:

- `bridge.cards`
- `bridge.persona_sync`
- `bridge.telegram`

Fresh merged-main graph:

- 85 bridge modules
- 445 internal edges
- largest SCC: 3
- cyclic modules: 3
- reciprocal pairs: 3
- Telegram importers: 27
- only cyclic component: `cards/persona_sync/telegram`

The component is a fully reciprocal triangle:

- `cards -> persona_sync`
- `cards -> telegram`
- `persona_sync -> cards`
- `persona_sync -> telegram`
- `telegram -> cards`
- `telegram -> persona_sync`

The correct final dependency order is:

```text
Cards presentation -> Telegram transport -> Persona storage
```

with lower-level pure helpers below all three.

## Cut A — PersonaSync owns persona identity

Move the persona identity helpers from `cards.py` into the canonical native Persona
owner `persona_sync.py`:

- `get_persona(persona_id)`
- `default_persona_id()`
- `persona_name(persona_id)`

Behavior is preserved exactly:

### get_persona

Returns `load_personas().get(persona_id)`.

### default_persona_id

- loads the native Persona catalog;
- reads native settings through the canonical `_native_settings()` path;
- reads `power_user.default_persona`;
- returns it only when that identifier exists in the native catalog;
- logs the same warning and returns an empty string on failure.

### persona_name

Returns the native Persona name or an empty string.

Production consumers migrate to `persona_sync`:

- startup PersonaService composition;
- Telegram session-default validation;
- macro rendering in Commands;
- status panel rendering;
- SyncCore snapshot/user-name logic.

Test helpers migrate to the same canonical owner.

`cards.py` no longer imports `persona_sync` and no longer defines compatibility
re-exports for these functions.

## Cut B — Remove unused PersonaSync reverse imports

`persona_sync.py` currently imports:

- `cards.default_persona_id`
- `telegram.update_session`

Neither imported symbol is referenced by PersonaSync production code.

Delete both imports.

After this change PersonaSync must import neither Cards nor Telegram.

No replacement collaborator is required because no behavior depended on either import.

## Cut C — Telegram stops importing Cards

Telegram currently imports three Cards symbols:

- `default_persona_id`
- `get_persona`
- `send_panel_message`

The first two move to canonical `persona_sync`.

The remaining `send_panel_message` dependency is presentation plumbing, not Cards
ownership.

### Pure panel request builder

Extend pure `panel_utils.py` with:

```python
def panel_message_request(
    chat_id: str,
    text: str,
    reply_markup: dict,
    message_id: int | None = None,
) -> tuple[str, dict]:
    ...
```

It returns the exact current method/payload semantics:

- `sendMessage` when `message_id` is absent;
- `editMessageText` when `message_id` is present;
- payload always contains `chat_id`, `text`, and `reply_markup`;
- payload contains `message_id` only for edits.

`cards.send_panel_message` uses this builder and its existing
`telegram.send_panel_request` boundary.

Telegram's two session-delete panel helpers use the same builder and Telegram's own
`send_panel_request` function directly.

This removes `telegram -> cards` while preserving panel session binding, because
delivery still flows through the exact same `send_panel_request` implementation.

## Final dependency direction

After this PR:

- `cards -> telegram` remains for panel transport;
- `telegram -> persona_sync` remains for native Persona/default settings;
- `cards -> persona_sync` is absent;
- `persona_sync -> cards` is absent;
- `persona_sync -> telegram` is absent;
- `telegram -> cards` is absent.

Therefore the graph is acyclic.

Expected graph:

- largest SCC: 3 -> 1
- cyclic modules: 3 -> 0
- reciprocal pairs: 3 -> 0
- no cyclic components
- no newly cyclic module

## Native ownership migration

### Cards

Cards remains the Telegram-facing card/panel presentation shell:

- Persona menu rendering uses injected `PersonaService`;
- character/session menus remain;
- generic panel delivery remains;
- card content remains delegated to `card_content`.

Cards no longer owns native Persona identity queries.

### PersonaSync

PersonaSync is the sole native Persona identity/storage owner:

- native settings;
- native Persona catalog;
- identity lookup/name/default;
- upsert/delete/integrity-checked storage.

### Telegram

Telegram owns transport/session defaults and may query PersonaSync for:

- default Persona;
- Persona existence;
- native settings path used for native World Info default resolution.

It no longer imports Cards.

## Test ownership migration

Tests must use canonical ownership:

- Persona identity tests use `persona_sync`;
- startup composition patches canonical `main.default_persona_id` only where the
  startup import seam is intentionally under test;
- SyncCore tests continue to exercise public SyncCore behavior after its imports
  migrate to PersonaSync;
- application import boundary tests remove persona identity functions from the Cards
  expected export list and validate their PersonaSync ownership;
- Telegram session default tests patch the canonical PersonaSync-owned helper seam
  exposed in Telegram's module namespace only when testing Telegram behavior.

No compatibility aliases are retained in Cards.

## Behavior invariants

- same default Persona selection/fallback;
- same native Persona lookup/name semantics;
- same warning behavior when native Persona settings are unavailable;
- same session-default repair behavior;
- same macro/status/sync user-name behavior;
- same session-delete panel text/markup/callback data;
- same panel binding/session ownership;
- same PersonaService composition behavior.

## Non-goals

- no broader Cards/Telegram delivery-port migration;
- no native World Info storage redesign;
- no PersonaService API redesign;
- no session persistence changes;
- no route_update decomposition;
- no transaction changes;
- no streaming continuation fix.

## Acceptance

1. `persona_sync.py` imports neither Cards nor Telegram.
2. `cards.py` imports no PersonaSync and no longer defines persona identity helpers.
3. `telegram.py` imports no Cards.
4. Persona identity helpers have one canonical owner: PersonaSync.
5. `panel_utils.panel_message_request` is pure and regression-covered.
6. Cards and Telegram preserve exact send/edit panel payload behavior.
7. all migrated persona/session/sync/status tests are green.
8. graph reports zero cyclic modules and zero reciprocal pairs.
9. full exact-head local + GitHub CI pass.
10. merge, verify actual main, and re-scan to confirm import-cycle retirement is complete.

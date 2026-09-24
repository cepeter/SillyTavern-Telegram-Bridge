# Reset Panel Ownership Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `1680dde22d555ca848b5fc53ddbebf376e180783`

## Purpose

Retire the remaining `commands <-> message_commands` two-module cycle.

Fresh merged-main graph:
- 79 modules
- 440 internal edges
- largest SCC: 9
- cyclic modules: 19
- reciprocal pairs: 14
- Telegram importers: 30
- SCC sizes: 9, 8, 2

The two-module SCC exists because:
- `message_commands -> commands` for `edit_last_user`;
- `commands -> message_commands` for `send_reset_confirmation_menu`.

Reset confirmation is presentation data, not conversation-generation ownership.

## Design

Create pure `bridge/reset_panel.py` with no `bridge.*` imports.

It owns:

```python
RESET_CONFIRMATION_TEXT: str

def reset_confirmation_request(
    chat_id: str,
    message_id: int | None = None,
) -> tuple[str, dict]:
    ...
```

The returned method/payload must be byte-for-byte equivalent to the current reset
confirmation panel:

- `sendMessage` without message_id;
- `editMessageText` with message_id;
- identical text;
- identical Confirm/Cancel callback markup.

Existing callers keep their current delivery boundary:

- `message_commands.prepare_message()` builds the request and calls its existing
  `send_panel_request`.
- `commands.handle_macro_command()` builds the same request and uses its existing
  `send_panel_request`.
- `help.handle_enum_callback()` uses the canonical reset panel text/markup through
  its existing `send_panel_message` helper.

No reset-panel compatibility alias remains in `message_commands.py`.

## Dependency direction

After this PR:

- `message_commands -> commands` remains for `edit_last_user`;
- `commands -> message_commands` is absent;
- `help -> message_commands` is absent;
- all three consumers may import pure `reset_panel`.

Expected graph:
- largest SCC remains 9;
- cyclic modules 19 -> 17;
- reciprocal pairs 14 -> 13;
- the two-module Commands/MessageCommands SCC disappears;
- no newly cyclic module.

## Non-goals

- no edit-message ownership refactor;
- no Help/InputFlow cycle work;
- no behavior change to reset persistence or confirmation callbacks;
- no route_update work;
- no transaction changes;
- no fix to unrelated legacy/dead macro branches unless required by tests.

## Acceptance

1. `reset_panel.py` is pure.
2. `commands.py` no longer imports `message_commands.py`.
3. `help.py` no longer imports `message_commands.py`.
4. `message_commands.py` no longer defines/re-exports reset confirmation UI.
5. reset request method/text/markup are regression-covered.
6. existing reset command/callback behavior stays green.
7. graph cyclic modules <=17, no newly cyclic module.
8. exact-head local + GitHub CI green, merge, then re-scan actual main.

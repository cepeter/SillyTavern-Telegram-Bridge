# Language Runtime Boundary Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `2c9a9c122f29d9ce253a7893fa29cd1d1b5e1116`

## Purpose

Remove the remaining `language -> telegram` dependency while preserving response-language selection, callback pagination, session persistence, and user-visible messages.

Fresh merged-main graph:

- 78 bridge modules
- 438 internal edges
- largest SCC: 11
- cyclic modules: 21
- reciprocal pairs: 15
- concrete Telegram importers: 31

Removing `language -> telegram` in the graph model keeps the largest SCC at 11 but reduces cyclic modules from 21 to 19. Because `generation` depends on `language`, this cut is expected to free both modules from that SCC.

## Design

`language.py` remains the canonical owner of:
- response/STT language normalization
- labels and prompt instructions
- language menu markup/text
- response-language command behavior

It must no longer import `bridge.telegram`.

Runtime effects become explicit collaborators:

- `send_language_menu(..., *, delivery_port: DeliveryPort, request_context)`
  uses `delivery_port.send_panel_request`.
- `set_response_language(..., *, update_session: Callable[..., object], operation_id=None)`
  validates then calls the supplied canonical session-update backend.
- `handle_language_command(..., *, delivery_port: DeliveryPort, update_session: Callable[..., object], request_context)`
  uses `delivery_port.send_text` and the two functions above.

Command routing already has `services.delivery` and will pass canonical
`telegram.update_session` as the persistence collaborator. Callback routing already
receives `DeliveryPort` and imports `update_session`; it forwards both.

No new global, service locator, compatibility re-export, dynamic import, or session
persistence implementation is introduced.

## Non-goals

- no pending-input refactor in this PR;
- no route_update decomposition;
- no transaction semantics change;
- no new LanguageService;
- no changes to language codes, labels, menu pagination, callback data, or messages.

## Acceptance

1. `language.py` imports no `bridge.telegram`.
2. Language normalization/instruction/menu behavior is unchanged.
3. command and callback paths pass explicit delivery/session-update collaborators.
4. direct tests use canonical language ownership rather than accidental re-exports.
5. full local and GitHub CI are green.
6. graph measures cyclic modules <=19 with no newly cyclic module.
7. merge and re-scan actual main before choosing the pending-input or next Telegram cut.

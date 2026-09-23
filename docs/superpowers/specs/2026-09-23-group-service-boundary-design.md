# GroupService Application Boundary Design

Date: 2026-09-23
Status: approved for implementation
Base: `main` at `ff4420d5fb622a948eafbe725cebd9ce63f70206`
Parent: `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`

## Purpose

Complete the GroupService boundary from the master ports-and-adapters target.
Group state persistence and turn policy already live in `group_core.py`, but
application/UI modules still call those functions directly. Application code
should instead consume one required service composed at startup.

The post-PR-91 graph has cyclic components of 12 and 11 modules. This work is
primarily an ownership milestone; its graph effect is measured after merge
before selecting the ModelRouter/provider work.

## Goals

1. Add pure `bridge/group_service.py` with no concrete bridge imports.
2. Make `BridgeServices.group` required.
3. Bind GroupService to existing `group_core` functions only in composition.
4. Migrate application/UI consumers away from direct `group_core` imports.
5. Preserve commands, panels, turn gating, generation, recovery, image replies,
   session setup, prompt/status output, idempotency, and persistence semantics.
6. Add no optional fallback, compatibility wrapper, locator, or dynamic import.

## Non-goals

No schema/repository rewrite, GroupDirector policy change, ModelRouter work,
route-update decomposition, broad transaction cleanup, or streaming fix.
`sync_api.py` is a lower-level backend adapter and may continue reading
canonical `group_core` state directly in this milestone.
## GroupService interface

Create a frozen dataclass with required backend callables. Public methods:

```python
state(db, chat_id, session_id) -> dict[str, object]
save(db, chat_id, session_id, state, operation_id=None) -> bool
user_turn_allowed(db, chat_id, session_id, sender_id) -> bool
claim_user_turn(db, chat_id, session_id, sender_id) -> bool
pass_user_turn(db, chat_id, session_id, sender_id) -> bool
setup_state(db, chat_id, session_id) -> dict | None
character_option_label(path: Path) -> str
resolve_character(requested: str) -> str | None
member_labels(member_files: list[str]) -> list[str]
current_speaker(db, chat_id, session, user_text="") -> tuple[str, dict[str, object]] | None
advance_turn(db, chat_id, session_id, operation_id=None) -> None
```

Methods add no policy; they delegate to canonical backends.

## Composition

`BridgeServices` and `build_bridge_services()` require `group: GroupService`.
Startup constructs GroupService from `group_core` before GroupDirectorService.
GroupDirectorService receives `group.state` and `group.member_labels`.
Tests gain `make_test_group_service()`.

## Consumer migration

Conversation preparation uses `services.group.current_speaker()`.
Reply persistence receives required `group_service` and calls
`advance_turn()`. ConversationService forwards `services.group`.

Image processing receives required `group_service` for speaker selection and
turn advancement. The image worker passes `services.group`. Committed message
recovery uses `services.group`.

Manual group-turn admission in `route_update()` uses
`services.group.user_turn_allowed()`.

`groups.py` remains the Telegram/UI adapter, but every function that reads or
mutates group state receives required `group_service: GroupService`.
Command, callback, panel, pending-input, session-name, status, and prompt paths
propagate the same injected service.
## Dependency direction

After migration, production imports of `bridge.group_core` are allowed only in:

- `bridge.main` for composition;
- `bridge.sync_api` as the lower-level Live Sync backend adapter.

Application/UI modules receive GroupService from `BridgeServices` or as an
explicit required parameter. `group_service.py` imports no `bridge.*` module.

## Error and durability behavior

GroupService delegates directly to current backends, so operation idempotency,
write transactions, group mode behavior, manual-turn ownership, and turn
advancement remain unchanged. No persistence commit moves in this PR.

## Testing

TDD guards must first fail on merged main because GroupService/composition are
missing and application modules still import `group_core`.

Behavior coverage must pin:

- service delegation and argument preservation;
- manual turn routing through `services.group`;
- conversation speaker selection and turn advancement;
- image speaker selection and turn advancement;
- committed recovery advancement;
- group command/menu operations through the injected service;
- group-session naming through the injected service;
- status/prompt group reads through the injected service.

Full verification includes focused suites, compileall, `git diff --check`,
full pytest-xdist, fresh-process imports, graph scan, exact-head GitHub CI,
merge, and post-merge re-scan.

## Acceptance criteria

1. `group_service.py` is pure and acyclic.
2. `BridgeServices.group` and `build_bridge_services(..., group=...)` are required.
3. No application/UI module imports `bridge.group_core`.
4. Only `main.py` and `sync_api.py` retain production `group_core` imports.
5. Existing group behavior stays green.
6. Exact-head local and GitHub CI pass.
7. Actual merged main is re-scanned before the next provider/ModelRouter slice.

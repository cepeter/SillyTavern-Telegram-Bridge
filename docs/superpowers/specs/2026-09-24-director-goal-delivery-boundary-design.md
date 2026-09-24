# Director Goal Delivery Boundary Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `42ab9cde4fcfd67c0843910ca7e8cd81217e86e8`

## Purpose

Retire the `director_goals -> status_panels` dependency and the concrete
`director_goals -> telegram` delivery dependency.

Fresh merged-main graph:
- 80 bridge modules
- 441 internal edges
- largest SCC: 9
- cyclic modules: 17
- reciprocal pairs: 13
- concrete Telegram importers: 30
- SCC sizes: 9, 8

Removing `director_goals -> status_panels` in the graph model changes the SCCs to
8 + 8 and cyclic modules to 16. Removing the direct Telegram delivery dependency is
also architecturally aligned with the existing DeliveryPort boundary.

## Design

Create pure `bridge/director_goal_panel.py` with no `bridge.*` imports.

It owns:

```python
def director_goal_panel(goal: str) -> tuple[str, dict]:
    ...
```

The function returns the current Director Goal panel text and inline keyboard
byte-for-byte:

- title: `Director objective`
- empty state: `No hidden objective is set.`
- buttons:
  - Set objective -> `goal:set`
  - Clear -> `goal:clear`
  - Close -> `goal:close`

### Director Goal command delivery

`handle_director_goal_command(..., *, delivery_port: DeliveryPort, request_context)`
uses only:

- `delivery_port.send_text` for topic validation / clear / set responses;
- `delivery_port.send_panel_request` for the status panel;
- local `get_director_goal` / `set_director_goal` persistence.

It must not import `bridge.status_panels` or `bridge.telegram`.

`_director_goal_command_route(..., services=...)` passes `services.delivery`.

### Shared Status panel rendering

`status_panels.send_director_goal_menu` remains the existing callback/pending-input
UI wrapper. It uses `director_goal_panel(goal)` for canonical text/markup and its
existing `send_panel_message` delivery boundary.

This avoids duplicating presentation content while keeping StatusPanels responsible
for its existing callback rendering path.

## Dependency direction

After this PR:

- `status_panels -> director_goals` remains for goal state read/write;
- `director_goals -> status_panels` is absent;
- `director_goals -> telegram` is absent;
- both Director Goals and Status Panels may import pure `director_goal_panel`.

Expected graph:
- largest SCC: 9 -> 8
- cyclic modules: 17 -> 16
- reciprocal pairs: 13 -> 12
- Telegram importers: 30 -> 29
- no newly cyclic module

## Non-goals

- no InputFlowService widening;
- no removal of `input_flows -> status_panels` in this PR;
- no DirectorGoal persistence semantics change;
- no GroupDirector behavior change;
- no route_update / transaction / streaming changes.

## Acceptance

1. `director_goal_panel.py` is pure.
2. `director_goals.py` imports neither `status_panels` nor Telegram.
3. Director Goal command route uses required DeliveryPort.
4. status_panels uses the shared pure panel builder.
5. text, markup, callback data, set/clear/status behavior are regression-covered.
6. largest SCC <=8 and cyclic modules <=16 with no newly cyclic module.
7. full local + GitHub CI green.
8. merge and re-scan actual main before choosing the next cut.

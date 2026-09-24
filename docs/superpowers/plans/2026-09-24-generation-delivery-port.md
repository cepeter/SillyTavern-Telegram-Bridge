# Generation DeliveryPort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Remove generation's concrete media/Telegram dependencies through one required DeliveryPort while preserving delivery/recovery behavior.

**Spec:** `docs/superpowers/specs/2026-09-24-generation-delivery-port-design.md`

## Global constraints

- Base: `74bd498036465f7f083e0d3aa17835f506eeefed`.
- No compatibility aliases, delivery singleton, optional fallback or service locator.
- Do not modify ProviderPort behavior or the known streaming continuation limitation.
- Do not broaden into unrelated Telegram adapter migration.

## Review Focus

1. local-committed regen/continue must redeliver without generation;
2. recovery cleanup must use the exact injected request/delete collaborators;
3. swipe panel requests must retain RequestContext scoping;
4. reply delivery must still go through media's canonical send_reply;
5. generation.py must have zero media/telegram imports.

### Task 1: Add pure required DeliveryPort

**Files:** create delivery_port.py; modify composition/main/test setup; add
test_generation_delivery_port.py and explicit BridgeServices fixtures.

- [ ] Write RED tests for pure port, required composition and startup binding.
- [ ] Run focused RED.
- [ ] Implement DeliveryPort and required BridgeServices.delivery.
- [ ] Update startup/test composition.
- [ ] Run focused GREEN.
- [ ] Commit `refactor: add required generation delivery port`.

### Task 2: Migrate generation delivery and recovery

**Files:** generation.py, command_routes.py, message_commands.py and recovery tests.

- [ ] Add RED guards for no media/telegram imports and no module-global recovery binding.
- [ ] Add injected recovery/reply/typing behavior tests.
- [ ] Replace global recovery with DeliveryPort-bound local factory.
- [ ] Require DeliveryPort on regenerate/continue and internal rendered-reply helper.
- [ ] Propagate services.delivery through command/message paths.
- [ ] Run recovery/generation focused suite.
- [ ] Commit `refactor: inject delivery port into generation workflows`.
### Task 3: Migrate swipe delivery callbacks

**Files:** generation.py, command_routes.py, panel_callback_routes.py,
callback_dispatch.py and affected tests.

- [ ] Add RED tests for swipe send/edit through injected DeliveryPort.
- [ ] Require DeliveryPort on send_swipe_menu/edit_swipe_menu.
- [ ] Propagate through command route and callback dispatch.
- [ ] Run swipe/panel/callback focused suite.
- [ ] Commit `refactor: route swipe delivery through port`.

### Task 4: Verify, publish, merge and re-scan

- [ ] Architecture guard: generation imports no media/telegram; DeliveryPort pure;
  no global/fallback binding.
- [ ] Fresh-process import, compileall and diff-check.
- [ ] Graph target: largest SCC <=11, cyclic modules <=21, Telegram importers <=31.
- [ ] Full pytest-xdist suite.
- [ ] Whole-branch self-review against Review Focus.
- [ ] Publish exact head; require GitHub test and dependency-audit.
- [ ] Merge; fetch actual main; rerun graph/full verification and choose next cut.

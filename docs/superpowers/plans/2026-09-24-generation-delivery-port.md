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

- [x] Write RED tests for pure port, required composition and startup binding.
- [x] Run focused RED.
- [x] Implement DeliveryPort and required BridgeServices.delivery.
- [x] Update startup/test composition.
- [x] Run focused GREEN.
- [x] Commit `refactor: add required generation delivery port`.

### Task 2: Migrate generation delivery and recovery

**Files:** generation.py, command_routes.py, message_commands.py and recovery tests.

- [x] Add RED guards for no media/telegram imports and no module-global recovery binding.
- [x] Add injected recovery/reply/typing behavior tests.
- [x] Replace global recovery with DeliveryPort-bound local factory.
- [x] Require DeliveryPort on regenerate/continue and internal rendered-reply helper.
- [x] Propagate services.delivery through command/message paths.
- [x] Run recovery/generation focused suite.
- [x] Commit `refactor: inject delivery port into generation workflows`.
### Task 3: Migrate swipe delivery callbacks

**Files:** generation.py, command_routes.py, panel_callback_routes.py,
callback_dispatch.py and affected tests.

- [x] Add RED tests for swipe send/edit through injected DeliveryPort.
- [x] Require DeliveryPort on send_swipe_menu/edit_swipe_menu.
- [x] Propagate through command route and callback dispatch.
- [x] Run swipe/panel/callback focused suite.
- [x] Commit `refactor: route swipe delivery through port`.

### Task 4: Verify, publish, merge and re-scan

- [x] Architecture guard: generation imports no media/telegram; DeliveryPort pure;
  no global/fallback binding.
- [x] Fresh-process import, compileall and diff-check.
- [x] Graph target: largest SCC <=11, cyclic modules <=21, Telegram importers <=31.
- [x] Full pytest-xdist suite.
- [x] Whole-branch self-review against Review Focus.
- [ ] Publish exact head; require GitHub test and dependency-audit.
- [ ] Merge; fetch actual main; rerun graph/full verification and choose next cut.


## Local Verification Record

- Base: 74bd498036465f7f083e0d3aa17835f506eeefed.
- Task 1 DeliveryPort RED: 3 failed; GREEN: 46 passed + 30 subtests.
- Task 2 core architecture RED: 4 failed; GREEN core: 7 passed.
- Tasks 2–3 recovery/swipe focused migration: 88 passed + 163 subtests.
- Task 2 verification subset: 30 passed + 4 subtests.
- Task 3 verification subset: 15 passed + 129 subtests.
- Full branch suite: 804 passed + 479 subtests, exit 0.
- Fresh-process imports passed for DeliveryPort, generation, command/message/callback
  routes, composition and main.
- compileall and git diff --check passed.
- Architecture guards: DeliveryPort pure; generation imports neither media nor
  Telegram; no module-global or fallback delivery binding.
- Graph: 78 modules, 438 edges, largest SCC 12→11, cyclic modules 23→21,
  reciprocal pairs 16→15, concrete Telegram importers 32→31.
- Whole-branch author self-review: no remaining Critical, Important, or Minor
  findings against the five Review Focus conditions.

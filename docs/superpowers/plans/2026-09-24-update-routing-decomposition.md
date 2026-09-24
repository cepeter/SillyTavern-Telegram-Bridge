# Telegram Update Routing Decomposition Implementation Plan

### Task 1: TDD coordinator ownership
- [ ] RED: callback/message focused modules exist.
- [ ] RED: route_update owns only duplicate detection, update-class selection, completion, and offset return.
- [ ] RED: route_update contains no JobService enqueue/submit and no Telegram delivery.
- [ ] RED: is_long_running_command is owned by message routing, not update_routing.

### Task 2: TDD completion semantics
- [ ] RED: duplicate update completes once and skips all focused helpers.
- [ ] RED: callback success completes once.
- [ ] RED: edited-message success completes once.
- [ ] RED: ordinary handled message completes once.
- [ ] RED: no-chat-id ordinary message does not complete.
- [ ] RED: helper exception propagates and does not complete.

### Task 3: Implement focused routing modules
- [ ] Extract callback ingress into update_callback_routing.py.
- [ ] Extract edited/ordinary ingress and command/media classification into update_message_routing.py.
- [ ] Move LONG_RUNNING_COMMANDS/is_long_running_command with message routing.
- [ ] Reduce route_update to explicit coordinator logic.
- [ ] Keep complete_update unchanged as the sole processed-update/offset transaction boundary.
- [ ] Migrate source-boundary and direct tests.
- [ ] Run focused update/job/help routing suites.
- [ ] Commit implementation.

### Task 4: Verify, publish, merge
- [ ] architecture/import-direction guards.
- [ ] graph remains fully acyclic.
- [ ] compileall, diff-check, fresh imports.
- [ ] full pytest-xdist.
- [ ] whole-diff review and one TDD fix pass if needed.
- [ ] publish exact head; require GitHub test + dependency-audit.
- [ ] merge and verify merged main.
- [ ] continue to Ruff/static/dependency-direction enforcement.

# Help and Memory Delivery Boundary Implementation Plan

### Task 1: TDD Help ownership and fast paths
- [ ] RED: Help and HelpDetails no longer import each other.
- [ ] RED: HelpDetails owns HELP_CATEGORIES and send_help_menu.
- [ ] RED: send_help_menu/send_help_command/handle_help_callback require DeliveryPort.
- [ ] RED: /help <command> forwards the correct argument order and exact DeliveryPort.
- [ ] RED: update_routing Help callback fast path forwards DeliveryPort and a concrete RequestContext.

### Task 2: TDD Memory delivery ownership
- [ ] RED: memory.py imports no Telegram.
- [ ] RED: handle_memory_command requires send_text_fn.
- [ ] RED: command_routes passes services.delivery.send_text.
- [ ] RED: input_flows passes its existing send_text collaborator.
- [ ] Preserve exact memory command user-visible text.

### Task 3: Implement coordinated cuts
- [ ] Move HELP_CATEGORIES and send_help_menu into help_details.
- [ ] Remove Help↔HelpDetails imports.
- [ ] Route all Help UI through DeliveryPort.
- [ ] Fix /help command and callback fast-path forwarding while migrating signatures.
- [ ] Remove memory Telegram import and route text through send_text_fn.
- [ ] Migrate canonical-owner tests and direct fixtures.
- [ ] Run focused Help/Memory/routing suites.
- [ ] Commit implementation.

### Task 4: Verify, publish, merge, re-scan
- [ ] architecture + graph guards.
- [ ] compileall, diff-check, full pytest-xdist.
- [ ] whole-diff review; one TDD fix pass if needed.
- [ ] publish exact head; require GitHub test + dependency-audit.
- [ ] merge, verify actual main, re-scan before next cut.

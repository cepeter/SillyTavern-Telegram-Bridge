# Reset Panel Ownership Implementation Plan

### Task 1: TDD pure reset presentation
- [ ] RED: pure reset_panel module exists and returns exact send/edit payloads.
- [ ] RED: commands/help no longer import message_commands.
- [ ] RED: message_commands no longer owns send_reset_confirmation_menu.

### Task 2: Migrate reset panel consumers
- [ ] Create pure reset_panel request builder.
- [ ] Update message_commands reset command delivery.
- [ ] Update commands reset delivery.
- [ ] Update Help enum reset panel delivery.
- [ ] Migrate tests from accidental message_commands reset-panel ownership.
- [ ] Run focused reset/audit/help/command suites and commit.

### Task 3: Verify, publish, merge, re-scan
- [ ] architecture + graph guards.
- [ ] compileall, diff-check, full pytest-xdist.
- [ ] whole-diff review; one TDD fix pass if needed.
- [ ] publish exact head; require GitHub test + dependency-audit.
- [ ] merge, verify merged main, re-scan before next cut.

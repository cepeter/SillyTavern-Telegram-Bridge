# UI Runtime Cycle Wave Implementation Plan

### Task 1: TDD architecture and collaborator contracts
- [ ] RED: expressions imports no Telegram and expression menu requires DeliveryPort.
- [ ] RED: expression command/callback paths forward exact DeliveryPort.
- [ ] RED: InputFlowService requires start_session_name backend.
- [ ] RED: groups imports no session_naming and group:new_session forwards exact InputFlowService/GroupService.

### Task 2: Implement coordinated cuts
- [ ] Route expression menu delivery through DeliveryPort.
- [ ] Propagate DeliveryPort from command and callback routers.
- [ ] Extend pure InputFlowService with required session-name starter.
- [ ] Compose canonical session_naming.start_session_name_input in main/test factory.
- [ ] Route group new-session through services.input_flow.
- [ ] Remove direct target imports.
- [ ] Migrate direct test fixtures and run focused suites.
- [ ] Commit implementation.

### Task 3: Verify, publish, merge, re-scan
- [ ] architecture + graph guards.
- [ ] compileall, diff-check, full pytest-xdist.
- [ ] whole-diff review with one TDD fix pass if needed.
- [ ] publish exact head and require GitHub test + dependency-audit.
- [ ] merge, verify actual main, re-scan before next cut.

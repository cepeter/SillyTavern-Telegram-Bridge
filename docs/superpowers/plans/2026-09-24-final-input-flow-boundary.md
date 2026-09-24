# Final InputFlow Boundary Implementation Plan

### Task 1: TDD service contract and Help startup
- [ ] RED: InputFlowService requires start_text_action and handle_session_name backends.
- [ ] RED: Help imports no InputFlows.
- [ ] RED: Help enum callback receives InputFlowService and forwards exact text-action startup arguments.
- [ ] RED: CallbackDispatch forwards exact services.input_flow.

### Task 2: TDD pending/session/director ownership
- [ ] RED: InputFlows imports neither SessionNaming nor StatusPanels.
- [ ] RED: InputFlowService.handle_pending injects exact handle_session_name backend.
- [ ] RED: pending session-name path forwards exact group/operation/request context.
- [ ] RED: Director Goal text action renders/sends the canonical pure panel without StatusPanels.

### Task 3: Implement final InputFlow service boundary
- [ ] Extend pure InputFlowService with required start_text_action and handle_session_name backends.
- [ ] Compose canonical backends in main and test factory.
- [ ] Route Help enum text-action startup through InputFlowService.
- [ ] Forward services.input_flow from CallbackDispatch.
- [ ] Inject session-name handler through InputFlowService.handle_pending.
- [ ] Replace Director Goal StatusPanels dependency with pure panel + existing panel delivery.
- [ ] Remove direct target imports.
- [ ] Migrate direct tests/fixtures.
- [ ] Run focused Help/InputFlow/SessionNaming/Director Goal suites.
- [ ] Commit implementation.

### Task 4: Verify, publish, merge, re-scan
- [ ] architecture/import guards.
- [ ] graph scan proves cyclic modules <=3 and no new cyclic module.
- [ ] fresh imports, compileall, diff-check.
- [ ] full pytest-xdist.
- [ ] whole-diff review with one TDD fix pass if needed.
- [ ] publish exact head; require GitHub test + dependency-audit.
- [ ] merge, verify merged main, re-scan before final native/Telegram cycle.

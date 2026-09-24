# InputFlowService Boundary Implementation Plan

### Task 1: TDD service and architecture
- [ ] RED: pure service module, required composition, and no message_commands -> input_flows.
- [ ] RED: prepare_message forwards exact pending-input arguments through services.input_flow.

### Task 2: Implement and migrate
- [ ] Add pure InputFlowService.
- [ ] Require BridgeServices.input_flow and compose it in main.
- [ ] Add test InputFlowService factory and migrate direct BridgeServices fixtures.
- [ ] Replace message_commands direct handler import with services.input_flow.
- [ ] Run focused conversation/pending/persona/session suites and commit.

### Task 3: Verify, publish, merge, re-scan
- [ ] architecture + graph guards.
- [ ] compileall, diff-check, full pytest-xdist.
- [ ] whole-diff review and one TDD fix pass if needed.
- [ ] publish exact head, require GitHub test + dependency-audit.
- [ ] merge, verify actual main, re-scan before next cut.

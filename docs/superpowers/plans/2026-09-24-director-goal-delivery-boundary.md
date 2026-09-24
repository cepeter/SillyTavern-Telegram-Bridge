# Director Goal Delivery Boundary Implementation Plan

### Task 1: TDD pure panel and delivery ownership
- [ ] RED: pure `director_goal_panel` module exists and returns exact text/markup.
- [ ] RED: `director_goals.py` imports neither StatusPanels nor Telegram.
- [ ] RED: Director Goal command requires DeliveryPort and forwards exact status/set/clear messages.

### Task 2: Implement and migrate
- [ ] Add pure panel-data builder.
- [ ] Make Director Goal command use DeliveryPort.
- [ ] Pass `services.delivery` from extension command route.
- [ ] Make StatusPanels reuse the pure panel builder.
- [ ] Migrate direct tests from accidental StatusPanels-owned menu seam.
- [ ] Run focused Director Goal / status / extension / input-flow suites and commit.

### Task 3: Verify, publish, merge, re-scan
- [ ] architecture + graph guards.
- [ ] compileall, diff-check, full pytest-xdist.
- [ ] whole-diff review and one TDD fix pass if needed.
- [ ] publish exact head, require GitHub test + dependency-audit.
- [ ] merge, verify merged main, re-scan before next cut.

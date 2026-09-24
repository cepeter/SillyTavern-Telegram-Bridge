# Language Runtime Boundary Implementation Plan

**Goal:** retire `language -> telegram` without changing behavior.

### Task 1: TDD architecture and behavior
- [ ] Add architecture guard that `language.py` does not import Telegram.
- [ ] Add collaborator tests for menu delivery and session update.
- [ ] Demonstrate RED on merged main.

### Task 2: Explicit runtime collaborators
- [ ] Make menu delivery use required DeliveryPort.
- [ ] Make session mutation use required update_session callable.
- [ ] Make language command use both collaborators.
- [ ] Update command_routes and panel_callback_routes forwarding.
- [ ] Migrate direct tests from accidental re-exports to canonical owner.
- [ ] Run focused language/command/panel suites and commit.

### Task 3: Verify, publish, merge, re-scan
- [ ] AST import guard and fresh imports.
- [ ] graph scan confirms cyclic modules <=19 and no new cyclic module.
- [ ] compileall, diff-check, full pytest-xdist.
- [ ] whole-diff self-review.
- [ ] publish exact head, require GitHub test + dependency-audit.
- [ ] merge, verify merged main, re-scan before next cut.

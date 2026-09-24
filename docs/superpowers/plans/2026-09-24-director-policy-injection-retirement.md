# Director Policy Injection Retirement Implementation Plan

### Task 1: TDD ownership retirement
- [ ] RED: extension registry has no Director customization value/provider/getter.
- [ ] RED: GroupDirectorService owns DirectorCustomization and DirectorPolicy.
- [ ] RED: GroupDirectorService requires director_policy field.
- [ ] RED: director_goals exposes director_goal_policy without registry dependency.
- [ ] RED: main injects exact director_goal_policy.

### Task 2: Implement direct policy injection
- [ ] Move DirectorCustomization type to GroupDirectorService.
- [ ] Add DirectorPolicy type alias.
- [ ] Rename service field to director_policy.
- [ ] Rename Director Goal provider to director_goal_policy.
- [ ] Remove Director single-provider registry state/functions/snapshot entry.
- [ ] Keep Director Goal command-route registration only.
- [ ] Inject director_goal_policy directly in main.
- [ ] Migrate tests/import ownership.
- [ ] Run focused Director/extension/composition suites.
- [ ] Commit implementation.

### Task 3: Verify, publish, merge
- [ ] static architecture checker green.
- [ ] Ruff/mypy static surface green.
- [ ] import graph remains fully acyclic.
- [ ] compileall, diff-check.
- [ ] full pytest-xdist.
- [ ] whole-diff review.
- [ ] publish exact head and require protected CI contexts.
- [ ] merge and verify merged main.

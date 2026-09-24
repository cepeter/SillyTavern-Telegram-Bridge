# Static Architecture Enforcement Implementation Plan

### Task 1: TDD dependency-direction policy
- [ ] RED: repository dependency checker artifact exists.
- [ ] RED: real repository is required to be acyclic.
- [ ] RED: synthetic cycle is rejected.
- [ ] RED: function-local import participates in cycle detection.
- [ ] RED: isolated service/port target importing bridge.* is rejected.
- [ ] RED: missing configured static target is rejected.

### Task 2: TDD static service/port surface
- [ ] Pin Ruff 0.16.8 and mypy 2.3.1 in requirements-dev.txt.
- [ ] Add pyproject Ruff/mypy configuration.
- [ ] RED: static surface passes Ruff E4/E7/E9/F.
- [ ] RED: static surface passes mypy with untyped-defs disallowed.
- [ ] Fix only annotation/narrowing findings in ProviderPort, InputFlowService, SyncService, JobService, and GroupDirectorService.
- [ ] Preserve behavior with focused service tests.

### Task 3: CI enforcement
- [ ] Add static-analysis job to ci.yml.
- [ ] Install only development tooling in the static job.
- [ ] Run dependency checker.
- [ ] Run Ruff on explicit static targets.
- [ ] Run mypy on explicit static targets.
- [ ] Keep test and dependency-audit jobs unchanged.
- [ ] Add/update source-boundary tests for CI/static policy as appropriate.

### Task 4: Verify, publish, merge, protect main
- [ ] dependency checker + checker tests green.
- [ ] Ruff static surface green.
- [ ] mypy static surface green.
- [ ] import graph remains fully acyclic.
- [ ] compileall, diff-check, full pytest-xdist.
- [ ] whole-diff review.
- [ ] publish exact head; require test + dependency-audit + static-analysis.
- [ ] merge and verify merged main.
- [ ] inspect existing main branch protection.
- [ ] require test, dependency-audit, and static-analysis without weakening existing policy.
- [ ] verify resulting protection and merged-main CI.

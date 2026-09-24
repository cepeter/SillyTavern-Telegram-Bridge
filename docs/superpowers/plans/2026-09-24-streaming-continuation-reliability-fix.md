# Streaming Continuation Reliability Fix Implementation Plan

### Task 1: TDD callback and cancellation semantics
- [ ] RED: continuation callbacks remain cumulative across HTTP segments.
- [ ] RED: cancellation set after the first length-stopped segment prevents the next request.
- [ ] RED: cancellation during a continuation prevents any further continuation request and preserves received text.
- [ ] RED: empty-content length recovery forwards stream_callback and cancel_event.

### Task 2: TDD visible continuation bound
- [ ] RED: streaming continuation performs at most three continuation requests after the initial segment.
- [ ] RED: every continuation request remains streaming.
- [ ] RED: continuation message history contains prior assistant segment + continuation instruction for each attempt.
- [ ] Keep existing non-stream continuation tests unchanged.

### Task 3: Implement iterative streaming continuation
- [ ] Add dedicated visible-continuation bound.
- [ ] Add focused SSE segment reader with cumulative callback prefix.
- [ ] Replace visible recursive continuation with iterative streaming requests.
- [ ] Preserve request payload/header/timeout settings.
- [ ] Forward callback/cancel_event through empty-content budget recovery.
- [ ] Preserve accumulated visible output on continuation failure/cancellation.
- [ ] Run focused generation/provider tests.
- [ ] Commit implementation.

### Task 4: Verify, publish, merge
- [ ] dependency/static policy green.
- [ ] Ruff/mypy protected surface green.
- [ ] import graph remains fully acyclic.
- [ ] compileall + diff-check.
- [ ] full pytest-xdist.
- [ ] whole-diff review.
- [ ] publish exact head; require protected test + dependency-audit + static-analysis.
- [ ] merge and verify merged main.

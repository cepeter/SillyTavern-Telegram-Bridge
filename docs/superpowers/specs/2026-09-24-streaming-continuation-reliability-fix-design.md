# Streaming Continuation Reliability Fix Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `4959fc714869d6156988541ab175a37431db21e7`

## Purpose

Fix the confirmed OpenAI-compatible streaming continuation defect that was
intentionally deferred during the provider-boundary and architecture migrations.

Current behavior:

- streaming responses correctly accumulate visible SSE deltas;
- `stream_callback` receives cumulative text within one HTTP response;
- `cancel_event` is checked while reading one HTTP response;
- an empty `finish_reason="length"` response may retry with a larger output budget;
- a visible `finish_reason="length"` response recursively calls
  `generate_provider_text()` with continuation messages.

The visible recursive continuation has three defects:

1. it does not forward `stream_callback`;
2. it does not forward `cancel_event`;
3. it has no dedicated visible-continuation bound and instead increments the unrelated
   recovery-attempt counter.

Because Telegram streaming edits replace the whole message, simply forwarding the
callback into the recursive call would also be incorrect: the continuation request
would emit only its local text and replace the prior visible segment.

## Scope

Fix only OpenAI-compatible streaming continuation.

Do not change:

- non-stream continuation behavior;
- Anthropic transport behavior;
- OpenCode Muse behavior;
- provider/model routing;
- generation settings semantics;
- response-language rendering;
- Telegram streaming UI code;
- architecture/service boundaries.

## Chosen design — iterative streaming segments

Replace visible recursive streaming continuation with an iterative loop local to the
OpenAI-compatible streaming path.

Define a dedicated continuation bound:

```python
_MAX_VISIBLE_CONTINUATIONS = 3
```

This matches the existing non-stream visible continuation policy:

- initial segment;
- up to three continuation HTTP requests.

The bound is independent from `_recovery_attempt`, which remains dedicated to
empty-content output-budget recovery.

## Streaming segment reader

Introduce a focused private helper conceptually equivalent to:

```python
_read_openai_stream_segment(
    response,
    *,
    prefix: str,
    stream_callback,
    cancel_event,
) -> tuple[str, str | None, bool]
```

Returns:

- current segment text;
- finish reason;
- cancellation state.

### Callback semantics

Within every segment, the callback receives the full visible response so far:

```text
prefix + current segment buffer
```

Joined with the same single-space segment separator used by the final returned
response.

Examples:

Initial segment callback:
`Part one.`

Continuation delta callback:
`Part one. Part two...`

Final continuation callback:
`Part one. Part two.`

This preserves the current Telegram contract because
`message_commands.stream_update()` replaces the placeholder text rather than
appending to it.

### Throttling

Preserve the existing 0.5 second intermediate callback throttle per HTTP segment.

Always send one final callback for a non-empty segment, using cumulative text.

## Visible length continuation flow

After the initial streaming segment:

1. append its visible content;
2. if finish reason is not `length`, return;
3. if cancellation is set, return accumulated visible content;
4. while fewer than three visible continuation requests have been attempted:
   - append the previous segment as an assistant message;
   - append the existing continuation instruction as a user message;
   - create another streaming request with the same provider/model/settings;
   - read it with the same `stream_callback` and `cancel_event`;
   - append non-empty visible content;
   - stop on non-`length` finish;
   - stop on cancellation;
   - on request/read failure, log and return accumulated content;
   - on empty continuation content, log and return accumulated content.
5. if all three continuation requests still finish by length, return all accumulated
   visible text without a fourth continuation request.

No recursive `generate_provider_text()` call is used for visible continuation.

## Cancellation semantics

Use the exact same `cancel_event` object for all streaming segments.

Required behavior:

- check cancellation while reading every response;
- check cancellation before opening every continuation request;
- if cancellation is set after a visible segment, do not start another request;
- if cancellation becomes set during a continuation segment, keep visible text
  already received and do not start another request.

This fix does not define a new cancellation exception. It preserves the existing
behavior of returning visible content accumulated before cancellation.

## Empty-content length recovery

Keep the current output-budget recovery semantics:

- only when visible content is empty;
- only for `finish_reason="length"`;
- only while `_recovery_attempt < 2`;
- use `_recovery_settings()`.

The recursive retry must now forward:

- `stream_callback`;
- `cancel_event`;
- `request_timeout`.

Because the retry has no prior visible text, callback-prefix handling is not needed.

If cancellation is already set before the recovery retry, do not open another HTTP
request; preserve current failure semantics for the empty/cancelled response rather
than issuing additional work.

## Request payload invariants

Every streaming continuation request preserves:

- `stream=true`;
- model;
- temperature;
- max_tokens;
- top_p;
- frequency/presence penalties;
- reasoning settings;
- stop sequences;
- provider headers;
- request timeout.

Continuation message construction remains:

```python
{"role": "assistant", "content": previous_segment}
{"role": "user", "content": _CONTINUATION_INSTRUCTION}
```

Each subsequent request receives all prior continuation turns, matching the existing
non-stream approach.

## Failure behavior

If an automatic continuation request fails after one or more visible segments:

- log a warning;
- return the accumulated usable content.

If a continuation returns no visible content:

- log a warning;
- return accumulated content.

Do not discard prior visible output.

## TDD acceptance

Add regression tests proving:

1. streaming length continuation remains streaming;
2. callback updates during continuation are cumulative across segments;
3. the same cancellation object stops a continuation before another network request;
4. cancellation during a continuation prevents subsequent requests while preserving
   visible content;
5. visible streaming continuation is bounded to at most three continuation requests;
6. every continuation request carries the expected assistant/user continuation turns;
7. empty-content length recovery forwards callback and cancellation collaborators;
8. existing non-stream continuation tests remain unchanged and green.

## Static / architecture acceptance

The change remains inside `provider_transport.py` and tests.

Requirements:

- dependency graph remains acyclic;
- static-analysis job remains green;
- Ruff/mypy protected surface remains green;
- full regression suite remains green;
- protected GitHub CI (`test`, `dependency-audit`, `static-analysis`) passes on
  exact head before merge.

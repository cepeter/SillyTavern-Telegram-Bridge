# Director Policy Boundary — Design

Date: 2026-09-19
Status: Proposed for implementation after review
Repository: `cepeter/SillyTavern-Telegram-Bridge`
Target implementation: Phase 1 implementation PR (number assigned when opened)
Parent architecture: `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`
Migration phase: Phase 1

## 1. Purpose

PR #27 replaced several late runtime overrides with explicit extension hooks. One important coupling remains in `bridge/director_goals.py`: it still captures and replaces `group_director_plan` and `group_prompt_context`.

This design removes those two overrides without duplicating the Group Director workflow in a new provider layer.

The long-term architectural rule is:

> Core services own execution; injected policies and adapters provide bounded customization.

For this slice, `groups.py` remains the owner of Director execution and fallback behavior. `director_goals.py` becomes a policy provider that contributes only Director-specific policy data.

This is an incremental migration step toward explicit service composition and dependency injection. The registry is a compatibility bridge, not the intended permanent application architecture.

## 2. Goals

Phase 1 implementation must:

- remove the remaining public callable overrides from `director_goals.py`
- keep one canonical Group Director execution path in `groups.py`
- preserve Director Goal behavior
- preserve the `director -> utility -> main session model` resolution chain
- preserve forced-speaker behavior
- preserve invalid-output and generation-failure fallback behavior
- preserve hidden-goal prompt behavior without writing goals into the transcript
- make Director customization explicit and testable
- make repeated runtime loading deterministic
- provide an interface that can later move from global registration to constructor injection

## 3. Non-goals

Phase 1 implementation will not:

- redesign Director prompting for quality
- change Group modes or user-visible commands
- move `director_goals` schema creation
- remove commits from Director Goal persistence
- refactor Group persistence
- modify Scene State or Memory Curator
- remove `runtime_loader.py`
- replace all registries with dependency injection
- introduce an ORM
- redesign task-model routing

Those changes belong to later architectural slices.

## 4. Current problem

The core Group Director implementation already exists in `bridge/groups.py`.

It owns:

1. group-state validation
2. member filtering
3. forced-speaker handling
4. transcript collection
5. Director prompt construction
6. generation settings
7. model invocation
8. JSON decision parsing
9. round-robin fallback

`bridge/director_goals.py` currently captures the core implementations:

```python
_ORIGINAL_GROUP_DIRECTOR_PLAN_GOALS = group_director_plan
_ORIGINAL_GROUP_PROMPT_CONTEXT_GOALS = group_prompt_context
```

It then replaces both public functions.

The prompt-context replacement is only a decorator, but the planner replacement duplicates most of the core Director workflow in order to add:

- a hidden scene objective
- Director task-model routing
- a larger Director token budget
- slightly different instructions

This creates two problems.

First, behavior depends on runtime load order. Second, fixes to the core planner can be applied to `groups.py` but accidentally omitted from the duplicated planner in `director_goals.py`.

Removing only the monkey patch while keeping a whole-function provider would solve the first problem but not the second.

## 5. Chosen architecture

Use one optional, named **Director customization provider**.

The provider does not execute Director generation. It returns bounded policy data consumed by the canonical planner in `groups.py`.

Conceptually:

```text
Telegram / generation flow
        |
        v
groups.group_director_plan()
        |
        +-- validate group
        +-- honor forced speaker
        +-- collect transcript
        +-- build core Director request
        +-- ask Director policy for customization
        +-- merge bounded customization
        +-- call model
        +-- parse decision
        +-- fallback if needed

DirectorGoalPolicy
        |
        +-- read session goal
        +-- resolve Director task model
        +-- return hidden instructions
        +-- return bounded settings overrides
        +-- return speaker-context addition
```

The Groups subsystem remains the execution owner.

## 6. Why one provider, not an ordered pipeline

Command routes and post-retain hooks legitimately support multiple independent handlers.

Director policy is different. A second provider would immediately create conflict questions:

- which model wins?
- how do settings merge?
- in what order do hidden policies apply?
- can one provider override another?
- which provider owns a failure?

There is currently one policy consumer: Director Goals.

Therefore Phase 1 implementation uses exactly one named Director customization provider. A second registration is rejected.

If a real second policy use case appears later, composition semantics can be designed from concrete requirements instead of guessed now.

## 7. Proposed interface

The compatibility registry will add one single-provider slot.

The exact implementation may use a dataclass, TypedDict, or equivalent typed structure, but the semantic interface is:

```python
DirectorCustomization(
    model: str | None,
    hidden_instructions: str,
    max_tokens: int | None,
    speaker_context: str,
)
```

The provider receives enough context to resolve policy without taking ownership of execution:

```python
provider(
    db,
    chat_id,
    session,
) -> DirectorCustomization | None
```

The provider must not receive:

- the model-generation callable
- the final message list for arbitrary replacement
- the fallback callback
- permission to persist transcript messages

This boundary is deliberate. Policy may customize Director behavior, but Groups owns execution.

## 8. Core Director ownership

After Phase 1 implementation, `groups.py` owns exactly one `group_director_plan()`.

Its flow is:

### 8.1 Validate

Load group state, filter valid members, and return `None` unless Director mode is enabled with at least two members.

### 8.2 Forced speaker

If `forced_speaker` is a valid member, return it immediately.

The customization provider is not needed for this path.

This preserves current behavior and avoids unnecessary DB/model-policy work.

### 8.3 Build core state

Collect:

- member labels
- recent transcript
- latest user turn
- standard Director instructions
- default generation settings
- default model

The core system prompt retains invariant requirements such as:

- select one known speaker
- produce a short direction
- do not write dialogue
- do not speak for the user
- emit strict JSON

An extension cannot remove these invariants.

### 8.4 Apply policy customization

Ask the registered Director customization provider for optional policy.

If returned:

- use `customization.model` when non-empty
- append `hidden_instructions` in the dedicated hidden-policy section
- use `customization.max_tokens` when provided
- leave `speaker_context` for `group_prompt_context()`

### 8.5 Generate and parse

Call the model once through the same canonical path.

Parse through the existing bounded Director decision parser.

### 8.6 Fallback

If generation raises or output is invalid, preserve the current round-robin fallback.

The presence or failure of Director Goal policy must not change fallback semantics.

## 9. Director Goal policy behavior

`director_goals.py` keeps responsibility for:

- goal normalization
- goal persistence
- `/group goal` command handling
- goal-specific policy content
- Director task-model selection

It stops owning:

- Director transcript collection
- final Director message construction
- `generate_text()`
- parsing the Director result
- round-robin fallback
- the base group prompt-context implementation

The policy should return approximately:

### Model

Resolve with:

```text
task model: director
        |
        v
task model: utility
        |
        v
session main model / default
```

This preserves existing Director Goals behavior.

### Hidden instructions

If no goal exists, the provider may return no hidden instructions.

If a goal exists, it contributes text equivalent in meaning to:

- this is a hidden scene objective
- advance it naturally when appropriate
- continuity and believable behavior take priority
- do not force completion
- never expose the objective

The base Director invariants remain owned by `groups.py`.

### Token budget

The base Director planner currently uses `max_tokens=180`, while Director Goals uses `max_tokens=220`.

To preserve behavior without creating a generic settings injection surface, the policy may provide only an optional `max_tokens` override. For Director Goals this is `220`.

Temperature, reasoning budget, stop sequences, and all other generation settings remain owned by `groups.py` in Phase 1 implementation.

If a future policy has a demonstrated need to customize another setting, the interface can be extended explicitly at that time.

### Speaker context

When Group mode is `director` and a goal exists, the policy provides the hidden objective block used in the current speaker generation context.

The goal remains absent from persisted transcript messages.

## 10. Prompt assembly

The canonical Director prompt should have clear ownership boundaries:

```text
Core invariant Director instructions
+
Optional hidden policy instructions
+
Allowed speakers
+
Recent transcript
+
Latest user turn
```

The extension provides content only for the optional hidden policy section.

This prevents an extension from accidentally removing safety and output-format requirements.

## 11. Group prompt context

`group_prompt_context()` remains defined only in `groups.py`.

It first builds the existing core context:

- current speaker
- other characters
- single-speaker restriction
- autonomous-mode rules when applicable
- Director pacing guidance when available

For Director mode, it may then query the same optional Director customization provider and append `speaker_context`.

This replaces the current wrapper pattern:

```text
groups.group_prompt_context
    -> late override
    -> call captured original
    -> append goal
```

with:

```text
groups.group_prompt_context
    -> build core context
    -> obtain optional policy
    -> append bounded speaker context
```

No `_ORIGINAL_GROUP_PROMPT_CONTEXT_GOALS` remains.

## 12. Failure semantics

Failure behavior must be explicit.

### Provider registration failure

Duplicate Director provider registration raises immediately.

This indicates a programming/configuration error.

### Provider execution failure

If the Director customization provider raises while planning:

1. log the provider name and exception
2. continue using ordinary Director configuration
3. attempt the normal Director generation path
4. retain normal invalid-output / generation-error fallback

An optional policy failure must not make Group Director unusable.

### Speaker-context policy failure

If policy lookup fails while building speaker context:

1. log the failure
2. return the ordinary Group Director speaker context
3. do not fail message generation solely because hidden-goal customization failed

### Core Director generation failure

Unchanged: log and fall back to round robin.

## 13. Registry lifecycle

`bridge/extension_registry.py` gains one single-provider slot with:

- explicit name
- explicit callable
- duplicate rejection
- reset support
- snapshot visibility

Conceptually:

```python
register_director_customization_provider(name, provider)
get_director_customization(...)
```

`reset_extension_registry()` clears it alongside the existing registries.

`extension_registry_snapshot()` exposes its registration name for tests and diagnostics.

Expected runtime snapshot:

```python
{
    ...
    "director_customization": ("director_goals",),
}
```

Repeated runtime loads must produce the same result.

## 14. Long-term transition to dependency injection

The registry is transitional.

The intended future form is:

```python
class DirectorPolicy(Protocol):
    def customize(...) -> DirectorCustomization | None:
        ...
```

and:

```python
director_service = GroupDirectorService(
    model_router=model_router,
    policy=DirectorGoalPolicy(...),
)
```

At that point the global registry lookup disappears.

Phase 1 implementation should choose names and responsibilities that map cleanly to that future interface.

The long-term architecture remains:

```text
UI / Telegram adapters
        |
Application services
        |
Domain policies / ports
        |
Infrastructure adapters
```

Phase 1 implementation establishes the policy boundary but does not introduce the full service layer yet.

## 15. Files expected to change

Primary:

- `bridge/extension_registry.py`
- `bridge/groups.py`
- `bridge/director_goals.py`
- `bridge/runtime_loader.py`
- `tests/test_runtime_loader.py`
- `tests/test_group_director.py`
- `tests/test_director_goals.py`

A small dedicated policy-registry test file may be added if that keeps tests clearer.

No unrelated files should be changed unless implementation reveals a required dependency.

## 16. Required regression coverage

Implementation is not complete unless tests demonstrate all of the following.

### Registry

- one Director provider registers successfully
- a second provider is rejected
- reset removes the provider
- repeated runtime loads restore the same provider name deterministically

### Runtime loader

- `director_goals.py` reports zero public callable overrides
- the safety override allowlist no longer lists `director_goals.py`

### Director execution

- known model output selects the expected speaker
- invalid output still falls back to round robin
- generation exceptions still fall back to round robin
- forced speaker still bypasses Director model generation
- the canonical core planner is used with and without policy

### Director Goals

- the hidden goal reaches Director planning
- the Director task model is selected
- utility fallback still works when no Director-specific model is configured
- main session model fallback still works when neither Director nor utility model is configured
- the hidden goal appears in Director speaker context
- the hidden goal is not written into the transcript
- clearing the goal removes policy content
- policy failure falls back to ordinary Director behavior

## 17. Acceptance criteria

Phase 1 implementation is acceptable when:

1. `director_goals.py` contains no captured `_ORIGINAL_GROUP_DIRECTOR_PLAN_GOALS`
2. `director_goals.py` contains no captured `_ORIGINAL_GROUP_PROMPT_CONTEXT_GOALS`
3. `director_goals.py` has zero public callable runtime overrides
4. only one canonical `group_director_plan()` execution implementation remains
5. only one canonical `group_prompt_context()` implementation remains
6. Director Goals still influences model selection, hidden planning policy, and speaker context
7. all existing behavior and fallback tests pass
8. new provider lifecycle and failure tests pass
9. CI passes
10. no schema/transaction refactor is mixed into the PR

## 18. Alternatives rejected

### Whole-function Director provider

Rejected because it would preserve a duplicated Director execution path and merely replace monkey-patching with provider indirection.

### Generic ordered Director hook pipeline

Rejected because there is only one policy consumer and conflict semantics are currently undefined.

### Immediate GroupService / dependency-injection rewrite

Rejected for this PR because it would combine interface extraction with a broad application-architecture rewrite.

The selected design deliberately creates a boundary that can later be injected into a service without requiring that service conversion now.

## 19. Follow-on architecture sequence

After Phase 1 implementation, the recommended sequence is:

### Phase 2 — Versioned schema migrations

Move feature schema creation out of getters and runtime feature paths.

### Phase 3 — Repository and transaction ownership

Make reads side-effect free and move commit responsibility to explicit application/service boundaries.

### Phase 4 — Composition root

Introduce `BridgeConfig` / `BridgeServices` and begin explicit dependency construction.

### Phase 5+ — Service extraction

Incrementally extract Group Director, Memory, Persona/Sync, and Job services.

### Later — Replace safety overrides with adapters/decorators

Convert state-integrity, sync-safety, and scheduler hardening from late function replacement into explicit layers.

### Final phase — Retire compatibility runtime execution

Remove `exec()`-based shared namespace loading only after ordinary imports and explicit dependencies make it unnecessary.

The objective is not to rewrite the runtime directly. The objective is to make the runtime loader progressively irrelevant.

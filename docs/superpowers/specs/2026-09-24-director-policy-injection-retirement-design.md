# Director Policy Injection Retirement Design

Date: 2026-09-24
Status: approved for implementation
Base: `main` at `f59d8377917f556ab740b595865d9724016f733c`

## Purpose

Finish the final transitional step from the original Director Policy boundary design.

Current state:

- Group Director execution is owned by `GroupDirectorService`;
- the service receives a callable named `director_customization`;
- that callable is currently `extension_registry.get_director_customization`;
- Director Goals registers its policy into a global single-provider registry slot at
  application startup;
- command-route and event-hook registries remain legitimate multi-handler extension
  points.

The original Phase 1 design explicitly described the Director customization registry
as a compatibility bridge and intended the same semantic interface to become an
injected policy dependency once explicit composition existed.

That composition root now exists. The single-provider registry slot should be retired.

## Chosen architecture

Use direct constructor injection:

```text
main composition root
    |
    v
GroupDirectorService(director_policy=director_goal_policy)
    |
    v
DirectorCustomization | None
```

The Group Director service remains the sole execution owner.

Director Goals remains a bounded policy provider only.

## GroupDirectorService ownership

Move the policy value type into the bridge-independent service module:

```python
@dataclass(frozen=True)
class DirectorCustomization:
    model: str | None = None
    hidden_instructions: str = ""
    max_tokens: int | None = None
    speaker_context: str = ""

DirectorPolicy = Callable[
    [sqlite3.Connection, str, dict[str, str]],
    DirectorCustomization | None,
]
```

Rename the injected field:

```python
director_customization
```

to:

```python
director_policy
```

The service calls `director_policy(...)` in the same two places where it currently
calls `director_customization(...)`.

No execution, fallback, prompt construction, parsing, model selection, or token-clamp
behavior changes.

The service remains independent of all `bridge.*` imports and stays inside the
static-analysis isolated service surface.

## Director Goals policy provider

`director_goals.py` imports `DirectorCustomization` from
`group_director_service.py`.

Rename the private provider:

```python
_director_goal_customization(...)
```

to the explicit public policy callable:

```python
director_goal_policy(...)
```

Behavior is unchanged:

- resolve the Director task model;
- read the session goal;
- return max_tokens=220;
- include hidden instructions and speaker context only when a goal exists.

The function never receives model-generation or fallback ownership.

## Startup composition

`main.py` imports `director_goal_policy` directly and injects it into
`GroupDirectorService`:

```python
group_director = GroupDirectorService(
    ...,
    director_policy=director_goal_policy,
    ...
)
```

`main.py` no longer imports any Director customization getter from
`extension_registry`.

This is ordinary explicit dependency injection.

## Extension registry cleanup

Remove only the single-provider Director slot:

- `DirectorCustomization`
- `DirectorCustomizationProvider`
- `_DIRECTOR_CUSTOMIZATION_PROVIDER`
- `register_director_customization_provider`
- `get_director_customization`
- `director_customization` snapshot entry
- reset logic specific to that slot

Keep the true extension mechanisms:

- command routes;
- post-retain hooks;
- summary-context hooks;
- summary-clear hooks.

Those are multi-handler/event semantics and remain valid extension-registry use.

## Director Goal extension registration

`register_director_goal_extensions()` registers only its command route.

It no longer:

- checks the Director customization snapshot;
- registers a policy provider.

The Director Goal policy is composed directly by startup instead.

Repeated extension initialization remains deterministic for command/event registries.

## Type and static-analysis impact

The stabilized service/port static surface remains bridge-independent.

`GroupDirectorService` gains stronger typing because the injected policy return type
is no longer `Any`.

The static-analysis CI job must remain green:

- zero import cycles;
- zero reciprocal pairs;
- Ruff pass;
- mypy pass on the isolated service/port surface.

## Tests and ownership migration

Update tests to use canonical ownership:

- import `DirectorCustomization` from `group_director_service`;
- inject direct test policy callables into GroupDirectorService;
- Director Goals tests use `director_goal_policy` directly;
- extension-registry tests remove single-provider Director tests;
- extension-registry snapshots no longer contain `director_customization`;
- explicit application composition tests expect only command/event registrations;
- startup composition test proves exact `director_goal_policy` injection.

No compatibility re-export remains in `extension_registry`.

## Behavior invariants

Preserve:

- forced-speaker short circuit;
- Director Goal hidden objective behavior;
- task-model resolution;
- max token override;
- hidden policy prompt content;
- speaker-context addition;
- invalid customization-field fallback semantics where still applicable through
  custom injected test policies;
- generation-failure/invalid-output round-robin fallback;
- Group Director default behavior when a policy returns `None`;
- `/group goal` command registration and dispatch.

## Non-goals

- no command-route registry redesign;
- no event-hook registry redesign;
- no GroupDirector prompt-quality changes;
- no model-provider changes;
- no transaction changes;
- no streaming continuation fix;
- no repo-wide Ruff cleanup.

## Acceptance

1. `extension_registry.py` contains no Director single-provider slot/type/getter.
2. `GroupDirectorService` owns typed Director customization/policy definitions.
3. GroupDirectorService field is explicit `director_policy`, not registry-oriented.
4. `director_goals.director_goal_policy` is directly injectable and behaviorally
   equivalent to the prior provider.
5. `main` injects that exact policy directly.
6. Director Goal extension registration contains only command-route registration.
7. command/event extension behavior remains green.
8. static-analysis policy remains green with zero cycles/reciprocal pairs.
9. full exact-head local and GitHub CI pass.
10. merge and verify actual main.

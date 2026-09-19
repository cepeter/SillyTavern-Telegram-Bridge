# Phase 1 DirectorPolicy Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the final Director Goals runtime overrides while preserving one canonical Group Director execution path, existing model-routing semantics, hidden-goal behavior, and all Director fallback behavior.

**Architecture:** Add one transitional single-provider Director customization slot to `bridge.extension_registry`. `bridge/groups.py` remains the sole owner of Director execution and consumes bounded policy data; `bridge/director_goals.py` registers a provider that returns model selection, hidden instructions, a 220-token override, and speaker context. The runtime-loader allowlist entry for Director Goals is then removed.

**Tech Stack:** Python 3.11, stdlib `dataclasses`/`logging`/`unittest`, SQLite, existing `bridge.runtime` compatibility facade, pytest/unittest CI.

**Spec:** `docs/superpowers/specs/2026-09-19-director-policy-boundary-design.md`

## Global Constraints

- Core services own execution; policies provide bounded customization only.
- Do not add new `_ORIGINAL_*` capture chains.
- Do not add new public-callable runtime overrides.
- Keep exactly one canonical `group_director_plan()` execution implementation.
- Keep exactly one canonical `group_prompt_context()` implementation.
- Preserve Director model routing: Director task model -> utility task model -> session main/default.
- Preserve forced-speaker short-circuit behavior.
- Preserve invalid-output and generation-exception round-robin fallback behavior.
- Preserve hidden-goal behavior without writing the goal into transcript messages.
- Director Goals may customize only `model`, `hidden_instructions`, `max_tokens`, and `speaker_context`.
- Preserve the Director Goals token budget at `max_tokens=220`; ordinary Director mode remains `max_tokens=180`.
- Provider execution failures must be logged and fall back to ordinary Director behavior.
- Duplicate Director provider registration must fail fast.
- Do not mix schema migration, transaction ownership, ORM, composition-root, or service-extraction work into this phase.
- Keep runtime reload behavior deterministic.
- All existing repository CI must pass before merge.

## Review Focus

- **No provider registered:** ordinary Director mode must still use the session/default model and `max_tokens=180`.
- **Provider raises:** planning and speaker-context generation must continue with ordinary Director behavior rather than failing the user request.
- **No goal but Director Goals provider present:** task-model routing and the `220` token budget must still apply, while no hidden-goal text is injected.
- **Forced speaker configured:** the planner must return immediately without calling the provider or model.
- **Runtime reload / duplicate registration:** repeated full runtime loads must restore exactly one `director_goals` provider, while an actual second registration in one registry lifetime is rejected.

---

## File Structure

### Modify: `bridge/extension_registry.py`

Responsibility after this phase:

- existing multi-listener command/memory/summary extension registries
- one single-provider Director customization slot
- provider reset, duplicate rejection, failure isolation, and diagnostics
- typed `DirectorCustomization` value object

It must not execute Group Director logic.

### Modify: `bridge/groups.py`

Responsibility after this phase:

- the only `group_director_plan()` implementation
- core Director validation, transcript collection, prompt invariants, generation, parsing, and fallback
- optional consumption of bounded Director customization
- the only `group_prompt_context()` implementation

### Modify: `bridge/director_goals.py`

Responsibility after this phase:

- Director Goal persistence and normalization
- `/group goal` command route
- Director Goal policy provider
- no model invocation
- no Director decision parsing
- no duplicate Group Director execution path
- no captured original Group functions

### Modify: `bridge/runtime_loader.py`

Responsibility after this phase:

- keep `director_goals.py` in the safety stage
- remove its public-callable override allowlist entry

### Modify: `tests/test_extension_registry.py`

Responsibility:

- unit tests for the single Director provider slot
- isolate registry globals so tests never clear the live runtime registry

### Modify: `tests/test_group_director.py`

Responsibility:

- core Director behavior with and without customization
- provider failure fallback
- forced-speaker bypass
- generation failure/invalid-output fallback

### Modify: `tests/test_director_goals.py`

Responsibility:

- Director Goal policy behavior
- task-model fallback chain
- hidden prompt/context behavior
- transcript non-persistence

### Modify: `tests/test_runtime_loader.py`

Responsibility:

- assert `director_goals.py` has zero overrides
- assert runtime snapshot contains exactly one Director provider
- ensure repeated-load registry reset includes the single-provider slot

---

### Task 1: Add the single Director customization provider contract

**Files:**
- Modify: `bridge/extension_registry.py:1-122`
- Modify: `tests/test_extension_registry.py:1-75`

**Interfaces:**
- Produces:
  - `DirectorCustomization`
  - `DirectorCustomizationProvider`
  - `register_director_customization_provider(name: str, provider: DirectorCustomizationProvider) -> None`
  - `get_director_customization(db: Any, chat_id: str, session: dict[str, str]) -> DirectorCustomization | None`
  - `extension_registry_snapshot()["director_customization"] -> tuple[str, ...]`
- Consumes: nothing from later tasks.

- [ ] **Step 1: Write failing provider registration and retrieval tests**

Add to `tests/test_extension_registry.py`:

```python
def test_director_customization_provider_registers_and_returns_value(self):
    with patch.object(registry, "_DIRECTOR_CUSTOMIZATION_PROVIDER", None):
        expected = registry.DirectorCustomization(
            model="utility::director",
            hidden_instructions="Hidden objective.",
            max_tokens=220,
            speaker_context="Hidden scene objective: Hidden objective.",
        )

        registry.register_director_customization_provider(
            "director_goals",
            lambda db, chat_id, session: expected,
        )

        self.assertEqual(
            registry.get_director_customization(
                None,
                "chat",
                {"session_id": "session"},
            ),
            expected,
        )
        self.assertEqual(
            registry.extension_registry_snapshot()["director_customization"],
            ("director_goals",),
        )
```

- [ ] **Step 2: Write failing duplicate-registration test**

```python
def test_director_customization_provider_rejects_duplicate_registration(self):
    with patch.object(registry, "_DIRECTOR_CUSTOMIZATION_PROVIDER", None):
        registry.register_director_customization_provider(
            "first",
            lambda db, chat_id, session: None,
        )
        with self.assertRaisesRegex(RuntimeError, "already registered"):
            registry.register_director_customization_provider(
                "second",
                lambda db, chat_id, session: None,
            )
```

- [ ] **Step 3: Write failing provider-exception isolation test**

```python
def test_director_customization_provider_failure_returns_none(self):
    def fail(_db, _chat_id, _session):
        raise RuntimeError("boom")

    with patch.object(registry, "_DIRECTOR_CUSTOMIZATION_PROVIDER", None):
        registry.register_director_customization_provider("broken", fail)
        with self.assertLogs(level="ERROR") as logs:
            result = registry.get_director_customization(
                None,
                "chat",
                {"session_id": "session"},
            )

        self.assertIsNone(result)
        self.assertTrue(
            any("Director customization provider failed: broken" in line for line in logs.output)
        )
```

- [ ] **Step 4: Write failing reset test without mutating live runtime state**

Patch every registry global, because `reset_extension_registry()` clears them all:

```python
def test_reset_extension_registry_clears_director_provider(self):
    with (
        patch.dict(registry._COMMAND_ROUTES, clear=True),
        patch.dict(registry._POST_RETAIN_HOOKS, clear=True),
        patch.dict(registry._SUMMARY_CONTEXT_HOOKS, clear=True),
        patch.dict(registry._SUMMARY_CLEAR_HOOKS, clear=True),
        patch.object(registry, "_DIRECTOR_CUSTOMIZATION_PROVIDER", None),
    ):
        registry.register_director_customization_provider(
            "director_goals",
            lambda db, chat_id, session: None,
        )
        registry.reset_extension_registry()
        self.assertEqual(
            registry.extension_registry_snapshot()["director_customization"],
            (),
        )
```

- [ ] **Step 5: Run the new registry tests and verify they fail**

Run:

```bash
python -m unittest   tests.test_extension_registry.ExtensionRegistryTests.test_director_customization_provider_registers_and_returns_value   tests.test_extension_registry.ExtensionRegistryTests.test_director_customization_provider_rejects_duplicate_registration   tests.test_extension_registry.ExtensionRegistryTests.test_director_customization_provider_failure_returns_none   tests.test_extension_registry.ExtensionRegistryTests.test_reset_extension_registry_clears_director_provider -v
```

Expected: FAIL because the Director customization types/functions do not exist.

- [ ] **Step 6: Implement the minimal provider contract**

In `bridge/extension_registry.py`, add `dataclass` and the types near the existing hook aliases:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class DirectorCustomization:
    model: str | None = None
    hidden_instructions: str = ""
    max_tokens: int | None = None
    speaker_context: str = ""

DirectorCustomizationProvider = Callable[
    [Any, str, dict[str, str]],
    DirectorCustomization | None,
]

_DIRECTOR_CUSTOMIZATION_PROVIDER: tuple[str, DirectorCustomizationProvider] | None = None
```

Extend reset:

```python
def reset_extension_registry() -> None:
    global _DIRECTOR_CUSTOMIZATION_PROVIDER

    _COMMAND_ROUTES.clear()
    _POST_RETAIN_HOOKS.clear()
    _SUMMARY_CONTEXT_HOOKS.clear()
    _SUMMARY_CLEAR_HOOKS.clear()
    _DIRECTOR_CUSTOMIZATION_PROVIDER = None
```

Add registration and retrieval:

```python
def register_director_customization_provider(
    name: str,
    provider: DirectorCustomizationProvider,
) -> None:
    global _DIRECTOR_CUSTOMIZATION_PROVIDER

    key = str(name or "").strip()
    if not key:
        raise ValueError("extension name must not be empty")
    if not callable(provider):
        raise TypeError(f"extension {key} must be callable")
    if _DIRECTOR_CUSTOMIZATION_PROVIDER is not None:
        existing_name, _existing_provider = _DIRECTOR_CUSTOMIZATION_PROVIDER
        raise RuntimeError(
            f"director customization provider already registered: {existing_name}"
        )
    _DIRECTOR_CUSTOMIZATION_PROVIDER = (key, provider)


def get_director_customization(
    db: Any,
    chat_id: str,
    session: dict[str, str],
) -> DirectorCustomization | None:
    registered = _DIRECTOR_CUSTOMIZATION_PROVIDER
    if registered is None:
        return None

    name, provider = registered
    try:
        result = provider(db, chat_id, session)
    except Exception:
        logging.exception("Director customization provider failed: %s", name)
        return None

    if result is not None and not isinstance(result, DirectorCustomization):
        logging.error(
            "Director customization provider returned invalid value: %s",
            name,
        )
        return None
    return result
```

Extend snapshot:

```python
"director_customization": (
    (_DIRECTOR_CUSTOMIZATION_PROVIDER[0],)
    if _DIRECTOR_CUSTOMIZATION_PROVIDER is not None
    else ()
),
```

- [ ] **Step 7: Run the registry test file**

Run:

```bash
python -m unittest tests.test_extension_registry -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```bash
git add bridge/extension_registry.py tests/test_extension_registry.py
git commit -m "refactor: add Director customization provider"
```

---

### Task 2: Make Groups consume bounded Director customization

**Files:**
- Modify: `bridge/groups.py:325-399`
- Modify: `tests/test_group_director.py:8-119`

**Interfaces:**
- Consumes:
  - `get_director_customization(db, chat_id, session) -> DirectorCustomization | None`
  - `DirectorCustomization.model`
  - `DirectorCustomization.hidden_instructions`
  - `DirectorCustomization.max_tokens`
  - `DirectorCustomization.speaker_context`
- Produces:
  - canonical `group_director_plan()` with optional policy input
  - canonical `group_prompt_context()` with optional policy context

- [ ] **Step 1: Write a failing ordinary-Director/no-provider regression test**

Add a test that temporarily clears only the single provider slot and asserts the base model and token budget:

```python
def test_director_without_policy_uses_main_model_and_base_token_budget(self):
    old_safe = rt.safe_character_path
    old_fields = rt.card_fields_from_file
    old_generate = rt.generate_text
    calls = []

    rt.safe_character_path = lambda filename: Path(filename)
    rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

    def fake_generate(_key, model, messages, **kwargs):
        calls.append((model, messages, kwargs))
        return '{"speaker":"Alice","direction":"Hold the beat."}'

    rt.generate_text = fake_generate
    try:
        with patch.object(
            extension_registry,
            "_DIRECTOR_CUSTOMIZATION_PROVIDER",
            None,
        ):
            plan = rt.group_director_plan(
                self.db,
                "key",
                "chat|topic:1",
                self.session,
                "Continue.",
            )
    finally:
        rt.safe_character_path = old_safe
        rt.card_fields_from_file = old_fields
        rt.generate_text = old_generate

    self.assertEqual(plan[0], "alice.png")
    self.assertEqual(calls[0][0], "provider::main")
    self.assertEqual(calls[0][2]["settings"]["max_tokens"], 180)
```

Add imports:

```python
from unittest.mock import patch
import bridge.extension_registry as extension_registry
```

- [ ] **Step 2: Write a failing customization-consumption test**

```python
def test_director_applies_bounded_customization(self):
    old_safe = rt.safe_character_path
    old_fields = rt.card_fields_from_file
    old_generate = rt.generate_text
    calls = []

    rt.safe_character_path = lambda filename: Path(filename)
    rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

    def fake_generate(_key, model, messages, **kwargs):
        calls.append((model, messages, kwargs))
        return '{"speaker":"Bob","direction":"Notice the door."}'

    customization = extension_registry.DirectorCustomization(
        model="utility::director",
        hidden_instructions="Hidden scene objective: reveal the door slowly.",
        max_tokens=220,
        speaker_context="Hidden scene objective: reveal the door slowly.",
    )

    rt.generate_text = fake_generate
    try:
        with patch.object(
            extension_registry,
            "_DIRECTOR_CUSTOMIZATION_PROVIDER",
            ("test", lambda db, chat_id, session: customization),
        ):
            plan = rt.group_director_plan(
                self.db,
                "key",
                "chat|topic:1",
                self.session,
                "Look around.",
            )
    finally:
        rt.safe_character_path = old_safe
        rt.card_fields_from_file = old_fields
        rt.generate_text = old_generate

    self.assertEqual(plan[0], "bob.png")
    self.assertEqual(calls[0][0], "utility::director")
    self.assertEqual(calls[0][2]["settings"]["max_tokens"], 220)
    joined = "\n".join(str(message["content"]) for message in calls[0][1])
    self.assertIn("reveal the door slowly", joined)
```

- [ ] **Step 3: Write a failing provider-failure fallback test**

The provider failure must be isolated by `get_director_customization()`, so core generation still executes:

```python
def test_director_policy_failure_uses_ordinary_director_behavior(self):
    old_safe = rt.safe_character_path
    old_fields = rt.card_fields_from_file
    old_generate = rt.generate_text
    calls = []

    rt.safe_character_path = lambda filename: Path(filename)
    rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

    def fail_policy(_db, _chat_id, _session):
        raise RuntimeError("policy failed")

    def fake_generate(_key, model, messages, **kwargs):
        calls.append((model, messages, kwargs))
        return '{"speaker":"Alice","direction":"Continue normally."}'

    rt.generate_text = fake_generate
    try:
        with patch.object(
            extension_registry,
            "_DIRECTOR_CUSTOMIZATION_PROVIDER",
            ("broken", fail_policy),
        ):
            plan = rt.group_director_plan(
                self.db,
                "key",
                "chat|topic:1",
                self.session,
                "Continue.",
            )
    finally:
        rt.safe_character_path = old_safe
        rt.card_fields_from_file = old_fields
        rt.generate_text = old_generate

    self.assertEqual(plan[0], "alice.png")
    self.assertEqual(calls[0][0], "provider::main")
    self.assertEqual(calls[0][2]["settings"]["max_tokens"], 180)
```

- [ ] **Step 4: Write a failing forced-speaker bypass test**

The test must prove both policy and model generation are skipped:

```python
def test_forced_speaker_bypasses_policy_and_generation(self):
    state = rt.group_state(self.db, "chat|topic:1", self.session["session_id"])
    state["forced_speaker"] = "bob.png"
    rt.save_group_state(
        self.db,
        "chat|topic:1",
        self.session["session_id"],
        state,
    )

    old_safe = rt.safe_character_path
    old_generate = rt.generate_text
    policy_calls = []
    generation_calls = []
    rt.safe_character_path = lambda filename: Path(filename)

    def policy(_db, _chat_id, _session):
        policy_calls.append(True)
        return None

    def generate(*_args, **_kwargs):
        generation_calls.append(True)
        raise AssertionError("generation should not run")

    rt.generate_text = generate
    try:
        with patch.object(
            extension_registry,
            "_DIRECTOR_CUSTOMIZATION_PROVIDER",
            ("test", policy),
        ):
            plan = rt.group_director_plan(
                self.db,
                "key",
                "chat|topic:1",
                self.session,
                "Continue.",
            )
    finally:
        rt.safe_character_path = old_safe
        rt.generate_text = old_generate

    self.assertEqual(plan[0], "bob.png")
    self.assertEqual(policy_calls, [])
    self.assertEqual(generation_calls, [])
```

- [ ] **Step 5: Write a failing speaker-context customization test**

```python
def test_group_prompt_context_appends_director_policy_context(self):
    old_safe = rt.safe_character_path
    old_fields = rt.card_fields_from_file
    rt.safe_character_path = lambda filename: Path(filename)
    rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

    customization = extension_registry.DirectorCustomization(
        speaker_context="Hidden scene objective: keep the letter unopened."
    )
    try:
        with patch.object(
            extension_registry,
            "_DIRECTOR_CUSTOMIZATION_PROVIDER",
            ("test", lambda db, chat_id, session: customization),
        ):
            context = rt.group_prompt_context(
                self.db,
                "chat|topic:1",
                self.session,
                "alice.png",
                "Keep the pace measured.",
            )
    finally:
        rt.safe_character_path = old_safe
        rt.card_fields_from_file = old_fields

    self.assertIn("Invisible director guidance: Keep the pace measured.", context)
    self.assertIn("keep the letter unopened", context)
```

- [ ] **Step 6: Run the new Group Director tests and verify the customization-specific tests fail**

Run:

```bash
python -m unittest tests.test_group_director -v
```

Expected: existing tests may pass, but the new customization-specific assertions FAIL because `groups.py` does not yet consume the provider.

- [ ] **Step 7: Add the Director customization lookup import to `groups.py`**

At the top of `bridge/groups.py`:

```python
from bridge.extension_registry import get_director_customization as _get_director_customization
```

Use the underscored alias so the compatibility loader does not treat the imported helper as a public callable.

- [ ] **Step 8: Integrate customization into the canonical planner**

In `group_director_plan()`, keep validation and forced-speaker handling first.

After transcript collection, establish ordinary defaults:

```python
customization = _get_director_customization(db, chat_id, session)
model = str(session.get("model_id") or DEFAULT_MODEL)
hidden_instructions = ""
max_tokens = 180

if customization is not None:
    if customization.model:
        model = customization.model
    hidden_instructions = str(customization.hidden_instructions or "").strip()
    if customization.max_tokens is not None:
        max_tokens = int(customization.max_tokens)
```

Build the optional policy block without allowing it to replace core invariants:

```python
policy_block = (
    "\nHidden Director policy:\n" + hidden_instructions
    if hidden_instructions
    else ""
)
```

Keep the existing core system message unchanged except for a clear statement that hidden policy is subordinate to continuity if the current spec text requires it. Append `policy_block` in the user request before the transcript:

```python
"Allowed speakers: " + ", ".join(labels) +
policy_block +
"\nRecent transcript:\n" + (transcript or "(empty)") +
"\nLatest user turn:\n" + str(user_text)[:4000]
```

Keep core settings owned by Groups:

```python
settings = get_generation_settings(db, chat_id, session["session_id"])
settings.update({
    "temperature": 0.1,
    "max_tokens": max_tokens,
    "reasoning_budget": 0,
    "stop_sequences": "",
})
```

Call:

```python
raw = generate_text(
    api_key,
    model,
    director_messages,
    session_id=f"group-director:{chat_id}:{session['session_id']}",
    settings=settings,
    force_non_stream=True,
)
```

Do not change the existing parse/fallback block.

- [ ] **Step 9: Integrate speaker-context customization into the canonical context builder**

Keep the autonomous early return unchanged.

After the existing Director pacing guidance, add:

```python
if state.get("mode") == "director":
    customization = _get_director_customization(db, chat_id, session)
    if customization is not None and customization.speaker_context:
        base += "\n" + str(customization.speaker_context).strip()
```

Because provider exceptions are isolated in the registry helper, a failing provider leaves `base` unchanged.

- [ ] **Step 10: Run Group Director tests**

Run:

```bash
python -m unittest tests.test_group_director -v
```

Expected: PASS.

- [ ] **Step 11: Commit Task 2**

```bash
git add bridge/groups.py tests/test_group_director.py
git commit -m "refactor: consume Director policy in group core"
```

---

### Task 3: Convert Director Goals from execution override to policy provider

**Files:**
- Modify: `bridge/director_goals.py:8-238`
- Modify: `tests/test_director_goals.py:8-149`

**Interfaces:**
- Consumes:
  - `DirectorCustomization`
  - `register_director_customization_provider()`
  - existing `task_model_for_session(db, chat_id, session, "director")`
  - existing goal persistence helpers
- Produces:
  - `_director_goal_customization(db, chat_id, session) -> DirectorCustomization`
  - one `director_goals` provider registration
  - no `group_director_plan()` override
  - no `group_prompt_context()` override

- [ ] **Step 1: Add model-fallback tests before deleting the override**

Keep the existing utility-model test, then add explicit fallback coverage.

Utility fallback when no Director-specific task model is configured:

```python
def test_director_policy_falls_back_to_utility_model(self):
    rt.set_task_model(
        self.db,
        self.chat_id,
        self.session["session_id"],
        "utility::fallback",
        task="utility",
    )
    rt.set_task_model(
        self.db,
        self.chat_id,
        self.session["session_id"],
        "",
        task="director",
    )

    old_safe = rt.safe_character_path
    old_fields = rt.card_fields_from_file
    old_generate = rt.generate_text
    calls = []
    rt.safe_character_path = lambda filename: Path(filename)
    rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

    def fake_generate(_key, model, messages, **kwargs):
        calls.append((model, messages, kwargs))
        return '{"speaker":"Alice","direction":"Continue."}'

    rt.generate_text = fake_generate
    try:
        rt.group_director_plan(
            self.db,
            "",
            self.chat_id,
            self.session,
            "Continue.",
        )
    finally:
        rt.safe_character_path = old_safe
        rt.card_fields_from_file = old_fields
        rt.generate_text = old_generate

    self.assertEqual(calls[0][0], "utility::fallback")
```

Main-model fallback when neither Director nor utility task model is configured:

```python
def test_director_policy_falls_back_to_main_model(self):
    rt.set_task_model(
        self.db,
        self.chat_id,
        self.session["session_id"],
        "",
        task="director",
    )
    rt.set_task_model(
        self.db,
        self.chat_id,
        self.session["session_id"],
        "",
        task="utility",
    )

    old_safe = rt.safe_character_path
    old_fields = rt.card_fields_from_file
    old_generate = rt.generate_text
    calls = []
    rt.safe_character_path = lambda filename: Path(filename)
    rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

    def fake_generate(_key, model, messages, **kwargs):
        calls.append((model, messages, kwargs))
        return '{"speaker":"Alice","direction":"Continue."}'

    rt.generate_text = fake_generate
    try:
        rt.group_director_plan(
            self.db,
            "",
            self.chat_id,
            self.session,
            "Continue.",
        )
    finally:
        rt.safe_character_path = old_safe
        rt.card_fields_from_file = old_fields
        rt.generate_text = old_generate

    self.assertEqual(calls[0][0], "primary::main")
```

- [ ] **Step 2: Add a no-goal policy-behavior test**

This protects the subtle current behavior that task-model routing and the 220-token budget apply even without a configured hidden goal:

```python
def test_director_policy_applies_model_and_token_budget_without_goal(self):
    old_safe = rt.safe_character_path
    old_fields = rt.card_fields_from_file
    old_generate = rt.generate_text
    calls = []
    rt.safe_character_path = lambda filename: Path(filename)
    rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

    def fake_generate(_key, model, messages, **kwargs):
        calls.append((model, messages, kwargs))
        return '{"speaker":"Alice","direction":"Continue."}'

    rt.generate_text = fake_generate
    try:
        rt.group_director_plan(
            self.db,
            "",
            self.chat_id,
            self.session,
            "Continue.",
        )
    finally:
        rt.safe_character_path = old_safe
        rt.card_fields_from_file = old_fields
        rt.generate_text = old_generate

    joined = "\n".join(str(message["content"]) for message in calls[0][1])
    self.assertEqual(calls[0][0], "utility::director")
    self.assertEqual(calls[0][2]["settings"]["max_tokens"], 220)
    self.assertNotIn("Hidden scene objective:", joined)
```

- [ ] **Step 3: Add transcript non-persistence assertion to the existing hidden-goal planner test**

Around the `group_director_plan()` call:

```python
before = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
plan = rt.group_director_plan(...)
after = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

self.assertEqual(before, after)
```

- [ ] **Step 4: Run Director Goals tests before refactoring**

Run:

```bash
python -m unittest tests.test_director_goals -v
```

Expected: PASS on the current override implementation. These are characterization tests that protect behavior before deletion.

- [ ] **Step 5: Replace captured original imports with policy-registry imports**

At the top of `bridge/director_goals.py`, replace the current single registry import with:

```python
from bridge.extension_registry import (
    DirectorCustomization as _DirectorCustomization,
    register_command_route as _register_command_route,
    register_director_customization_provider as _register_director_customization_provider,
)
```

Delete:

```python
_ORIGINAL_GROUP_DIRECTOR_PLAN_GOALS = group_director_plan
_ORIGINAL_GROUP_PROMPT_CONTEXT_GOALS = group_prompt_context
```

- [ ] **Step 6: Delete the duplicated `group_director_plan()` implementation**

Remove the entire override at the current `bridge/director_goals.py:75-154`.

There must be no replacement whole-function planner in this module.

- [ ] **Step 7: Delete the duplicated `group_prompt_context()` wrapper**

Remove the entire override at the current `bridge/director_goals.py:157-184`.

There must be no replacement whole-function context builder in this module.

- [ ] **Step 8: Add the bounded Director Goal customization provider**

Add:

```python
def _director_goal_customization(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
) -> _DirectorCustomization:
    goal = get_director_goal(db, chat_id, session["session_id"])
    model = task_model_for_session(db, chat_id, session, "director")

    if not goal:
        return _DirectorCustomization(
            model=model,
            max_tokens=220,
        )

    hidden_instructions = (
        "Hidden scene objective: " + goal +
        " Advance this objective naturally when appropriate. "
        "Do not force completion, do not contradict established continuity, "
        "and never mention that an objective exists."
    )
    speaker_context = (
        "Hidden scene objective: " + goal +
        " Advance it only when natural for the current speaker and established scene. "
        "Never mention, quote, or expose this objective."
    )
    return _DirectorCustomization(
        model=model,
        hidden_instructions=hidden_instructions,
        max_tokens=220,
        speaker_context=speaker_context,
    )
```

Keep the wording semantically equivalent to the existing Director Goals prompts; do not broaden the policy surface.

- [ ] **Step 9: Register the policy beside the existing command route**

At module bottom:

```python
_register_director_customization_provider(
    "director_goals",
    _director_goal_customization,
)
_register_command_route("director_goals", _director_goal_command_route)
```

- [ ] **Step 10: Run Director Goals and Group Director tests**

Run:

```bash
python -m unittest tests.test_director_goals tests.test_group_director -v
```

Expected: PASS.

- [ ] **Step 11: Confirm no duplicate Director execution implementation remains**

Run:

```bash
grep -R "_ORIGINAL_GROUP_DIRECTOR_PLAN_GOALS\|_ORIGINAL_GROUP_PROMPT_CONTEXT_GOALS" -n bridge tests
grep -R "^def group_director_plan" -n bridge
grep -R "^def group_prompt_context" -n bridge
```

Expected:

- no `_ORIGINAL_GROUP_..._GOALS` matches
- exactly one `group_director_plan` definition, in `bridge/groups.py`
- exactly one `group_prompt_context` definition, in `bridge/groups.py`

- [ ] **Step 12: Commit Task 3**

```bash
git add bridge/director_goals.py tests/test_director_goals.py
git commit -m "refactor: make Director Goals a policy provider"
```

---

### Task 4: Remove the runtime override allowance and prove deterministic loading

**Files:**
- Modify: `bridge/runtime_loader.py:70-88`
- Modify: `tests/test_runtime_loader.py:12-82`

**Interfaces:**
- Consumes:
  - `extension_registry_snapshot()["director_customization"]`
  - runtime registration performed by `director_goals.py`
- Produces:
  - zero public-callable overrides for `director_goals.py`
  - deterministic repeated runtime provider registration

- [ ] **Step 1: Change the runtime-report expectation first**

In `test_runtime_exposes_structured_load_report`, change:

```python
self.assertEqual(
    by_module["director_goals.py"],
    ("group_director_plan", "group_prompt_context"),
)
```

to:

```python
self.assertEqual(by_module["director_goals.py"], ())
```

Add:

```python
self.assertEqual(
    extensions["director_customization"],
    ("director_goals",),
)
```

- [ ] **Step 2: Extend the repeated-load test to isolate and verify the single-provider slot**

Add `patch.object(extension_registry, "_DIRECTOR_CUSTOMIZATION_PROVIDER", None)` to the existing context manager.

Make one synthetic module register both a command route and a Director provider:

```python
(root / "one.py").write_text(
    "from bridge.extension_registry import "
    "register_command_route, register_director_customization_provider\n"
    "def route_one(*args, **kwargs):\n    return False\n"
    "def director_policy(db, chat_id, session):\n    return None\n"
    'register_command_route("first", route_one)\n'
    'register_director_customization_provider("policy", director_policy)\n',
    encoding="utf-8",
)
```

After both loads:

```python
self.assertEqual(first, second)
self.assertEqual(first["command_routes"], ("first", "second"))
self.assertEqual(first["director_customization"], ("policy",))
```

- [ ] **Step 3: Run runtime-loader tests and verify the override assertion fails**

Run:

```bash
python -m unittest tests.test_runtime_loader -v
```

Expected: FAIL because `director_goals.py` is still allowlisted as an override module until the loader is updated.

- [ ] **Step 4: Remove the Director Goals override allowlist entry**

In `bridge/runtime_loader.py`, delete:

```python
("director_goals.py", ("group_director_plan", "group_prompt_context")),
```

Keep `director_goals.py` in the `safety_overrides` stage module list because registration order is still part of the compatibility runtime during this phase.

- [ ] **Step 5: Run focused regression tests**

Run:

```bash
python -m unittest   tests.test_extension_registry   tests.test_runtime_loader   tests.test_group_director   tests.test_director_goals -v
```

Expected: PASS.

- [ ] **Step 6: Run syntax compilation**

Run:

```bash
python -m compileall bridge tests
```

Expected: exit 0.

- [ ] **Step 7: Run the complete unittest suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 8: Run pytest exactly as CI does**

Run:

```bash
pytest
```

Expected: all tests PASS.

- [ ] **Step 9: Run dependency consistency/audit commands used by CI where available**

Run the repository's CI-equivalent commands from `.github/workflows/ci.yml`, including:

```bash
python -m pip check
pip-audit
```

Expected: exit 0, subject to the repository's existing locked dependency environment.

- [ ] **Step 10: Verify architecture invariants**

Run:

```bash
grep -R "_ORIGINAL_GROUP_DIRECTOR_PLAN_GOALS\|_ORIGINAL_GROUP_PROMPT_CONTEXT_GOALS" -n bridge tests
grep -R '^def group_director_plan' -n bridge
grep -R '^def group_prompt_context' -n bridge
```

Expected:

- no captured Director Goal originals
- one planner implementation
- one prompt-context implementation

Also confirm the runtime report test proves `director_goals.py == ()`.

- [ ] **Step 11: Commit Task 4**

```bash
git add bridge/runtime_loader.py tests/test_runtime_loader.py
git commit -m "test: enforce Director policy runtime boundary"
```

---

## Final Verification

- [ ] **Step 1: Compare implementation against the Phase 1 spec**

Confirm every acceptance criterion in `docs/superpowers/specs/2026-09-19-director-policy-boundary-design.md` is represented by code or a test.

- [ ] **Step 2: Verify the diff is Phase 1 only**

Run:

```bash
git diff main...HEAD --   bridge/extension_registry.py   bridge/groups.py   bridge/director_goals.py   bridge/runtime_loader.py   tests/test_extension_registry.py   tests/test_group_director.py   tests/test_director_goals.py   tests/test_runtime_loader.py
```

Then:

```bash
git diff --name-only main...HEAD
```

Expected: only the Phase 1 implementation/test files above, plus this implementation-plan document if it is carried on the implementation branch.

- [ ] **Step 3: Run final full verification**

```bash
python -m compileall bridge tests
python -m unittest discover -s tests -v
pytest
python -m pip check
pip-audit
```

Expected: all commands exit 0.

- [ ] **Step 4: Request code review before opening/merging the implementation PR**

Use the Superpowers `requesting-code-review` skill against the completed branch and specifically review:

- whether `groups.py` is now the sole execution owner
- whether the provider can accidentally replace core prompt invariants
- whether provider failure semantics match the spec
- whether model fallback behavior is identical
- whether registry lifecycle remains deterministic
- whether any unrelated Phase 2+ work leaked into the branch

- [ ] **Step 5: Open the Phase 1 implementation PR only after review findings are addressed**

The PR description should link both:

- `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`
- `docs/superpowers/specs/2026-09-19-director-policy-boundary-design.md`

and state explicitly that this is Phase 1 of the master migration roadmap.

# PR #62 Native Application Service Composition Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Memory, Persona, Sync, Group Director, and Job services mandatory in the production service graph and delete the remaining application-service compatibility constructors, resolvers, and optional routing fallbacks.

**Architecture:** Keep `bridge.main` as the composition root for this PR. High-level worker/router boundaries receive the required `BridgeServices`; leaf helpers receive only the concrete service they use. No global service registry, no new compatibility wrapper, no runtime-context retirement, and no `main.py` decomposition are included.

**Tech Stack:** Python 3.11, dataclasses, sqlite3, unittest, pytest, existing bridge application-service modules, GitHub Actions CI.

**Spec:** `docs/superpowers/specs/2026-09-22-pr62-native-application-service-composition-retirement-design.md`

## Global Constraints

- The repository is preproduction; backward compatibility with the retired compatibility-call style is not required.
- `BridgeServices` must require `JobService`, `MemoryService`, `PersonaService`, `SyncService`, and `GroupDirectorService`.
- `build_bridge_services()` must require all five application services.
- Production workflows must never reconstruct a missing application service.
- Do not introduce `CURRENT_SERVICES`, `get_services()`, setters, registries, or any equivalent service locator.
- Worker/router/controller boundaries may receive `BridgeServices`; lower-level helpers receive only the specific application service they need.
- Do not remove or redesign `bridge.runtime_context` in this PR.
- Do not split `bridge.main` in this PR.
- Preserve Hindsight, Persona integrity/storage, Live Sync conflict/safety, Group Director fallback, durable-job, transaction, operation-phase, and Telegram behavior.
- Historical design specs remain historical records and are not rewritten.
- Use TDD: each compatibility-retirement slice begins with a failing guard or integration test, then the minimum production change, then focused verification.
- Keep commits small enough that each task can be reviewed independently.

## Review Focus

- **A caller omits one application service while constructing `BridgeServices`:** construction must fail immediately rather than create a partial service graph. Task 1 pins this with dataclass/signature tests.
- **A message or callback route is called without `services`:** the Python API must reject the call instead of silently choosing direct backend helpers. Tasks 2, 3, 5, and 6 pin required routing signatures.
- **A test supplies a fake service whose backend raises an expected operational error:** the existing service-level error/fallback semantics must remain unchanged. Tasks 2, 4, and 5 retain focused behavior tests.
- **A Sync worker starts with the injected `SyncService`:** every poll cycle must use that exact service and must not reconstruct one after an exception/reconnect. Task 4 adds an identity-sensitive worker test.
- **Group Director generation fails or returns an unknown speaker:** round-robin fallback must remain inside `GroupDirectorService` after all wrapper functions are removed. Task 5 preserves those service tests and migrates legacy wrapper tests to the canonical service.

---

## File Structure

The plan intentionally preserves the existing file layout.

### Production files

- `bridge/composition.py` — root service-graph contract; all application services become required.
- `bridge/main.py` — continues to construct all services; worker entry points continue forwarding the same instances.
- `bridge/memory.py` — delete Memory compatibility constructor/resolver only; memory backend/application behavior remains.
- `bridge/message_commands.py` — require `BridgeServices`, consume Memory/Persona/Group Director services directly.
- `bridge/telegram.py` — require explicit `MemoryService` for session deletion paths that purge memory.
- `bridge/media.py` / `bridge/help.py` — tighten only service parameters that still default to `None` in production workflows.
- `bridge/persona_sync.py` — delete Persona compatibility constructor/resolver.
- `bridge/input_flows.py` — require explicit `PersonaService` and `MemoryService` where used.
- `bridge/callbacks.py` — require `BridgeServices`, consume Persona/Sync services directly.
- `bridge/panel_callback_routes.py` — require explicit `SyncService` and Persona service for the relevant callbacks.
- `bridge/sync_api.py` — delete Sync compatibility constructor/resolver; require explicit service in worker lifecycle.
- `bridge/groups.py` — delete Group Director compatibility constructor and delegation wrappers.
- `bridge/command_routes.py` / `bridge/commands.py` — adjust service propagation only where currently optional.

### Test files

- `tests/test_native_runtime_retirement.py` — permanent architectural guards.
- `tests/test_composition.py` — required service-graph contract and worker injection.
- `tests/test_memory_service.py` — remove compatibility-service tests and verify explicit injection.
- `tests/test_persona_service.py` — remove compatibility-service tests and verify explicit injection.
- `tests/test_sync_service.py` — remove compatibility-service tests and verify explicit worker/callback injection.
- `tests/test_group_director.py` — migrate wrapper-level tests to canonical `GroupDirectorService`.
- `tests/test_group_director_service.py` — retain canonical behavior coverage and add any migrated parser assertion.
- Other integration/UI tests are edited only when a now-required service argument must be supplied.

---

### Task 1: Make the Root Application Service Graph Mandatory

**Files:**
- Modify: `tests/test_native_runtime_retirement.py`
- Modify: `tests/test_composition.py`
- Modify: `bridge/composition.py`

**Interfaces:**
- Consumes: existing `MemoryService`, `PersonaService`, `SyncService`, `GroupDirectorService`, `JobService`.
- Produces: `BridgeServices(..., jobs, group_director, memory, persona, sync)` with no defaults and `build_bridge_services(..., jobs=, group_director=, memory=, persona=, sync=)` with no defaults.

- [ ] **Step 1: Add failing required-service architecture guards**

Extend `tests/test_native_runtime_retirement.py`:

```python
def test_all_application_services_are_required_by_composition(self):
    required = {
        "jobs",
        "group_director",
        "memory",
        "persona",
        "sync",
    }
    for name in required:
        with self.subTest(name=name):
            self.assertIs(
                BridgeServices.__dataclass_fields__[name].default,
                MISSING,
            )
            self.assertIs(
                inspect.signature(build_bridge_services)
                .parameters[name]
                .default,
                inspect.Parameter.empty,
            )
```

Replace the narrower `test_job_service_is_required_by_composition` with this broader contract.

- [ ] **Step 2: Add a failing construction test for omitted services**

In `tests/test_composition.py`, add:

```python
def test_build_bridge_services_requires_complete_application_graph(self):
    signature = inspect.signature(build_bridge_services)
    for name in ("jobs", "group_director", "memory", "persona", "sync"):
        self.assertEqual(
            signature.parameters[name].default,
            inspect.Parameter.empty,
            name,
        )
```

Update existing `build_bridge_services(...)` and direct `BridgeServices(...)` fixtures to provide an explicit fake `group_director` alongside the already-provided Memory/Persona/Sync/Jobs values.

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```bash
python3 -m unittest   tests.test_native_runtime_retirement.NativeRuntimeRetirementTests.test_all_application_services_are_required_by_composition   tests.test_composition.CompositionConfigTests.test_build_bridge_services_requires_complete_application_graph -v
```

Expected: FAIL because `group_director`, `memory`, `persona`, and `sync` currently have defaults.

- [ ] **Step 4: Make all application services required**

Change `bridge/composition.py` to:

```python
@dataclass(frozen=True)
class BridgeServices:
    config: BridgeConfig
    db_factory: Callable[[], sqlite3.Connection]
    telegram: TelegramRuntime
    background: BackgroundRuntime
    jobs: JobService
    group_director: GroupDirectorService
    memory: MemoryService
    persona: PersonaService
    sync: SyncService


def build_bridge_services(
    config: BridgeConfig,
    *,
    db_factory: Callable[[], sqlite3.Connection],
    telegram: TelegramRuntime,
    background: BackgroundRuntime,
    jobs: JobService,
    group_director: GroupDirectorService,
    memory: MemoryService,
    persona: PersonaService,
    sync: SyncService,
) -> BridgeServices:
    return BridgeServices(
        config=config,
        db_factory=db_factory,
        telegram=telegram,
        background=background,
        jobs=jobs,
        group_director=group_director,
        memory=memory,
        persona=persona,
        sync=sync,
    )
```

Do not change service implementations.

- [ ] **Step 5: Update all test service-graph fixtures minimally**

Search for direct construction:

```bash
grep -R "BridgeServices(" -n tests bridge
grep -R "build_bridge_services(" -n tests bridge
```

For every test fixture missing one of the five application services, inject an explicit fake object or the existing fake service.

Do not add defaults back to production code to satisfy tests.

- [ ] **Step 6: Run focused composition suites**

Run:

```bash
python3 -m unittest tests.test_native_runtime_retirement tests.test_composition -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bridge/composition.py tests/test_native_runtime_retirement.py tests/test_composition.py
git commit -m "refactor: require complete application service graph"
```

---

### Task 2: Retire Memory Compatibility Resolution

**Files:**
- Modify: `tests/test_memory_service.py`
- Modify: `tests/test_native_runtime_retirement.py`
- Modify: `bridge/memory.py`
- Modify: `bridge/message_commands.py`
- Modify: `bridge/telegram.py`
- Modify: `bridge/media.py`
- Modify: `bridge/help.py`
- Modify: `bridge/input_flows.py`
- Modify other direct callers found by the search commands below.

**Interfaces:**
- Consumes: required `BridgeServices.memory: MemoryService`.
- Produces: Memory-using helpers with explicit `memory_service: MemoryService` parameters; no `compatibility_memory_service()` or `resolve_memory_service()`.

- [ ] **Step 1: Replace compatibility tests with failing retirement guards**

In `tests/test_native_runtime_retirement.py`, extend the forbidden helper set:

```python
def test_no_application_service_fallback_helpers_remain(self):
    forbidden = {
        "compatibility_memory_service",
        "resolve_memory_service",
        "compatibility_persona_service",
        "resolve_persona_service",
        "compatibility_sync_service",
        "resolve_sync_service",
        "_compat_group_director_service",
    }
    offenders = {}
    for path in sorted(BRIDGE_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        hits = sorted(name for name in forbidden if name in source)
        if hits:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = hits
    self.assertEqual(offenders, {})
```

For Task 2, run this test knowing it will remain RED until later tasks remove all service helpers; use focused Memory-specific assertions in `tests/test_memory_service.py` for this task's RED/GREEN cycle.

Delete the old compatibility-service unit tests that assert fallback construction works.

- [ ] **Step 2: Add an explicit Memory injection test**

Add a source-boundary test:

```python
def test_memory_application_paths_do_not_resolve_compatibility_service(self):
    root = Path(__file__).parents[1] / "bridge"
    for filename in (
        "message_commands.py",
        "telegram.py",
        "media.py",
        "help.py",
        "input_flows.py",
    ):
        source = (root / filename).read_text(encoding="utf-8")
        self.assertNotIn("resolve_memory_service", source, filename)
        self.assertNotIn("compatibility_memory_service", source, filename)
```

This initially fails.

- [ ] **Step 3: Inventory Memory fallback call sites**

Run:

```bash
grep -R "resolve_memory_service\|compatibility_memory_service\|memory_service=None" -n bridge tests
```

Classify each hit as one of:

1. high-level router: obtain `services.memory`;
2. leaf workflow: require `memory_service: MemoryService`;
3. test compatibility assertion: delete or migrate to explicit fake.

Do not leave an unclassified hit.

- [ ] **Step 4: Make message routing use the required service**

In `bridge/message_commands.py`, change:

```python
def process_message(..., *, services: BridgeServices) -> None:
    ...
    memory_service = services.memory
    persona_service = services.persona
    group_director = services.group_director
```

Do not use `getattr(..., None)`.

For Memory-specific recovery calls, pass `services.memory` or the local required `memory_service` explicitly.

- [ ] **Step 5: Make Memory leaf helpers explicit**

For functions that currently do:

```python
def reset_session(..., *, memory_service=None):
    memory_service = resolve_memory_service(memory_service)
```

change to:

```python
def reset_session(
    ...,
    *,
    memory_service: MemoryService,
) -> None:
    ...
```

Apply the same rule to:

- generation/recovery functions that call `MemoryService.prompt_context()`, `retain()`, `summary_status()`, or `purge_session()`;
- inactive-session deletion;
- image/document workflows that perform prompt-memory or retention.

Keep transaction and operation-phase code exactly where it already lives.

- [ ] **Step 6: Delete Memory fallback construction**

From `bridge/memory.py`, delete:

```python
def compatibility_memory_service() -> _MemoryService:
    ...


def resolve_memory_service(memory_service=None) -> _MemoryService:
    ...
```

Remove the now-unused `MemoryService as _MemoryService` import if no other use remains.

- [ ] **Step 7: Migrate Memory tests to explicit fakes**

Retain canonical `MemoryServiceTests`.

For integration tests, pass a fake explicitly instead of relying on direct-call fallback:

```python
fake_memory = MemoryService(
    recall_context=lambda *_args, **_kwargs: "",
    summary_for_prompt=lambda *_args, **_kwargs: "",
    summary_state=lambda *_args, **_kwargs: ("", 0),
    retain_session=lambda *_args, **_kwargs: None,
    purge_session_memory=lambda *_args, **_kwargs: 0,
)
```

Delete tests whose only purpose was to prove `compatibility_memory_service()` or `resolve_memory_service()` behavior.

- [ ] **Step 8: Run focused Memory tests**

Run:

```bash
python3 -m unittest tests.test_memory_service tests.test_reset_behavior tests.test_session_delete -v
```

Expected: PASS.

Then run:

```bash
grep -R "resolve_memory_service\|compatibility_memory_service\|memory_service=None" -n bridge
```

Expected: no production hits for compatibility helpers; any remaining optional `memory_service=None` must be investigated and removed if the function actually uses Memory.

- [ ] **Step 9: Commit**

```bash
git add bridge tests
git commit -m "refactor: retire memory service compatibility fallback"
```

---

### Task 3: Retire Persona Compatibility Resolution

**Files:**
- Modify: `tests/test_persona_service.py`
- Modify: `tests/test_composition.py`
- Modify: `bridge/persona_sync.py`
- Modify: `bridge/input_flows.py`
- Modify: `bridge/callbacks.py`
- Modify: `bridge/message_commands.py`
- Modify: `bridge/generation.py` only if a Persona service parameter is still optional there.
- Modify related panel tests only as required.

**Interfaces:**
- Consumes: required `BridgeServices.persona: PersonaService`.
- Produces: Persona-using helpers with explicit `persona_service: PersonaService`; no `compatibility_persona_service()` or `resolve_persona_service()`.

- [ ] **Step 1: Add a failing Persona boundary test**

In `tests/test_persona_service.py`, add:

```python
def test_persona_application_paths_do_not_resolve_compatibility_service(self):
    root = Path(__file__).parents[1] / "bridge"
    for filename in (
        "input_flows.py",
        "callbacks.py",
        "message_commands.py",
        "generation.py",
    ):
        source = (root / filename).read_text(encoding="utf-8")
        self.assertNotIn("resolve_persona_service", source, filename)
        self.assertNotIn("compatibility_persona_service", source, filename)
```

Expected: RED on current code.

- [ ] **Step 2: Inventory Persona fallback call sites**

Run:

```bash
grep -R "resolve_persona_service\|compatibility_persona_service\|persona_service=None" -n bridge tests
```

Also search direct application lookup bypasses:

```bash
grep -R "persona_name(" -n bridge/message_commands.py bridge/generation.py bridge/input_flows.py bridge/callbacks.py
```

The production message path must use `services.persona.name(...)` instead of a compatibility-era direct helper when the service already owns that use case.

- [ ] **Step 3: Tighten Persona input/panel helper signatures**

Change functions such as:

```python
def _persona_input_prompt(..., *, persona_service=None):
    persona_service = resolve_persona_service(persona_service)
```

to:

```python
def _persona_input_prompt(
    ...,
    *,
    persona_service: PersonaService,
) -> str:
    ...
```

Apply the same rule to:

- `send_persona_edit_menu`;
- `start_persona_input`;
- `_handle_persona_input`;
- `send_persona_delete_confirm`;
- `handle_persona_callback`;
- any other Persona UI helper that currently resolves a fallback.

- [ ] **Step 4: Pass the required service from routers**

In `process_message(..., services: BridgeServices)`:

```python
persona_service = services.persona
```

For user-name resolution use:

```python
user_name = (
    persona_service.name(current_persona)
    if current_persona
    else DEFAULT_USER_NAME
)
```

In `process_callback(..., services: BridgeServices)`:

```python
persona_service = services.persona
```

and pass it into Persona callback/input helpers.

- [ ] **Step 5: Delete Persona fallback construction**

From `bridge/persona_sync.py`, delete:

```python
def compatibility_persona_service() -> _PersonaService:
    ...


def resolve_persona_service(persona_service=None) -> _PersonaService:
    ...
```

Remove imports used only by those constructors if they become unused.

Do not delete native Persona backend functions used by `bridge.main._build_startup_services()`.

- [ ] **Step 6: Migrate Persona tests**

Retain canonical `PersonaServiceTests`.

Delete `PersonaCompatibilityServiceTests` and replace any fallback-dependent integration test with an explicit `PersonaService` or small fake implementing the exact method used.

For menu/input tests, a minimal fake may be:

```python
class FakePersonaService:
    def get(self, persona_id):
        return {
            "name": "Native",
            "description": "Description",
        } if persona_id == "native.png" else None

    def name(self, persona_id):
        persona = self.get(persona_id)
        return str(persona["name"]) if persona else ""
```

Add methods only when the test exercises them.

- [ ] **Step 7: Run focused Persona tests**

Run:

```bash
python3 -m unittest   tests.test_persona_service   tests.test_persona_editor   tests.test_persona_native_storage   tests.test_persona_native_sync   tests.test_persona_integrity -v
```

Expected: PASS.

Then run:

```bash
grep -R "resolve_persona_service\|compatibility_persona_service\|persona_service=None" -n bridge
```

Expected: no compatibility helper hits and no optional Persona service on a path that uses Persona application behavior.

- [ ] **Step 8: Commit**

```bash
git add bridge tests
git commit -m "refactor: retire persona service compatibility fallback"
```

---

### Task 4: Retire Sync Compatibility Resolution and Require the Injected Worker Service

**Files:**
- Modify: `tests/test_sync_service.py`
- Modify: `tests/test_composition.py`
- Modify: `bridge/sync_api.py`
- Modify: `bridge/panel_callback_routes.py`
- Modify: `bridge/callbacks.py`
- Modify: `bridge/main.py` only if a call signature changes.

**Interfaces:**
- Consumes: required `BridgeServices.sync: SyncService`.
- Produces: `start_phase3_sync_worker(sync_service: SyncService)`, `_phase3_worker_loop(sync_service: SyncService)`, and `handle_sync_callback(..., sync_service: SyncService)`; no Sync compatibility constructor/resolver.

- [ ] **Step 1: Add a failing injected-worker identity test**

In `tests/test_sync_service.py`, replace compatibility tests with a worker-focused test.

Use a fake service:

```python
class FakeSyncService:
    def __init__(self):
        self.polls = 0

    def poll(self, _db):
        self.polls += 1
        raise StopIteration
```

Patch the worker stop/wait behavior so exactly one cycle executes, then assert the fake instance's `poll()` was called. The test must fail if `_phase3_worker_loop` replaces the supplied service by calling a resolver.

Keep the existing source-boundary assertion:

```python
self.assertNotIn("phase3_sync_poll(", chunk)
self.assertIn("sync_service.poll(", chunk)
```

and add:

```python
self.assertNotIn("resolve_sync_service", chunk)
```

- [ ] **Step 2: Run focused Sync tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_sync_service -v
```

Expected: FAIL because the worker currently calls `resolve_sync_service(sync_service)` and compatibility tests still exist.

- [ ] **Step 3: Require SyncService in callback routing**

Change:

```python
def handle_sync_callback(..., sync_service=None):
    ...
    sync_service = resolve_sync_service(sync_service)
```

to:

```python
def handle_sync_callback(
    ...,
    *,
    sync_service: SyncService,
):
    ...
```

Import `SyncService` from `bridge.sync_service` for annotation if needed.

In `bridge/callbacks.py`:

```python
sync_service = services.sync
```

and pass it explicitly.

- [ ] **Step 4: Require SyncService through the worker lifecycle**

Change conceptually:

```python
def _phase3_worker_loop(sync_service: SyncService) -> None:
    db = None
    try:
        while not _PHASE3_STOP_EVENT.wait(PHASE3_SYNC_INTERVAL_SECONDS):
            try:
                if db is None:
                    db = db_connect()
                sync_service.poll(db)
            ...
```

and:

```python
def start_phase3_sync_worker(sync_service: SyncService) -> None:
    ...
    threading.Thread(
        target=_phase3_worker_loop,
        args=(sync_service,),
        ...
    ).start()
```

Do not allow omitted service or reconstruct it inside the thread.

- [ ] **Step 5: Delete Sync fallback construction**

From `bridge/sync_api.py`, delete:

```python
def compatibility_sync_service() -> _SyncService:
    ...


def resolve_sync_service(sync_service=None) -> _SyncService:
    ...
```

Remove the alias import only if no longer needed elsewhere.

- [ ] **Step 6: Preserve production startup wiring**

Confirm `bridge.main` continues:

```python
start_phase3_sync_worker(sync_service=services.sync)
```

No backend-poll function should be passed directly.

- [ ] **Step 7: Migrate tests and run focused suites**

Delete tests asserting compatibility constructor late-binding.

Run:

```bash
python3 -m unittest   tests.test_sync_service   tests.test_sync_poll_safety   tests.test_sync_integrity   tests.test_sync_ui   tests.test_sync_phase3 -v
```

Expected: PASS.

Then:

```bash
grep -R "resolve_sync_service\|compatibility_sync_service\|sync_service=None" -n bridge
```

Expected: no compatibility hits and no optional Sync service on worker/callback paths.

- [ ] **Step 8: Commit**

```bash
git add bridge tests
git commit -m "refactor: retire sync service compatibility fallback"
```

---

### Task 5: Retire Group Director Compatibility Construction and Wrappers

**Files:**
- Modify: `tests/test_group_director.py`
- Modify: `tests/test_group_director_service.py`
- Modify: `tests/test_native_runtime_retirement.py`
- Modify: `bridge/groups.py`
- Modify: `bridge/message_commands.py`

**Interfaces:**
- Consumes: required `BridgeServices.group_director: GroupDirectorService`.
- Produces: message routing calls `services.group_director.plan(...)` and `services.group_director.prompt_context(...)` directly; no `_compat_group_director_service`, `group_director_plan`, `group_prompt_context`, or `parse_group_director_decision` wrapper remains.

- [ ] **Step 1: Add failing wrapper-retirement guards**

Extend `tests/test_native_runtime_retirement.py`:

```python
def test_group_director_compatibility_wrappers_are_deleted(self):
    source = (BRIDGE_DIR / "groups.py").read_text(encoding="utf-8")
    for forbidden in (
        "def _compat_group_director_service(",
        "def group_director_plan(",
        "def group_prompt_context(",
        "def parse_group_director_decision(",
    ):
        with self.subTest(forbidden=forbidden):
            self.assertNotIn(forbidden, source)
```

Expected: RED.

- [ ] **Step 2: Move parser assertion to the canonical service test**

In `tests/test_group_director_service.py`, add:

```python
def test_parse_decision_rejects_unknown_speaker(self):
    self.assertIsNone(
        self.service._parse_decision(
            '{"speaker":"Mallory","direction":"Enter dramatically."}',
            ["alice.png", "bob.png"],
        )
    )
```

This preserves the behavior formerly tested through the wrapper without preserving the wrapper.

- [ ] **Step 3: Migrate legacy Group Director integration tests**

In `tests/test_group_director.py`, stop calling:

```python
_m_message_commands.group_director_plan(...)
_m_groups.parse_group_director_decision(...)
```

Build a `GroupDirectorService` explicitly using the test's fake collaborators, or route through `process_message(..., services=...)` when the purpose is integration.

For pure planning behavior, prefer the service test module rather than duplicating implementation-level tests.

Retain integration coverage only for the message-routing handoff.

- [ ] **Step 4: Remove fallback branches from message routing**

In `bridge/message_commands.py`, replace:

```python
group_director = getattr(services, "group_director", None) if services is not None else None
if not command.startswith("/"):
    if group_director is not None:
        director_plan = group_director.plan(...)
    else:
        director_plan = group_director_plan(...)
```

with:

```python
group_director = services.group_director
if not command.startswith("/"):
    director_plan = group_director.plan(
        db,
        api_key,
        chat_id,
        session,
        text,
    )
```

For prompt context:

```python
group_context = group_director.prompt_context(
    db,
    chat_id,
    session,
    group_turn[0],
    director_instruction,
)
```

There is no compatibility else branch.

- [ ] **Step 5: Delete Group Director wrappers**

Delete from `bridge/groups.py`:

- `_compat_group_director_service()`;
- `parse_group_director_decision()`;
- `group_director_plan()`;
- `group_prompt_context()`.

Remove imports that existed only to construct the compatibility service, but keep Group UI/command dependencies still used elsewhere.

- [ ] **Step 6: Run focused Group tests**

Run:

```bash
python3 -m unittest   tests.test_group_director_service   tests.test_group_director   tests.test_group_turn_gating -v
```

Expected: PASS.

Then:

```bash
grep -R "_compat_group_director_service\|group_director_plan\|group_prompt_context\|parse_group_director_decision" -n bridge
```

Expected: no production hits.

- [ ] **Step 7: Commit**

```bash
git add bridge tests
git commit -m "refactor: retire group director compatibility wrappers"
```

---

### Task 6: Make Application Routing Require the Complete Service Graph

**Files:**
- Modify: `tests/test_composition.py`
- Modify: `tests/test_native_runtime_retirement.py`
- Modify: `bridge/message_commands.py`
- Modify: `bridge/callbacks.py`
- Modify: `bridge/command_routes.py`
- Modify: `bridge/commands.py` only where the command router currently accepts `services=None`.
- Modify affected routing/UI tests.

**Interfaces:**
- Consumes: complete `BridgeServices` from Task 1.
- Produces: selected application entry points with required `services: BridgeServices`; no `services=None` or `getattr(services, ..., None)` for required application services.

- [ ] **Step 1: Add failing signature/source guards**

In `tests/test_native_runtime_retirement.py`, add:

```python
def test_application_routes_require_service_graph(self):
    from bridge.callbacks import process_callback
    from bridge.message_commands import process_message

    for function in (process_message, process_callback):
        parameter = inspect.signature(function).parameters["services"]
        self.assertIs(
            parameter.default,
            inspect.Parameter.empty,
            function.__name__,
        )


def test_required_service_routes_do_not_use_optional_service_lookup(self):
    for filename in (
        "message_commands.py",
        "callbacks.py",
        "command_routes.py",
    ):
        source = (BRIDGE_DIR / filename).read_text(encoding="utf-8")
        self.assertNotIn("services=None", source, filename)
        self.assertNotIn('getattr(services, "memory", None)', source, filename)
        self.assertNotIn('getattr(services, "persona", None)', source, filename)
        self.assertNotIn('getattr(services, "sync", None)', source, filename)
        self.assertNotIn('getattr(services, "group_director", None)', source, filename)
```

Expected: RED before signature tightening.

- [ ] **Step 2: Require services on message and callback entry points**

Use explicit annotations:

```python
from bridge.composition import BridgeServices


def process_message(
    ...,
    *,
    services: BridgeServices,
) -> None:
    ...


def process_callback(
    ...,
    *,
    services: BridgeServices,
) -> None:
    ...
```

Do not add a default.

- [ ] **Step 3: Tighten command routing only where it consumes services**

If `handle_command_route(..., services=None)` uses service-backed handlers, change it to:

```python
def handle_command_route(
    ...,
    *,
    services: BridgeServices,
) -> bool:
    ...
```

Pass it explicitly from `process_message`.

For lower-level command handlers that consume only Memory or Persona, pass those narrow services rather than the full graph unless the existing function truly coordinates multiple service types.

- [ ] **Step 4: Update direct-call tests with complete fake services**

Introduce a local helper in `tests/test_composition.py` if repeated construction is large:

```python
def _fake_services(
    config,
    *,
    db_factory,
    telegram,
    background,
    jobs,
    group_director,
    memory,
    persona,
    sync,
):
    return BridgeServices(
        config=config,
        db_factory=db_factory,
        telegram=telegram,
        background=background,
        jobs=jobs,
        group_director=group_director,
        memory=memory,
        persona=persona,
        sync=sync,
    )
```

Keep this test-only; do not introduce a production fake/default builder.

For tests calling `process_message()` or `process_callback()` directly, pass `services=...`.

- [ ] **Step 5: Verify no optional application graph remains**

Run:

```bash
grep -R "services=None" -n bridge
grep -R 'getattr(services, "memory", None)\|getattr(services, "persona", None)\|getattr(services, "sync", None)\|getattr(services, "group_director", None)' -n bridge
```

Expected: no hits in the selected application routing code.

A `services` parameter in unrelated code is not changed unless it carries this application graph.

- [ ] **Step 6: Run routing/composition tests**

Run:

```bash
python3 -m unittest   tests.test_composition   tests.test_native_runtime_retirement   tests.test_session_command_routing   tests.test_panel_lifecycle   tests.test_panelification -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bridge tests
git commit -m "refactor: require services at application routing boundaries"
```

---

### Task 7: Close the Compatibility Surface and Run Full Validation

**Files:**
- Modify: `tests/test_native_runtime_retirement.py` if final guard consolidation is needed.
- Modify: `README.md` only if the implementation changed architecture wording beyond what PR #61 already documents.
- No new production architecture should be introduced in this task.

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: exact-head evidence that no application-service compatibility fallback remains and behavior remains green.

- [ ] **Step 1: Run the final compatibility-residue scan**

Run:

```bash
grep -R   -e "compatibility_memory_service"   -e "resolve_memory_service"   -e "compatibility_persona_service"   -e "resolve_persona_service"   -e "compatibility_sync_service"   -e "resolve_sync_service"   -e "_compat_group_director_service"   -e "group_director_plan"   -e "group_prompt_context"   -e "parse_group_director_decision"   bridge tests
```

Expected: no production hits. Test files may contain forbidden names only inside regression-guard string literals.

- [ ] **Step 2: Run service-specific suites together**

Run:

```bash
python3 -m unittest   tests.test_memory_service   tests.test_persona_service   tests.test_sync_service   tests.test_group_director_service   tests.test_job_service   tests.test_job_service_workers -v
```

Expected: PASS with zero failures/errors.

- [ ] **Step 3: Run recovery/integrity regressions**

Run:

```bash
python3 -m unittest   tests.test_operation_recovery   tests.test_reset_behavior   tests.test_session_delete   tests.test_persona_integrity   tests.test_sync_integrity   tests.test_sync_poll_safety   tests.test_group_turn_gating -v
```

Expected: PASS.

- [ ] **Step 4: Run complete unittest/audit suite**

Run:

```bash
python3 -m unittest discover -s tests -q
```

Expected: exit 0, zero failures/errors.

Record the exact test count from output for the PR body.

- [ ] **Step 5: Run complete pytest suite**

Run:

```bash
python3 -m pytest -q
```

Expected: exit 0.

Record the exact passed/subtest counts.

- [ ] **Step 6: Compile all Python sources**

Run:

```bash
python3 -m compileall -q bridge tests
```

Expected: exit 0.

- [ ] **Step 7: Validate installed dependency consistency**

Run:

```bash
python3 -m pip check
```

Expected:

```text
No broken requirements found.
```

- [ ] **Step 8: Run dependency vulnerability audit**

Run the repository CI-equivalent audit command. Current expected command:

```bash
python3 -m pip_audit
```

Expected: no known vulnerabilities in the installed dependency set.

If CI uses a different exact invocation, use `.github/workflows/ci.yml` as authoritative and record that command/output.

- [ ] **Step 9: Review the final diff against the approved spec**

Run:

```bash
git diff --stat main...HEAD
git diff main...HEAD -- bridge tests README.md
```

Verify line-by-line:

- no runtime-context work entered the diff;
- no `main.py` decomposition entered the diff;
- no global service locator was introduced;
- application service implementations retain their behavior;
- transaction/operation-phase boundaries are unchanged;
- only compatibility resolution and required dependency propagation changed.

- [ ] **Step 10: Run permanent architecture guards one final time**

Run:

```bash
python3 -m unittest tests.test_native_runtime_retirement -v
```

Expected: PASS.

- [ ] **Step 11: Commit final guard/docs cleanup if needed**

Only if Task 7 produced tracked changes:

```bash
git add tests/test_native_runtime_retirement.py README.md
git commit -m "test: guard native application service composition"
```

If there are no changes, do not create an empty commit.

- [ ] **Step 12: Prepare the PR**

Before PR creation:

```bash
git status --short
git log --oneline main..HEAD
```

Expected:

- clean working tree;
- only PR #62 commits on the feature branch.

Create a pull request from:

```text
punzer4-code:refactor/pr62-native-service-composition
```

to:

```text
cepeter/SillyTavern-Telegram-Bridge:main
```

PR body must report:

- compatibility helpers/wrappers removed;
- required `BridgeServices` contract;
- affected routing boundaries;
- exact head SHA;
- exact CI/local validation counts;
- branch ahead/behind status;
- changed-file/addition/deletion counts;
- review status;
- explicit note that runtime-context retirement and `main.py` decomposition are out of scope.

Do not merge automatically.

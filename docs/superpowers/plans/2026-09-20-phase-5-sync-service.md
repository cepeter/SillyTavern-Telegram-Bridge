# Phase 5D SyncService Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract Live API Sync application orchestration behind an injected `SyncService` while preserving the current hardened SillyTavern API, polling, conflict, retry/backoff, and state-integrity behavior.

**Architecture:** Add an ordinary-import `SyncService` that owns status, manual sync, realtime toggle, and polling use cases. Production receives it through `BridgeServices`; legacy direct callers resolve a late-bound compatibility service so they still see final runtime-hardened collaborators. `sync_safety.py`, `state_integrity.py`, the SillyTavern API client, and existing worker lifecycle remain compatibility/backend layers during Phase 5D.

**Tech Stack:** Python 3.11, dataclasses, SQLite, threading, unittest/pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-20-sync-service-design.md`

## Global Constraints

- Baseline is upstream `470bc4e28420e097f47fcd336abe5c94903f180f`.
- Preserve loopback-only API URL validation, CSRF/cookie authentication, timeout, redirect, allowed-route, and payload-size protections.
- Preserve sync-ID mismatch, initial-divergence, conflict-stop, import/export, and checkpoint semantics.
- Preserve chat-lock and durable-job exclusion, oldest-binding fairness, scanning past locked candidates, the 32-attempt bound, retry/backoff, and fifth-failure shutdown.
- Preserve the final hardened `apply_sync_snapshot()` path from `state_integrity.py`.
- Keep `sync_safety.py`, `state_integrity.py`, and recovery override entries in place for Phase 6/7.
- Do not add a runtime stage, public-callable override, `_ORIGINAL_*` capture, global service locator, or new Sync protocol.
- `sync_service.py` must remain outside `DEFAULT_RUNTIME_STAGES`.
- Use strict RED -> GREEN TDD and obtain exact-final-head CI before Ready-for-review.
- Do not merge the PR.

## Review Focus

- Compatibility construction must resolve the final hardened `phase3_sync_poll` at call time; capturing the raw `sync_api.py` implementation would silently bypass job-lock safety.
- Manual API/validation failures must disable realtime and return the exact existing `Live API unavailable: ...` result, while unexpected programming errors must still propagate.
- A service-backed remote import must still execute the final `state_integrity.py` snapshot hardening, including explicit metadata clears and Hindsight refresh.
- The worker must retain its SQLite reconnect behavior when `SyncService.poll()` raises `sqlite3.Error`; service injection must not make a poisoned connection persist.
- Legacy direct command/callback/worker callers with no injected `BridgeServices` must resolve the compatibility service instead of failing or falling back to raw execution.

---

### Task 1: SyncService contract and read-only status repository primitive

**Files:**
- Create: `bridge/sync_service.py`
- Create: `tests/test_sync_service.py`
- Modify: `bridge/repositories.py`
- Modify: `tests/test_repository_transactions.py`

**Interfaces:**
- Consumes: final binding loader, session-message count repository callable, manual-sync backend, realtime-toggle backend, poll backend, realtime-disable callable, API-configured callable, expected exception types.
- Produces: `SyncStatus`, `SyncService.status()`, `sync_now()`, `toggle_realtime()`, `poll()`, and `count_session_messages()`.

- [ ] **Step 1: Write RED service tests**

Create `tests/test_sync_service.py` with a fake-collaborator fixture:

```python
import sqlite3
import unittest

from bridge.sync_service import SyncService, SyncStatus


class ExpectedSyncError(RuntimeError):
    pass


class SyncServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.binding = {
            "sync_id": "stb-test",
            "last_synced_at": 123.0,
            "last_direction": "bridge_to_sillytavern_api",
            "realtime_enabled": 1,
        }
        self.disabled = []
        self.polls = []
        self.sync_result = "unchanged"
        self.toggle_result = "realtime API sync disabled"

        self.service = SyncService(
            load_binding=lambda _db, _chat, _session: dict(self.binding),
            count_messages=lambda _db, _chat, _session: 7,
            sync_now_backend=self._sync_now,
            toggle_realtime_backend=lambda *_args: self.toggle_result,
            poll_backend=lambda db: self.polls.append(db),
            disable_realtime=lambda _db, chat, session, error:
                self.disabled.append((chat, session, error)),
            api_configured=lambda: True,
            expected_errors=(ExpectedSyncError, ValueError),
        )

    def tearDown(self):
        self.db.close()

    def _sync_now(self, *_args):
        if isinstance(self.sync_result, BaseException):
            raise self.sync_result
        return self.sync_result

    def test_status_is_structured_and_read_only(self):
        status = self.service.status(self.db, "chat", "session")
        self.assertEqual(
            status,
            SyncStatus(
                session_id="session",
                message_count=7,
                sync_id="stb-test",
                last_synced_at=123.0,
                last_direction="bridge_to_sillytavern_api",
                realtime_enabled=True,
                api_configured=True,
            ),
        )
        self.assertFalse(self.db.in_transaction)

    def test_manual_sync_delegates_success(self):
        self.assertEqual(
            self.service.sync_now(self.db, "chat", "session"),
            "unchanged",
        )
        self.assertEqual(self.disabled, [])

    def test_expected_manual_failure_disables_realtime_and_returns_feedback(self):
        self.sync_result = ExpectedSyncError("API unavailable")
        self.assertEqual(
            self.service.sync_now(self.db, "chat", "session"),
            "Live API unavailable: API unavailable",
        )
        self.assertEqual(
            self.disabled,
            [("chat", "session", "API unavailable")],
        )

    def test_unexpected_manual_failure_propagates(self):
        self.sync_result = RuntimeError("bug")
        with self.assertRaisesRegex(RuntimeError, "bug"):
            self.service.sync_now(self.db, "chat", "session")
        self.assertEqual(self.disabled, [])

    def test_toggle_preserves_backend_result(self):
        self.assertEqual(
            self.service.toggle_realtime(self.db, "chat", "session"),
            "realtime API sync disabled",
        )

    def test_poll_delegates_to_injected_backend(self):
        self.service.poll(self.db)
        self.assertEqual(self.polls, [self.db])
```

Also add a status case where `api_configured()` returns `False` and the binding has zero/empty last-sync values.

- [ ] **Step 2: Verify service tests are RED**

Run:

```bash
python -m unittest tests.test_sync_service -v
```

Expected: import failure because `bridge.sync_service` does not exist.

- [ ] **Step 3: Add RED repository test**

Extend `RepositoryPrimitiveTests.setUp()` with this exact table before adding the test:

```sql
CREATE TABLE messages(
    chat_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
```

Then add:

```python
def test_count_session_messages_is_read_only(self):
    self.db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
        "VALUES(?,?,?,?,?)",
        ("chat", "session", "user", "one", 1.0),
    )
    self.db.execute(
        "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
        "VALUES(?,?,?,?,?)",
        ("chat", "session", "assistant", "two", 2.0),
    )
    self.db.commit()

    traced = []
    self.db.set_trace_callback(traced.append)
    try:
        self.assertEqual(
            repositories.count_session_messages(
                self.db, "chat", "session"
            ),
            2,
        )
    finally:
        self.db.set_trace_callback(None)

    self.assertFalse(self.db.in_transaction)
    self.assertFalse(
        any(
            sql.lstrip().upper().startswith(
                ("INSERT", "UPDATE", "DELETE", "REPLACE",
                 "CREATE", "ALTER", "DROP")
            )
            for sql in traced
        ),
        traced,
    )
```

- [ ] **Step 4: Verify repository test is RED**

Run the new repository test directly.

Expected: `AttributeError` for missing `count_session_messages`.

- [ ] **Step 5: Implement the repository primitive**

Add to `bridge/repositories.py`:

```python
def count_session_messages(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM messages "
        "WHERE chat_id=? AND session_id=?",
        (str(chat_id), str(session_id)),
    ).fetchone()
    return int(row[0] or 0) if row else 0
```

No commit, rollback, or schema creation.

- [ ] **Step 6: Implement minimal SyncService**

Create `bridge/sync_service.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True)
class SyncStatus:
    session_id: str
    message_count: int
    sync_id: str
    last_synced_at: float
    last_direction: str
    realtime_enabled: bool
    api_configured: bool


@dataclass(frozen=True)
class SyncService:
    load_binding: Callable[[sqlite3.Connection, str, str], dict[str, object]]
    count_messages: Callable[[sqlite3.Connection, str, str], int]
    sync_now_backend: Callable[[sqlite3.Connection, str, str], str]
    toggle_realtime_backend: Callable[[sqlite3.Connection, str, str], str]
    poll_backend: Callable[[sqlite3.Connection], None]
    disable_realtime: Callable[[sqlite3.Connection, str, str, str], None]
    api_configured: Callable[[], bool]
    expected_errors: tuple[type[Exception], ...]

    def status(self, db, chat_id, session_id) -> SyncStatus:
        binding = self.load_binding(db, str(chat_id), str(session_id))
        return SyncStatus(
            session_id=str(session_id),
            message_count=self.count_messages(
                db, str(chat_id), str(session_id)
            ),
            sync_id=str(binding.get("sync_id") or ""),
            last_synced_at=float(binding.get("last_synced_at") or 0.0),
            last_direction=str(binding.get("last_direction") or ""),
            realtime_enabled=bool(binding.get("realtime_enabled")),
            api_configured=bool(self.api_configured()),
        )

    def sync_now(self, db, chat_id, session_id) -> str:
        try:
            return str(
                self.sync_now_backend(
                    db, str(chat_id), str(session_id)
                )
            )
        except self.expected_errors as exc:
            self.disable_realtime(
                db, str(chat_id), str(session_id), str(exc)
            )
            return f"Live API unavailable: {exc}"

    def toggle_realtime(self, db, chat_id, session_id) -> str:
        return str(
            self.toggle_realtime_backend(
                db, str(chat_id), str(session_id)
            )
        )

    def poll(self, db) -> None:
        self.poll_backend(db)
```

- [ ] **Step 7: Verify Task 1 GREEN**

Run:

```bash
python -m unittest   tests.test_sync_service   tests.test_repository_transactions -v
```

Expected: all pass.

- [ ] **Step 8: Commit Task 1**

Commit:

```text
refactor: add SyncService application contract
```

---

### Task 2: Late-bound compatibility service and startup composition

**Files:**
- Modify: `bridge/sync_api.py`
- Modify: `bridge/composition.py`
- Modify: `bridge/main.py`
- Modify: `tests/test_sync_service.py`
- Modify: `tests/test_composition.py`
- Modify: `tests/test_runtime_loader.py`

**Interfaces:**
- Consumes: Task 1 `SyncService`, `SyncStatus`, and `count_session_messages`.
- Produces: `compatibility_sync_service()`, `resolve_sync_service()`, `BridgeServices.sync`, and startup-created production SyncService.

- [ ] **Step 1: Add RED compatibility late-binding tests**

In `tests/test_sync_service.py`, import `bridge.runtime as rt` and patch runtime globals after the compatibility module has already loaded:

```python
def test_compatibility_service_late_binds_final_poll_backend(self):
    sentinel_poll = object()

    def final_poll(_db):
        return sentinel_poll

    with patch.object(rt, "phase3_sync_poll", final_poll),          patch.object(rt, "sync_binding") as binding,          patch.object(rt, "phase3_sync_now") as sync_now,          patch.object(rt, "phase3_toggle_realtime") as toggle,          patch.object(rt, "_phase3_disable") as disable,          patch.object(rt, "phase3_api_configured", return_value=True):
        service = rt.compatibility_sync_service()

    self.assertIs(service.poll_backend, final_poll)
    self.assertIs(service.load_binding, binding)
    self.assertIs(service.sync_now_backend, sync_now)
    self.assertIs(service.toggle_realtime_backend, toggle)
    self.assertIs(service.disable_realtime, disable)


def test_resolve_sync_service_prefers_injected_service(self):
    sentinel = object()
    self.assertIs(rt.resolve_sync_service(sentinel), sentinel)
```

- [ ] **Step 2: Add RED composition/startup tests**

Update `tests/test_composition.py`:

- import `SyncService`;
- pass a sentinel `sync` argument to `build_bridge_services()`;
- assert `services.sync is sentinel`;
- add startup collaborator identity coverage:

```python
def test_startup_builds_sync_service_from_final_runtime_collaborators(self):
    with patch.object(rt, "sync_binding") as binding,          patch.object(rt, "phase3_sync_now") as sync_now,          patch.object(rt, "phase3_toggle_realtime") as toggle,          patch.object(rt, "phase3_sync_poll") as poll,          patch.object(rt, "_phase3_disable") as disable,          patch.object(rt, "phase3_api_configured") as configured:
        services = rt._build_startup_services(self.config)

    self.assertIsInstance(services.sync, SyncService)
    self.assertIs(services.sync.load_binding, binding)
    self.assertIs(services.sync.sync_now_backend, sync_now)
    self.assertIs(services.sync.toggle_realtime_backend, toggle)
    self.assertIs(services.sync.poll_backend, poll)
    self.assertIs(services.sync.disable_realtime, disable)
    self.assertIs(services.sync.api_configured, configured)
```

Update the Phase 5 source guard from "stop at persona" to "stop at sync": GroupDirector, Memory, Persona, and Sync must exist; JobService must still not exist.

- [ ] **Step 3: Add RED runtime-loader guard**

Add to `tests/test_runtime_loader.py`:

```python
def test_sync_service_is_not_a_runtime_stage(self):
    loaded_modules = {
        module
        for stage in DEFAULT_RUNTIME_STAGES
        for module in stage.modules
    }
    self.assertNotIn("sync_service.py", loaded_modules)
```

- [ ] **Step 4: Verify RED**

Run:

```bash
python -m unittest   tests.test_sync_service   tests.test_composition   tests.test_runtime_loader -v
```

Expected failures: missing compatibility helpers, missing `BridgeServices.sync`, and missing startup construction.

- [ ] **Step 5: Implement late-bound compatibility construction**

At the ordinary-import top of `bridge/sync_api.py` add:

```python
from bridge.repositories import (
    count_session_messages as _count_session_messages,
)
from bridge.sync_service import SyncService as _SyncService
```

After final raw Sync function definitions are available in that staged module, add:

```python
def compatibility_sync_service() -> _SyncService:
    return _SyncService(
        load_binding=sync_binding,
        count_messages=_count_session_messages,
        sync_now_backend=phase3_sync_now,
        toggle_realtime_backend=phase3_toggle_realtime,
        poll_backend=phase3_sync_poll,
        disable_realtime=_phase3_disable,
        api_configured=phase3_api_configured,
        expected_errors=(SillyTavernApiError, ValueError),
    )


def resolve_sync_service(sync_service=None) -> _SyncService:
    return (
        sync_service
        if sync_service is not None
        else compatibility_sync_service()
    )
```

Do not hoist any of those runtime collaborator values into module-level captured aliases.

- [ ] **Step 6: Wire BridgeServices and startup**

In `bridge/composition.py`:

```python
from bridge.sync_service import SyncService
...
sync: SyncService | None = None
```

Add the optional `sync` keyword to `build_bridge_services()` and place it into the frozen `BridgeServices`.

In `bridge/main.py`, ordinary-import:

```python
from bridge.sync_service import SyncService as _SyncService
from bridge.repositories import (
    count_session_messages as _count_session_messages,
)
```

Inside `_build_startup_services()`, after Persona construction:

```python
sync = _SyncService(
    load_binding=sync_binding,
    count_messages=_count_session_messages,
    sync_now_backend=phase3_sync_now,
    toggle_realtime_backend=phase3_toggle_realtime,
    poll_backend=phase3_sync_poll,
    disable_realtime=_phase3_disable,
    api_configured=phase3_api_configured,
    expected_errors=(SillyTavernApiError, ValueError),
)
```

Pass `sync=sync` into `_build_bridge_services_value()`.

Because `_build_startup_services()` executes only after the staged runtime is fully loaded, these global function lookups must resolve the final hardened runtime values.

- [ ] **Step 7: Verify Task 2 GREEN**

Run the Task 2 unittest set again. Expected: all pass.

- [ ] **Step 8: Commit Task 2**

Commit:

```text
refactor: inject SyncService at startup
```

---

### Task 3: Route Sync command, status UI, and callbacks through SyncService

**Files:**
- Modify: `bridge/recovery.py`
- Modify: `bridge/command_routes.py`
- Modify: `bridge/callbacks.py`
- Modify: `bridge/panel_callback_routes.py`
- Modify: `tests/test_sync_phase3.py`
- Modify: `tests/test_composition.py`

**Interfaces:**
- Consumes: injected `services.sync` or `resolve_sync_service(None)`.
- Produces: service-backed `sync_status_text`, `send_sync_menu`, `handle_sync_callback`, and production propagation through slash commands/callbacks.

- [ ] **Step 1: Add RED status rendering test**

In `tests/test_sync_phase3.py`, ordinary-import `SyncStatus`:

```python
from bridge.sync_service import SyncStatus
```

Then add a fake service returning explicit structured status:

```python
def test_sync_status_text_renders_injected_service_status(self):
    fake = type(
        "FakeSync",
        (),
        {
            "status": lambda _self, _db, _chat, _session:
                SyncStatus(
                    session_id="phase3",
                    message_count=9,
                    sync_id="stb-injected",
                    last_synced_at=0.0,
                    last_direction="",
                    realtime_enabled=True,
                    api_configured=False,
                )
        },
    )()

    text = rt.sync_status_text(
        self.db,
        "chat",
        self.session,
        sync_service=fake,
    )

    self.assertIn("Messages: 9", text)
    self.assertIn("Sync ID: stb-injected", text)
    self.assertIn("Live API sync: on (not configured)", text)
```

- [ ] **Step 2: Add RED callback delegation tests**

Use a fake with `sync_now()`, `toggle_realtime()`, and `status()`, patch raw runtime execution functions to raise, and assert:

```python
def test_sync_now_callback_uses_injected_service(self):
    fake = FakeSyncService()
    with patch.object(
        rt,
        "phase3_sync_now",
        side_effect=AssertionError("raw sync bypassed service"),
    ), patch.object(
        rt,
        "_phase3_disable",
        side_effect=AssertionError("raw disable bypassed service"),
    ):
        handled = rt.handle_sync_callback(
            self.db, "token", callback, answer_callback,
            "sync:now", "chat", callback["message"],
            self.session, "phase3", None,
            sync_service=fake,
        )
    self.assertTrue(handled)
    self.assertEqual(fake.calls[0], ("sync_now", "chat", "phase3"))
```

Add the equivalent `sync:realtime` test that forbids direct `phase3_toggle_realtime`.

Add a manual expected-failure fake result and assert the callback forwards the service's existing `Live API unavailable: ...` string unchanged.

- [ ] **Step 3: Add RED production propagation tests**

In `tests/test_composition.py`:

- give worker/test `BridgeServices` a `sync_service` sentinel;
- patch `send_sync_menu`, invoke the `/sync` command route through `handle_command_route(..., services=services)`, and assert `sync_service=services.sync`;
- patch `handle_primary_panel_callback`, invoke `process_callback(..., services=services)`, and assert its keyword receives the same Sync service.

- [ ] **Step 4: Verify RED**

Run:

```bash
python -m unittest   tests.test_sync_phase3   tests.test_composition -v
```

Expected: missing `sync_service` parameters/propagation.

- [ ] **Step 5: Make recovery UI resolve and use SyncService**

Change signatures:

```python
def sync_status_text(..., *, sync_service=None) -> str:
def send_sync_menu(..., *, sync_service=None) -> None:
def handle_sync_callback(..., *, sync_service=None):
```

At each entry point:

```python
sync_service = resolve_sync_service(sync_service)
```

Render status from `sync_service.status(...)`:

```python
status = sync_service.status(
    db, chat_id, session["session_id"]
)
if status.last_synced_at:
    timestamp = time.strftime(
        "%Y-%m-%d %H:%M:%S %Z",
        time.localtime(status.last_synced_at),
    )
    last = f"{status.last_direction} at {timestamp}"
else:
    last = "never"
enabled = "on" if status.realtime_enabled else "off"
configured = "configured" if status.api_configured else "not configured"
```

For callbacks:

- `sync:realtime` -> `sync_service.toggle_realtime(...)`;
- `sync:now` -> `sync_service.sync_now(...)`;
- menu/status refresh -> `send_sync_menu(..., sync_service=sync_service)`.

Remove the callback's direct expected-error catch and direct `_phase3_disable`; that behavior now belongs to `SyncService.sync_now()`.

- [ ] **Step 6: Propagate service through command and callback routers**

Change `_handle_memory_media(..., services=None)`, pass `services` from `handle_command_route`, and call:

```python
send_sync_menu(
    token,
    chat_id,
    db,
    session,
    sync_service=(
        getattr(services, "sync", None)
        if services is not None else None
    ),
)
```

In `bridge/callbacks.py`:

```python
sync_service = (
    getattr(services, "sync", None)
    if services is not None else None
)
```

Pass it to `handle_primary_panel_callback(..., sync_service=sync_service)`.

Update `handle_primary_panel_callback(..., *, sync_service=None)` and pass it only to `handle_sync_callback`.

- [ ] **Step 7: Verify Task 3 GREEN**

Run the Task 3 tests again plus existing `tests.test_sync_audit`. Expected: all pass.

- [ ] **Step 8: Commit Task 3**

Commit:

```text
refactor: route Sync UI through SyncService
```

---

### Task 4: Route realtime worker polling through the injected service

**Files:**
- Modify: `bridge/sync_api.py`
- Modify: `bridge/main.py`
- Modify: `tests/test_sync_phase3.py`
- Modify: `tests/test_composition.py`

**Interfaces:**
- Consumes: Task 2 `SyncService` and compatibility resolver.
- Produces: `_phase3_worker_loop(sync_service=None)`, `start_phase3_sync_worker(sync_service=None)`, production startup using `services.sync`.

- [ ] **Step 1: Add RED worker delegation test**

Avoid a real long-running thread by invoking the loop with a stop event whose first wait returns `False` and second returns `True`.

```python
def test_worker_loop_polls_through_injected_service(self):
    calls = []

    class FakeSync:
        def poll(self, db):
            calls.append(db)

    waits = iter([False, True])
    with patch.object(
        rt._PHASE3_STOP_EVENT,
        "wait",
        side_effect=lambda _timeout: next(waits),
    ), patch.object(
        rt,
        "db_connect",
        return_value=self.db,
    ), patch.object(
        rt,
        "phase3_sync_poll",
        side_effect=AssertionError("raw poll bypassed service"),
    ):
        rt._phase3_worker_loop(sync_service=FakeSync())

    self.assertEqual(calls, [self.db])
```

Use a non-closing fake connection or patch `close()` as necessary so the fixture remains valid after the loop.

- [ ] **Step 2: Add RED SQLite reconnect preservation test**

Provide a fake service whose first `poll()` raises `sqlite3.OperationalError` and whose second succeeds. Supply two fake connections from `db_connect`. Assert the first is closed and the second is used on the next cycle. This pins the spec's poisoned-connection behavior.

- [ ] **Step 3: Add RED startup propagation test**

Patch `rt.start_phase3_sync_worker` during `main()` setup or isolate the startup call using the existing startup harness and assert:

```python
start_sync.assert_called_once_with(
    sync_service=services.sync
)
```

The test must not require the Telegram poll loop to run indefinitely; use `--check` only if the worker would execute there, otherwise patch the shutdown event/getUpdates path so startup exits immediately.

- [ ] **Step 4: Add RED legacy-resolution test**

Patch `rt.resolve_sync_service` to return a fake and invoke `_phase3_worker_loop()` without an explicit service. Assert the resolved fake receives `poll()`.

- [ ] **Step 5: Verify RED**

Run the focused Sync/composition tests. Expected: worker signatures reject `sync_service` and production startup does not inject it.

- [ ] **Step 6: Implement worker injection**

Change:

```python
def _phase3_worker_loop(sync_service=None) -> None:
    sync_service = resolve_sync_service(sync_service)
    db = None
    try:
        while not _PHASE3_STOP_EVENT.wait(
            PHASE3_SYNC_INTERVAL_SECONDS
        ):
            try:
                if db is None:
                    db = db_connect()
                sync_service.poll(db)
            except Exception as exc:
                ...
```

Preserve the existing rollback, `sqlite3.Error` connection close/reconnect, logging, and final close logic exactly.

Change:

```python
def start_phase3_sync_worker(*, sync_service=None) -> bool:
    ...
    _PHASE3_WORKER = threading.Thread(
        target=_phase3_worker_loop,
        args=(sync_service,),
        name="sillytavern-phase3-sync",
        daemon=True,
    )
```

In `main()`:

```python
start_phase3_sync_worker(sync_service=services.sync)
```

- [ ] **Step 7: Verify Task 4 GREEN**

Run:

```bash
python -m unittest   tests.test_sync_phase3   tests.test_sync_audit   tests.test_composition -v
```

Expected: all pass.

- [ ] **Step 8: Commit Task 4**

Commit:

```text
refactor: poll realtime Sync through SyncService
```

---

### Task 5: Harden the service boundary without changing Sync safety semantics

**Files:**
- Modify: `tests/test_sync_service.py`
- Modify: `tests/test_sync_phase3.py`
- Modify: `tests/test_sync_audit.py`
- Modify: `tests/test_runtime_loader.py`
- Modify production files only if a RED regression identifies a boundary bypass.

**Interfaces:**
- Consumes: complete Phase 5D service/composition/UI/worker path.
- Produces: source-boundary and final-hardening regressions proving Phase 6 behavior was not accidentally bypassed.

- [ ] **Step 1: Add RED source-boundary tests**

In `tests/test_sync_service.py`, inspect migrated functions with `inspect.getsource` or file slicing.

Require:

```python
def test_sync_callback_does_not_call_raw_execution_backends(self):
    source = Path(... / "bridge" / "recovery.py").read_text(...)
    chunk = function_chunk(source, "def handle_sync_callback")
    for forbidden in (
        "phase3_sync_now(",
        "phase3_toggle_realtime(",
        "_phase3_disable(",
    ):
        self.assertNotIn(forbidden, chunk)


def test_realtime_worker_does_not_call_raw_poll_backend(self):
    source = Path(... / "bridge" / "sync_api.py").read_text(...)
    chunk = function_chunk(source, "def _phase3_worker_loop")
    self.assertNotIn("phase3_sync_poll(", chunk)
    self.assertIn("sync_service.poll(", chunk)
```

Also assert `sync_service.py` contains no `bridge.runtime` import and no Telegram import.

- [ ] **Step 2: Add final-hardened-poll identity regression**

Using the runtime load report and compatibility service:

```python
def test_compatibility_service_uses_hardened_poll_override(self):
    service = rt.compatibility_sync_service()
    self.assertIs(service.poll_backend, rt.phase3_sync_poll)

    safety_entry = next(
        item
        for item in rt.RUNTIME_LOAD_REPORT
        if item["module"] == "sync_safety.py"
    )
    self.assertIn(
        "phase3_sync_poll",
        safety_entry["public_callable_overrides"],
    )
```

Do not assume implementation-module import identity; assert against the final runtime symbol.

- [ ] **Step 3: Add service-backed state-integrity import regression**

Extend `tests/test_sync_phase3.py` using the existing fake API:

1. Create a session with a Persona/world assignment and messages.
2. Establish a baseline through the normal API Sync backend.
3. Modify remote metadata to include explicit empty `persona` and empty `world_info`, and modify remote transcript so an import is required.
4. Invoke:

```python
result = rt.resolve_sync_service().sync_now(
    self.db, "chat", "phase3"
)
```

5. Assert:
   - result is `"imported SillyTavern API changes"`;
   - reloaded session has `persona_id == ""`;
   - reloaded session has `world_file == ""`;
   - patch/spy `retain_session_memory` and assert the Hindsight refresh path ran.

This proves the service-backed flow still reaches the final `state_integrity.py` override.

- [ ] **Step 4: Retain safety regressions without rewriting them**

Run all existing `tests.test_sync_audit` cases unchanged. They must continue to prove:

- chat-lock exclusion;
- lock release after job-query failure;
- scan past 32 locked candidates;
- oldest-binding prioritization;
- fifth unexpected failure disables realtime;
- session-delete binding cleanup;
- startup orphan cleanup.

If any fail, do not rewrite `sync_safety.py` for convenience. First add/retain the failing regression and repair the service plumbing so the final safety backend remains in use.

- [ ] **Step 5: Verify runtime-stage invariant**

The Task 2 `test_sync_service_is_not_a_runtime_stage` must pass. Also inspect `DEFAULT_RUNTIME_STAGES` and assert no new allowlist entry was added for `sync_service.py`.

- [ ] **Step 6: Run focused Phase 5D GREEN suite**

Run:

```bash
python -m unittest   tests.test_sync_service   tests.test_sync_phase3   tests.test_sync_audit   tests.test_repository_transactions   tests.test_composition   tests.test_runtime_loader -v
```

Expected: zero failures/errors.

- [ ] **Step 7: Commit Task 5**

Commit:

```text
test: lock SyncService safety boundaries
```

---

### Task 6: Whole-phase verification, review, documentation, and pull request

**Files:**
- Modify: `docs/superpowers/specs/2026-09-20-sync-service-design.md` only if an implementation ruling changed the approved design.
- Modify: `docs/superpowers/plans/2026-09-20-phase-5-sync-service.md` only to mark checklist items backed by evidence.

**Interfaces:**
- Consumes: completed Phase 5D branch.
- Produces: exact-head verification and a Ready-for-review PR; does not merge it.

- [ ] **Step 1: Run focused Sync/service verification**

Run the Task 5 focused unittest command.

Expected: zero failures/errors.

- [ ] **Step 2: Run the repository CI-equivalent suite**

Run:

```bash
python -m compileall bridge tests
python -m unittest discover -s tests -v
python -m pytest -q
pip-audit
```

Expected: compile success, all unittest/pytest tests pass, and no known dependency vulnerabilities.

- [ ] **Step 3: Review the entire branch against baseline**

Compare final Phase 5D head against:

```text
470bc4e28420e097f47fcd336abe5c94903f180f
```

Review specifically for:

- raw Sync execution bypasses around `SyncService`;
- compatibility construction that captured the raw poller before safety overrides;
- any loss of chat-lock/job exclusion or polling fairness;
- changed conflict/backoff/user-facing result semantics;
- accidental edits to `sync_safety.py` or `state_integrity.py` beyond tests unless a verified regression required them;
- new runtime stage/override/`_ORIGINAL_*` symbols;
- unrelated JobService or Phase 6 work.

For any Critical/Important finding, add a failing regression first, verify RED, fix minimally, and rerun focused/full suites.

- [ ] **Step 4: Update verified checklist state**

Mark completed plan items `[x]` only when their test/CI evidence exists. Update the approved spec only if implementation required a genuine design ruling; do not rewrite it merely to match incidental code details.

Commit documentation:

```text
docs: close verified Phase 5D checklist
```

- [ ] **Step 5: Create/update Draft PR and obtain exact-final-head CI**

Open against `cepeter/SillyTavern-Telegram-Bridge:main` with title:

```text
refactor: extract Sync application service
```

Draft PRs may be opened earlier for RED CI evidence, but remain Draft until exact-final-head verification.

The PR body must state:

- status/manual sync/realtime toggle/polling are owned by `SyncService`;
- legacy callers use late-bound compatibility resolution;
- production worker polls through `services.sync`;
- `sync_safety.py` and `state_integrity.py` hardening remain intact;
- `sync_service.py` is outside runtime stages;
- exact final head SHA and CI totals;
- design and plan document paths.

- [ ] **Step 6: Inspect exact-head CI and review state**

For the workflow attached to the exact final SHA, verify every job succeeds and extract actual unittest, pytest/subtest, compile, and dependency-audit evidence.

Also check:

- PR mergeable state;
- comments;
- review threads;
- formal reviews.

Do not reuse a prior green run.

- [ ] **Step 7: Mark Ready only after exact-head GREEN**

Only after exact-head CI is green and no unresolved Critical/Important review issue exists, mark the PR Ready for maintainer review.

Do not merge it.

# Phase 5E JobService Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Extract durable job lifecycle, admission, and recovery orchestration behind an injected `JobService` while preserving all existing durable SQLite, per-chat ordering, operation-idempotency, recovery, and shutdown behavior.

**Architecture:** Add an ordinary-import `JobService` that composes the existing database state-transition primitives with `BackgroundRuntime.submit_chat`. Concrete message/callback/edit/image/voice/document workers remain outside the service; recovery uses an injected resolver that maps decoded `DurableJob` values to `JobSubmission` values. Production uses `services.jobs`; existing helper functions remain temporary compatibility delegates rather than independent lifecycle owners.

**Tech Stack:** Python 3.11, dataclasses, SQLite, threading/concurrent.futures, unittest/pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-20-job-service-design.md`

## Global Constraints

- Baseline is upstream `1dc67befe02bc84e49be224c40506d44a557e52c`.
- Persist the durable job before attempting background admission.
- Rejected background admission must leave the durable job queued and recoverable.
- Preserve queued/scheduled -> running attempt increments, done/failed final states, and current locked/busy finish behavior.
- Preserve per-chat ordered execution, queue/semaphore admission limits, tracked futures, shutdown admission control, and graceful drain behavior.
- Preserve startup requeue/recovery, bounded non-startup backlog recovery, stored model, `resolve_active`, and oldest-first repository ordering.
- Preserve callback/generation/edit operation-idempotency and the native-edit `local_committed` recovery rule.
- Keep SQL/state-transition primitives in `bridge/database.py`; do not duplicate them in JobService.
- Keep concrete business workers outside JobService.
- Do not add a runtime stage, public-callable override, `_ORIGINAL_*` capture, global service locator, scheduler, schema, or migration.
- `job_service.py` must remain outside `DEFAULT_RUNTIME_STAGES`.
- Use strict RED -> GREEN TDD and obtain exact-final-head CI before Ready-for-review.
- Do not merge the PR.

## Review Focus

- **Background admission returns False after durable enqueue:** the job must remain queued, never scheduled or failed, so restart/backlog recovery can still execute it. Task 1 and Task 4 pin this.
- **Recovered payload is malformed or resolver raises:** only that job becomes failed; later recovered rows must still be processed. Task 1 and Task 5 pin this.
- **Durable worker receives a duplicate/already-finished job:** `start()` returning False must prevent the business workflow from running and must not write another final state. Task 3 pins this.
- **Native edit fails after local commit:** the existing `native_edit_committed_after_failure()` decision must still mark the durable job done instead of failed. Task 3 pins this.
- **Worker DB creation fails before lifecycle start:** the durable row must remain queued/recoverable and must not be stranded in scheduled/running. Existing boot-failure regression plus Task 5 pin this.

---

### Task 1: JobService contract and durable recovery value types

**Files:**
- Create: `bridge/job_service.py`
- Create: `tests/test_job_service.py`

**Interfaces:**
- Consumes repository callables with the existing signatures:
  - `enqueue_backend(db, update_id, chat_id, session_id, telegram_message_id, kind, payload) -> int`
  - `actor_backend(db, job_id) -> str`
  - `schedule_backend(db, job_id) -> bool`
  - `start_backend(db, job_id) -> bool`
  - `finish_backend(db, job_id, state, error="") -> bool`
  - `recover_backend(db, recover_running=True) -> list[tuple]`
  - `submit_chat(label, chat_id, worker, *args) -> bool`
- Produces `DurableJob`, `JobSubmission`, and `JobService.enqueue/submit/start/complete/fail/actor_id/recover`.

- [x] **Step 1: Write RED unit tests for core lifecycle**

Create `tests/test_job_service.py`:

```python
import json
from pathlib import Path
import sqlite3
import unittest

from bridge.job_service import DurableJob, JobService, JobSubmission


class JobServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.calls = []
        self.recovery_rows = []
        self.submit_result = True
        self.start_result = True

        def enqueue_backend(
            db,
            update_id,
            chat_id,
            session_id,
            telegram_message_id,
            kind,
            payload,
        ):
            self.calls.append(
                (
                    "enqueue",
                    db,
                    update_id,
                    chat_id,
                    session_id,
                    telegram_message_id,
                    kind,
                    payload,
                )
            )
            return 41

        def submit_chat(label, chat_id, worker, *args):
            self.calls.append(
                ("submit_chat", label, chat_id, worker, args)
            )
            return self.submit_result

        self.service = JobService(
            enqueue_backend=enqueue_backend,
            actor_backend=lambda db, job_id:
                self.calls.append(("actor", db, job_id)) or "100",
            schedule_backend=lambda db, job_id:
                self.calls.append(("scheduled", db, job_id)) or True,
            start_backend=lambda db, job_id:
                self.calls.append(("running", db, job_id))
                or self.start_result,
            finish_backend=lambda db, job_id, state, error="":
                self.calls.append(
                    ("finish", db, job_id, state, error)
                )
                or True,
            recover_backend=lambda db, recover_running=True:
                list(self.recovery_rows),
            submit_chat=submit_chat,
        )

    def tearDown(self):
        self.db.close()

    def test_enqueue_delegates_and_returns_job_id(self):
        job_id = self.service.enqueue(
            self.db,
            9,
            "chat",
            "session",
            77,
            "generation",
            {"text": "hello"},
        )
        self.assertEqual(job_id, 41)
        self.assertEqual(self.calls[0][0], "enqueue")
        self.assertEqual(self.calls[0][2:], (
            9,
            "chat",
            "session",
            77,
            "generation",
            {"text": "hello"},
        ))

    def test_accepted_submit_appends_job_id_and_schedules_once(self):
        worker = lambda *_args: None
        submission = JobSubmission(
            label="generation",
            chat_id="chat",
            worker=worker,
            args=("services", "fields"),
        )

        self.assertTrue(
            self.service.submit(self.db, 41, submission)
        )

        submit = self.calls[0]
        self.assertEqual(submit[:3], ("submit_chat", "generation", "chat"))
        self.assertIs(submit[3], worker)
        self.assertEqual(
            submit[4],
            ("services", "fields", 41),
        )
        self.assertEqual(
            self.calls[1],
            ("scheduled", self.db, 41),
        )

    def test_rejected_submit_does_not_schedule_or_fail(self):
        self.submit_result = False
        submission = JobSubmission(
            label="generation",
            chat_id="chat",
            worker=lambda: None,
            args=(),
        )

        self.assertFalse(
            self.service.submit(self.db, 41, submission)
        )

        self.assertEqual(
            [call[0] for call in self.calls],
            ["submit_chat"],
        )

    def test_start_complete_fail_and_actor_delegate(self):
        self.assertTrue(self.service.start(self.db, 41))
        self.assertEqual(
            self.service.actor_id(self.db, 41),
            "100",
        )
        self.assertTrue(self.service.complete(self.db, 41))
        self.assertTrue(
            self.service.fail(
                self.db,
                42,
                RuntimeError("boom"),
            )
        )

        self.assertIn(("running", self.db, 41), self.calls)
        self.assertIn(("actor", self.db, 41), self.calls)
        self.assertIn(
            ("finish", self.db, 41, "done", ""),
            self.calls,
        )
        self.assertIn(
            ("finish", self.db, 42, "failed", "boom"),
            self.calls,
        )
```

Add a second test where `start_result=False` and assert `start()` returns False unchanged.

- [x] **Step 2: Write RED recovery tests**

Continue in `tests/test_job_service.py`:

```python
    def test_recover_decodes_and_submits_durable_job(self):
        self.recovery_rows = [(
            51,
            "chat",
            "stored-session",
            "10",
            "generation",
            json.dumps({
                "text": "hello",
                "model": "stored::model",
                "resolve_active": True,
            }),
        )]
        seen = []

        def resolver(job):
            seen.append(job)
            return JobSubmission(
                label=job.kind,
                chat_id=job.chat_id,
                worker=lambda *_args: None,
                args=("services",),
            )

        self.service.recover(
            self.db,
            resolver,
            recover_running=True,
        )

        self.assertEqual(
            seen,
            [DurableJob(
                job_id=51,
                chat_id="chat",
                session_id="stored-session",
                telegram_message_id=10,
                kind="generation",
                payload={
                    "text": "hello",
                    "model": "stored::model",
                    "resolve_active": True,
                },
            )],
        )
        self.assertIn(("scheduled", self.db, 51), self.calls)

    def test_recover_unsupported_kind_fails_exactly_once(self):
        self.recovery_rows = [(
            52, "chat", "session", "11", "unknown", "{}"
        )]

        self.service.recover(
            self.db,
            lambda _job: None,
            recover_running=False,
        )

        self.assertEqual(
            [
                call for call in self.calls
                if call[0] == "finish"
            ],
            [(
                "finish",
                self.db,
                52,
                "failed",
                "unsupported recovered job kind",
            )],
        )

    def test_recover_bad_payload_fails_and_continues(self):
        self.recovery_rows = [
            (53, "chat", "session", "12", "generation", "{bad"),
            (54, "chat", "session", "13", "generation", "{}"),
        ]
        seen = []

        def resolver(job):
            seen.append(job.job_id)
            return JobSubmission(
                label=job.kind,
                chat_id=job.chat_id,
                worker=lambda *_args: None,
                args=(),
            )

        self.service.recover(self.db, resolver)

        failures = [
            call for call in self.calls
            if call[0] == "finish"
        ]
        self.assertEqual(failures[0][2:4], (53, "failed"))
        self.assertEqual(seen, [54])
        self.assertIn(("scheduled", self.db, 54), self.calls)

    def test_recover_resolver_exception_fails_and_continues(self):
        self.recovery_rows = [
            (55, "chat", "session", "14", "generation", "{}"),
            (56, "chat", "session", "15", "generation", "{}"),
        ]
        seen = []

        def resolver(job):
            seen.append(job.job_id)
            if job.job_id == 55:
                raise ValueError("bad recovered job")
            return JobSubmission(
                label=job.kind,
                chat_id=job.chat_id,
                worker=lambda *_args: None,
                args=(),
            )

        self.service.recover(self.db, resolver)

        self.assertEqual(seen, [55, 56])
        self.assertIn(
            ("finish", self.db, 55, "failed", "bad recovered job"),
            self.calls,
        )
        self.assertIn(("scheduled", self.db, 56), self.calls)

    def test_recover_rejected_submission_remains_unfinished(self):
        self.submit_result = False
        self.recovery_rows = [
            (57, "chat", "session", "16", "generation", "{}"),
        ]

        self.service.recover(
            self.db,
            lambda job: JobSubmission(
                label=job.kind,
                chat_id=job.chat_id,
                worker=lambda *_args: None,
                args=(),
            ),
        )

        self.assertFalse(
            any(call[0] in {"scheduled", "finish"} for call in self.calls)
        )
```

- [x] **Step 3: Verify Task 1 is RED**

Run:

```bash
python -m unittest tests.test_job_service -v
```

Expected: import failure because `bridge.job_service` does not exist.

- [x] **Step 4: Implement minimal JobService**

Create `bridge/job_service.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import logging
import sqlite3


@dataclass(frozen=True)
class DurableJob:
    job_id: int
    chat_id: str
    session_id: str
    telegram_message_id: int
    kind: str
    payload: dict[str, object]


@dataclass(frozen=True)
class JobSubmission:
    label: str
    chat_id: str
    worker: Callable[..., None]
    args: tuple[object, ...]


@dataclass(frozen=True)
class JobService:
    enqueue_backend: Callable[..., int]
    actor_backend: Callable[..., str]
    schedule_backend: Callable[..., bool]
    start_backend: Callable[..., bool]
    finish_backend: Callable[..., bool]
    recover_backend: Callable[..., list[tuple]]
    submit_chat: Callable[..., bool]

    def enqueue(
        self,
        db: sqlite3.Connection,
        update_id: int,
        chat_id: str,
        session_id: str,
        telegram_message_id: int,
        kind: str,
        payload: dict[str, object],
    ) -> int:
        return int(self.enqueue_backend(
            db,
            int(update_id),
            str(chat_id),
            str(session_id),
            int(telegram_message_id),
            str(kind),
            payload,
        ))

    def submit(
        self,
        db: sqlite3.Connection,
        job_id: int,
        submission: JobSubmission,
    ) -> bool:
        accepted = bool(self.submit_chat(
            submission.label,
            submission.chat_id,
            submission.worker,
            *submission.args,
            int(job_id),
        ))
        if accepted:
            self.schedule_backend(db, int(job_id))
        return accepted

    def start(self, db, job_id: int) -> bool:
        return bool(self.start_backend(db, int(job_id)))

    def complete(self, db, job_id: int) -> bool:
        return bool(
            self.finish_backend(db, int(job_id), "done", "")
        )

    def fail(self, db, job_id: int, error) -> bool:
        return bool(
            self.finish_backend(
                db,
                int(job_id),
                "failed",
                str(error),
            )
        )

    def actor_id(self, db, job_id: int | None) -> str:
        return str(self.actor_backend(db, job_id) or "")

    def recover(
        self,
        db,
        resolver,
        *,
        recover_running: bool = True,
    ) -> None:
        rows = self.recover_backend(
            db,
            recover_running=recover_running,
        )
        for (
            job_id,
            chat_id,
            session_id,
            message_id,
            kind,
            payload_json,
        ) in rows:
            try:
                payload = json.loads(payload_json)
                if not isinstance(payload, dict):
                    raise ValueError("job payload must be an object")
                job = DurableJob(
                    job_id=int(job_id),
                    chat_id=str(chat_id),
                    session_id=str(session_id),
                    telegram_message_id=int(message_id or 0),
                    kind=str(kind),
                    payload=payload,
                )
                submission = resolver(job)
                if submission is None:
                    self.fail(
                        db,
                        job.job_id,
                        "unsupported recovered job kind",
                    )
                    continue
                if not self.submit(db, job.job_id, submission):
                    logging.warning(
                        "Could not dispatch recovered %s job %s",
                        job.kind,
                        job.job_id,
                    )
            except Exception as exc:
                self.fail(db, int(job_id), exc)
                logging.error(
                    "Could not recover job %s",
                    job_id,
                    exc_info=True,
                )
```

Important: after the `submission is None` branch, `continue` prevents the surrounding exception handler from writing a second failure.

- [x] **Step 5: Verify Task 1 GREEN**

Run:

```bash
python -m unittest tests.test_job_service -v
```

Expected: all JobService unit tests pass.

- [x] **Step 6: Commit Task 1**

Commit:

```text
refactor: add JobService application contract
```

---

### Task 2: Inject JobService and preserve compatibility helper entry points

**Files:**
- Modify: `bridge/composition.py`
- Modify: `bridge/main.py`
- Modify: `tests/test_composition.py`
- Modify: `tests/test_runtime_loader.py`

**Interfaces:**
- Consumes Task 1 `JobService` and `JobSubmission`.
- Produces `BridgeServices.jobs`, startup JobService construction, `_compatibility_job_service(background)`, `_jobs_for_services(services)`, and a compatibility `submit_durable_chat_job()` delegate.

- [x] **Step 1: Add RED composition tests**

In `tests/test_composition.py`, update the mock import and add JobService imports:

```python
from unittest.mock import Mock, patch

from bridge.job_service import JobService, JobSubmission
```

Extend the core composition test so:

```python
jobs = object()
services = build_bridge_services(
    config,
    db_factory=...,
    telegram=telegram,
    background=background,
    group_director=group_director,
    memory=memory,
    persona=persona,
    sync=sync,
    jobs=jobs,
)
self.assertIs(services.jobs, jobs)
```

Add startup identity coverage:

```python
def test_startup_builds_job_service_from_final_job_collaborators(self):
    with patch.object(rt, "enqueue_job") as enqueue,          patch.object(rt, "job_actor_id") as actor,          patch.object(rt, "mark_job_scheduled") as scheduled,          patch.object(rt, "mark_job_running") as running,          patch.object(rt, "finish_job") as finish,          patch.object(rt, "recover_jobs") as recover,          patch.object(rt, "submit_chat_background") as submit_chat:
        services = rt._build_startup_services(self.config)

    self.assertIsInstance(services.jobs, JobService)
    self.assertIs(services.jobs.enqueue_backend, enqueue)
    self.assertIs(services.jobs.actor_backend, actor)
    self.assertIs(services.jobs.schedule_backend, scheduled)
    self.assertIs(services.jobs.start_backend, running)
    self.assertIs(services.jobs.finish_backend, finish)
    self.assertIs(services.jobs.recover_backend, recover)
    self.assertIs(services.jobs.submit_chat, submit_chat)
```

Update the existing Phase 5 source guard from "stops at Sync" to "stops at JobService". The current guard already forbids `class JobService`; replace that prohibition with:

```python
self.assertIn("class GroupDirectorService", source)
self.assertIn("class MemoryService", source)
self.assertIn("class PersonaService", source)
self.assertIn("class SyncService", source)
self.assertIn("class JobService", source)
```

Do not add a speculative future-service class name.

- [x] **Step 2: Add RED runtime-stage guard**

In `tests/test_runtime_loader.py`:

```python
def test_job_service_is_not_a_runtime_stage(self):
    loaded_modules = {
        module
        for stage in DEFAULT_RUNTIME_STAGES
        for module in stage.modules
    }
    self.assertNotIn("job_service.py", loaded_modules)
```

- [x] **Step 3: Add RED compatibility submit test**

Replace the existing raw-scheduling expectation in `RecoveryCompositionTests.test_submit_durable_chat_job_uses_injected_background_and_explicit_job_id`.

Patch `rt._compatibility_job_service` and assert the wrapper delegates through JobService:

```python
fake_jobs = Mock()
fake_jobs.submit.return_value = True

with patch.object(
    rt,
    "_compatibility_job_service",
    return_value=fake_jobs,
):
    queued = rt.submit_durable_chat_job(
        self.db,
        self.background,
        "generation",
        "chat",
        41,
        rt.process_message_job,
        self.services,
        {"name": "Mira"},
        "chat",
        "hello",
        10,
        None,
        None,
    )

self.assertTrue(queued)
submission = fake_jobs.submit.call_args.args[2]
self.assertIsInstance(submission, JobSubmission)
self.assertEqual(submission.label, "generation")
self.assertEqual(submission.chat_id, "chat")
self.assertIs(submission.worker, rt.process_message_job)
self.assertEqual(submission.args[0], self.services)
fake_jobs.submit.assert_called_once()
```

Retain the existing explicit-job-id assertion through `fake_jobs.submit.call_args.args[1] == 41`.

- [x] **Step 4: Verify Task 2 RED**

Run:

```bash
python -m unittest   tests.test_composition   tests.test_runtime_loader -v
```

Expected failures: missing `BridgeServices.jobs`, missing startup JobService, missing compatibility helper.

- [x] **Step 5: Wire composition and production startup**

In `bridge/composition.py`:

```python
from bridge.job_service import JobService
...
jobs: JobService | None = None
```

Add `jobs` to `build_bridge_services()`.

In `bridge/main.py`, ordinary-import:

```python
from bridge.job_service import (
    JobService as _JobService,
    JobSubmission as _JobSubmission,
)
```

Refactor startup to create the background value before JobService:

```python
background = _BackgroundRuntime(
    submit_chat=submit_chat_background,
    register_backlog_dispatcher=register_durable_backlog_dispatcher,
    begin_shutdown=begin_background_shutdown,
)
jobs = _JobService(
    enqueue_backend=enqueue_job,
    actor_backend=job_actor_id,
    schedule_backend=mark_job_scheduled,
    start_backend=mark_job_running,
    finish_backend=finish_job,
    recover_backend=recover_jobs,
    submit_chat=background.submit_chat,
)
return _build_bridge_services_value(
    config,
    db_factory=_partial(db_connect, config.db_file),
    telegram=...,
    background=background,
    ...,
    sync=sync,
    jobs=jobs,
)
```

- [x] **Step 6: Implement compatibility helper and submit wrapper**

In `bridge/main.py`:

```python
def _compatibility_job_service(
    background: _BackgroundRuntime,
) -> _JobService:
    return _JobService(
        enqueue_backend=enqueue_job,
        actor_backend=job_actor_id,
        schedule_backend=mark_job_scheduled,
        start_backend=mark_job_running,
        finish_backend=finish_job,
        recover_backend=recover_jobs,
        submit_chat=background.submit_chat,
    )


def _jobs_for_services(
    services: _BridgeServices,
) -> _JobService:
    if services.jobs is not None:
        return services.jobs
    return _compatibility_job_service(services.background)
```

Rewrite the legacy wrapper without raw scheduling logic:

```python
def submit_durable_chat_job(
    db,
    background,
    label,
    chat_id,
    job_id,
    function,
    *args,
) -> bool:
    jobs = _compatibility_job_service(background)
    return jobs.submit(
        db,
        int(job_id),
        _JobSubmission(
            label=str(label),
            chat_id=str(chat_id),
            worker=function,
            args=tuple(args),
        ),
    )
```

This helper remains a compatibility entry point only; later production paths use `services.jobs`.

- [x] **Step 7: Verify Task 2 GREEN**

Run the Task 2 tests. Expected: all pass.

- [x] **Step 8: Commit Task 2**

Commit:

```text
refactor: inject JobService at startup
```

---

### Task 3: Route all durable worker lifecycle transitions through JobService

**Files:**
- Modify: `bridge/main.py`
- Modify: `bridge/media.py`
- Modify: `bridge/help.py`
- Modify: `tests/test_composition.py`
- Create: `tests/test_job_service_workers.py`

**Interfaces:**
- Consumes `_jobs_for_services(services)` and Task 1 lifecycle methods.
- Produces service-backed lifecycle for message, callback, edit, image, voice, and document durable workers.

- [x] **Step 1: Create a reusable fake JobService for worker tests**

Create `tests/test_job_service_workers.py` with:

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge.runtime as rt
from bridge.composition import (
    BackgroundRuntime,
    BridgeConfig,
    BridgeServices,
    TelegramRuntime,
)


class FakeJobs:
    def __init__(self):
        self.calls = []
        self.start_result = True
        self.actor = "100"

    def start(self, db, job_id):
        self.calls.append(("start", db, job_id))
        return self.start_result

    def complete(self, db, job_id):
        self.calls.append(("complete", db, job_id))
        return True

    def fail(self, db, job_id, error):
        self.calls.append(("fail", db, job_id, str(error)))
        return True

    def actor_id(self, db, job_id):
        self.calls.append(("actor", db, job_id))
        return self.actor


class JobWorkerServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "jobs.sqlite3"
        self.jobs = FakeJobs()
        config = BridgeConfig(
            bot_token="token",
            api_key="key",
            default_model="provider::model",
            default_character_file="mira.png",
            card_file=Path(self.tmp.name) / "mira.png",
            db_file=self.path,
            allowed_users=frozenset(),
        )
        config.card_file.write_bytes(b"card")
        self.sent = []
        self.services = BridgeServices(
            config=config,
            db_factory=lambda: rt.db_connect(self.path),
            telegram=TelegramRuntime(
                request=lambda *_args, **_kwargs: {},
                send_text=lambda *args, **_kwargs:
                    self.sent.append(args),
            ),
            background=BackgroundRuntime(
                submit_chat=lambda *_args, **_kwargs: True,
                register_backlog_dispatcher=lambda _callback: None,
                begin_shutdown=lambda: None,
            ),
            jobs=self.jobs,
        )

    def tearDown(self):
        self.tmp.cleanup()
```

- [x] **Step 2: Add RED duplicate-start guard for message worker**

```python
    def test_message_worker_stops_when_job_start_is_rejected(self):
        self.jobs.start_result = False
        with patch.object(
            rt,
            "process_message",
            side_effect=AssertionError("business workflow must not run"),
        ):
            rt.process_message_job(
                self.services,
                {"name": "Mira"},
                "chat",
                "hello",
                10,
                job_id=41,
            )

        self.assertEqual(self.jobs.calls[0][0], "start")
        self.assertFalse(
            any(call[0] in {"complete", "fail"} for call in self.jobs.calls)
        )
```

- [x] **Step 3: Add RED lifecycle tests for every worker**

For message worker success/failure, patch `committed_assistant_for_message=None` and `process_message`.

For callback, patch `operation_was_applied=False` and `process_callback`.

For image, patch `committed_assistant_for_message=None` and `process_telegram_image`.

For voice, patch `committed_assistant_for_message=None` and `process_voice_message`.

For document, patch `import_telegram_document`.

For edit, patch `edit_telegram_user_message`.

Each success test must assert:

```python
self.assertEqual(
    [call[0] for call in self.jobs.calls if call[0] != "actor"],
    ["start", "complete"],
)
```

Each representative failure path must assert `["start", "fail"]` and verify the user-facing error message remains unchanged.

At minimum include explicit failure tests for message, callback, edit, voice, image, and document so no worker retains raw `finish_job` behavior.

- [x] **Step 4: Pin actor lookup migration**

Add message/callback/voice tests that set `self.jobs.actor="777"`, patch `set_panel_actor_context`, and assert `"777"` is used for the durable worker actor context.

- [x] **Step 5: Pin native-edit local-commit recovery**

```python
    def test_locally_committed_edit_failure_completes_instead_of_failing(self):
        with patch.object(
            rt,
            "edit_telegram_user_message",
            side_effect=RuntimeError("provider delivery failed"),
        ), patch.object(
            rt,
            "native_edit_committed_after_failure",
            return_value=True,
        ):
            rt.process_edit_job(
                self.services,
                "chat",
                77,
                "edited",
                job_id=45,
            )

        names = [call[0] for call in self.jobs.calls]
        self.assertEqual(names, ["start", "complete"])
        self.assertNotIn("fail", names)
```

- [x] **Step 6: Verify worker tests are RED**

Run:

```bash
python -m unittest tests.test_job_service_workers -v
```

Expected: workers still call raw `mark_job_running`, `finish_job`, and `job_actor_id`; fake JobService calls are missing.

- [x] **Step 7: Migrate workers minimally**

At the beginning of every durable worker:

```python
jobs = _jobs_for_services(services)
```

Replace:

```python
if job_id is not None and not mark_job_running(db, job_id):
```

with:

```python
if job_id is not None and not jobs.start(db, job_id):
```

Replace durable actor lookup:

```python
job_actor_id(db, job_id)
```

with:

```python
jobs.actor_id(db, job_id)
```

Replace every:

```python
finish_job(db, job_id, "done")
```

with:

```python
jobs.complete(db, job_id)
```

and:

```python
finish_job(db, job_id, "failed", str(exc))
```

with:

```python
jobs.fail(db, job_id, exc)
```

Apply to:
- `process_message_job`
- `process_callback_job`
- `process_edit_job`
- `process_image_job`
- `process_voice_job`
- `process_document_job`

Do not change their chat locks, DB factory usage, business calls, user-facing messages, operation checks, or finally blocks.

- [x] **Step 8: Preserve callback-applied fast path**

In `process_callback_job`, retain:

```python
if job_id is not None and operation_was_applied(db, job_id):
    jobs.complete(db, job_id)
    return
```

Add/retain a test that patches `process_callback` to raise if called and asserts the fake JobService receives exactly `start`, then `complete`.

- [x] **Step 9: Verify Task 3 GREEN**

Run:

```bash
python -m unittest   tests.test_job_service_workers   tests.test_composition -v
```

Expected: all pass.

- [x] **Step 10: Commit Task 3**

Commit:

```text
refactor: route durable workers through JobService
```

---

### Task 4: Route Telegram durable enqueue and admission through services.jobs

**Files:**
- Modify: `bridge/main.py`
- Modify: `tests/test_composition.py`
- Modify: `tests/test_job_service.py`

**Interfaces:**
- Consumes `services.jobs.enqueue()`, `services.jobs.submit()`, and `JobSubmission`.
- Produces a Telegram update loop with no direct durable enqueue/admission orchestration for callback, edit, voice, image, document, command, or generation paths.

- [x] **Step 1: Add an integration fake for update-loop job calls**

In `tests/test_composition.py`, add:

```python
class RecordingJobs:
    def __init__(self, *, submit_result=True):
        self.calls = []
        self.submit_result = submit_result
        self.next_id = 700

    def enqueue(
        self,
        db,
        update_id,
        chat_id,
        session_id,
        message_id,
        kind,
        payload,
    ):
        job_id = self.next_id
        self.next_id += 1
        self.calls.append((
            "enqueue",
            update_id,
            chat_id,
            session_id,
            message_id,
            kind,
            payload,
        ))
        return job_id

    def submit(self, db, job_id, submission):
        self.calls.append(("submit", job_id, submission))
        return self.submit_result

    def recover(self, *_args, **_kwargs):
        return None
```

- [x] **Step 2: Add a one-update main-loop harness**

In `StartupCompositionTests`, add a helper that builds `BridgeServices` with `RecordingJobs`, returns one Telegram update from the first `getUpdates` call, sets `_SHUTDOWN_EVENT`, and returns no further updates.

Patch the same startup-only dependencies already used by `test_main_starts_sync_worker_with_injected_sync_service`:
- argparse
- load_env_file
- refresh_phase3_config
- enforce_runtime_permissions
- _load_startup_config
- _build_startup_services
- validate_startup_credential
- set_bot_commands
- read_png_chara
- card_fields
- install_bridge_signal_handlers
- start/stop Sync worker
- shutdown_background_executors
- run_database_maintenance

The helper returns the `RecordingJobs` instance and captured Telegram sends.

- [x] **Step 3: Add RED table-driven enqueue/submit tests**

Use subtests for these updates and expected kinds:

```python
cases = [
    (
        "callback",
        {
            "update_id": 1,
            "callback_query": {
                "id": "cb",
                "from": {"id": 100},
                "data": "enum:status",
                "message": {
                    "message_id": 10,
                    "chat": {"id": "chat"},
                },
            },
        },
        "callback",
    ),
    (
        "edit",
        {
            "update_id": 2,
            "edited_message": {
                "message_id": 11,
                "from": {"id": 100},
                "chat": {"id": "chat"},
                "text": "edited",
            },
        },
        "edit",
    ),
    (
        "voice",
        {
            "update_id": 3,
            "message": {
                "message_id": 12,
                "from": {"id": 100},
                "chat": {"id": "chat"},
                "voice": {"file_id": "voice"},
            },
        },
        "voice",
    ),
    (
        "image",
        {
            "update_id": 4,
            "message": {
                "message_id": 13,
                "from": {"id": 100},
                "chat": {"id": "chat"},
                "photo": [{"file_id": "photo", "file_size": 5}],
            },
        },
        "image",
    ),
    (
        "document",
        {
            "update_id": 5,
            "message": {
                "message_id": 14,
                "from": {"id": 100},
                "chat": {"id": "chat"},
                "document": {
                    "file_id": "doc",
                    "file_name": "notes.txt",
                    "mime_type": "text/plain",
                },
            },
        },
        "document",
    ),
    (
        "command",
        {
            "update_id": 6,
            "message": {
                "message_id": 15,
                "from": {"id": 100},
                "chat": {"id": "chat"},
                "text": "/status",
            },
        },
        "command",
    ),
    (
        "generation",
        {
            "update_id": 7,
            "message": {
                "message_id": 16,
                "from": {"id": 100},
                "chat": {"id": "chat"},
                "text": "hello",
            },
        },
        "generation",
    ),
]
```

For each case, assert exactly one `enqueue` and one `submit`, and:

```python
self.assertEqual(enqueue_call[5], expected_kind)
self.assertIsInstance(submit_call[2], JobSubmission)
self.assertEqual(submit_call[2].label, expected_kind)
```

For the callback case, choose callback data that is not handled by the immediate help callback path.

- [x] **Step 4: Pin rejected-admission user feedback**

Run generation, image, voice, document, and long-running command cases with `RecordingJobs(submit_result=False)`.

Assert their existing messages remain:
- generation: `"⏳ Message saved for generation after restart."`
- image: `"🖼️ Image saved for processing after restart."`
- voice: `"🎙️ Voice saved for processing after restart."`
- document: `"📄 Document saved for processing after restart."`
- long-running command: `"⏳ Command saved for execution after restart."`

For ordinary slash commands whose current branch unconditionally reports queued, preserve the current text exactly; do not invent a new failure message in Phase 5E.

- [x] **Step 5: Verify Task 4 RED**

Run the focused composition tests. Expected: `RecordingJobs` sees no enqueue/submit calls because main still invokes raw helpers.

- [x] **Step 6: Migrate each update-loop durable path**

For every durable path, replace raw enqueue:

```python
job_id = enqueue_job(
    db,
    update_id,
    chat_id,
    session_id,
    message_id,
    kind,
    payload,
)
```

with:

```python
job_id = services.jobs.enqueue(
    db,
    update_id,
    chat_id,
    session_id,
    message_id,
    kind,
    payload,
)
```

Replace raw submission with:

```python
queued = services.jobs.submit(
    db,
    job_id,
    _JobSubmission(
        label=kind,
        chat_id=chat_id,
        worker=worker,
        args=(...without job_id...),
    ),
)
```

The service appends `job_id` to the worker args.

Apply to callback, edit, voice, both image branches, document, command, and generation.

Do not change immediate help callbacks because they are intentionally not durable jobs today.

- [x] **Step 7: Add source guard for update loop**

In `tests/test_job_service.py`, slice `bridge/main.py` from `def main` to EOF and assert:

```python
self.assertNotIn("enqueue_job(", main_chunk)
self.assertNotIn("submit_durable_chat_job(", main_chunk)
self.assertIn("services.jobs.enqueue(", main_chunk)
self.assertIn("services.jobs.submit(", main_chunk)
```

This prevents a future durable branch from silently bypassing JobService.

- [x] **Step 8: Verify Task 4 GREEN**

Run:

```bash
python -m unittest   tests.test_composition   tests.test_job_service -v
```

Expected: all pass.

- [x] **Step 9: Commit Task 4**

Commit:

```text
refactor: route durable intake through JobService
```

---

### Task 5: Move startup/backlog recovery orchestration behind JobService

**Files:**
- Modify: `bridge/main.py`
- Modify: `tests/test_composition.py`
- Modify: `tests/test_audit_regressions.py`
- Modify: `tests/test_job_service.py`
- Modify: `tests/test_runtime_loader.py`

**Interfaces:**
- Consumes `DurableJob`, `JobSubmission`, `JobService.recover()`.
- Produces `resolve_recovered_job_submission(services, fields, job)`, compatibility `dispatch_recovered_jobs`, service-backed startup and backlog recovery.

- [x] **Step 1: Add RED resolver mapping tests**

In `tests/test_composition.py`, construct explicit `DurableJob` values and call:

```python
submission = rt.resolve_recovered_job_submission(
    self.services,
    {"name": "Mira"},
    DurableJob(
        job_id=51,
        chat_id="chat",
        session_id="stored-session",
        telegram_message_id=10,
        kind="generation",
        payload={
            "text": "hello",
            "model": "stored::model",
        },
    ),
)
```

Assert:

```python
self.assertEqual(submission.label, "generation")
self.assertIs(inspect.unwrap(submission.worker), rt.process_message_job)
self.assertIs(submission.args[0], self.services)
self.assertEqual(submission.args[-1], "stored::model")
```

Important: `submission.args` must not contain the durable `job_id`; JobService appends it.

Add explicit `resolve_active=True` and `False` cases:
- True -> queued session argument is `None`.
- False/absent -> queued session argument is the persisted `job.session_id`.

Add mapping tests for callback, edit, voice, image, and document, checking stored payload fields.

Unknown kind returns `None`.

- [x] **Step 2: Add RED startup recovery injection test**

Use a fake `jobs` object with:

```python
def recover(self, db, resolver, *, recover_running=True):
    self.calls.append((db, resolver, recover_running))
```

Build production-like services and run the startup harness.

Assert startup calls exactly once with `recover_running=True`.

Do not patch `dispatch_recovered_jobs` in this test; production startup should call `services.jobs.recover` directly.

- [x] **Step 3: Add RED backlog recovery test**

Update `test_backlog_dispatcher_reuses_same_services_instance`.

Use a fake injected jobs service and assert invoking the returned dispatcher:
- opens exactly one DB connection,
- calls `jobs.recover(..., recover_running=False)`,
- resolver maps through the same `resolve_recovered_job_submission`,
- closes the connection.

- [x] **Step 4: Preserve compatibility dispatch helper**

Add/modify a direct helper test:

```python
with patch.object(
    rt,
    "_jobs_for_services",
    return_value=fake_jobs,
):
    rt.dispatch_recovered_jobs(
        self.db,
        self.services,
        {"name": "Mira"},
        recover_running=False,
    )

fake_jobs.recover.assert_called_once()
self.assertFalse(
    fake_jobs.recover.call_args.kwargs["recover_running"]
)
```

The wrapper should contain no row loop of its own after migration.

- [x] **Step 5: Pin bounded repository recovery and boot failure**

Retain unchanged:
- `test_startup_recovery_is_bounded` in `tests/test_audit_regressions.py`;
- `test_transient_worker_boot_failure_requeues_scheduled_job`;
- `test_transient_worker_boot_requeues_on_injected_database_path`.

These tests remain required. If the service migration breaks them, repair service plumbing rather than changing the recovery limit/state semantics.

- [x] **Step 6: Implement recovered-job resolver**

In `bridge/main.py`:

```python
def resolve_recovered_job_submission(
    services: _BridgeServices,
    fields: dict,
    job: _DurableJob,
) -> _JobSubmission | None:
    payload = job.payload
    model_override = str(
        payload.get("model") or services.config.default_model
    )
    session_for_job = (
        None
        if payload.get("resolve_active")
        else job.session_id
    )

    if job.kind in {"generation", "command"}:
        return _JobSubmission(
            label=job.kind,
            chat_id=job.chat_id,
            worker=process_message_job,
            args=(
                services,
                fields,
                job.chat_id,
                str(payload["text"]),
                job.telegram_message_id,
                session_for_job,
                model_override,
            ),
        )
    if job.kind == "callback":
        return _JobSubmission(
            label=job.kind,
            chat_id=job.chat_id,
            worker=process_callback_job,
            args=(
                services,
                job.chat_id,
                payload["callback"],
            ),
        )
    if job.kind == "edit":
        return _JobSubmission(
            label=job.kind,
            chat_id=job.chat_id,
            worker=process_edit_job,
            args=(
                services,
                job.chat_id,
                job.telegram_message_id,
                str(payload["text"]),
                model_override,
            ),
        )
    if job.kind == "voice":
        return _JobSubmission(
            label=job.kind,
            chat_id=job.chat_id,
            worker=process_voice_job,
            args=(
                services,
                fields,
                job.chat_id,
                payload["voice"],
                job.telegram_message_id,
                session_for_job,
                model_override,
            ),
        )
    if job.kind == "image":
        return _JobSubmission(
            label=job.kind,
            chat_id=job.chat_id,
            worker=process_image_job,
            args=(
                services,
                job.chat_id,
                str(payload["file_id"]),
                str(payload.get("caption") or ""),
                int(payload.get("file_size") or 0),
                job.telegram_message_id,
                session_for_job,
                model_override,
            ),
        )
    if job.kind == "document":
        return _JobSubmission(
            label=job.kind,
            chat_id=job.chat_id,
            worker=process_document_job,
            args=(
                services,
                job.chat_id,
                payload["document"],
                job.telegram_message_id,
                model_override,
            ),
        )
    return None
```

Ordinary-import `DurableJob as _DurableJob` in main.py.

- [x] **Step 7: Replace recovery row loop with JobService**

Compatibility wrapper:

```python
def dispatch_recovered_jobs(
    db,
    services,
    fields,
    *,
    recover_running=True,
) -> None:
    jobs = _jobs_for_services(services)
    jobs.recover(
        db,
        lambda job: resolve_recovered_job_submission(
            services,
            fields,
            job,
        ),
        recover_running=recover_running,
    )
```

Backlog dispatcher:

```python
def make_durable_backlog_dispatcher(services, fields):
    def dispatch():
        db = services.db_factory()
        try:
            jobs = _jobs_for_services(services)
            jobs.recover(
                db,
                lambda job: resolve_recovered_job_submission(
                    services,
                    fields,
                    job,
                ),
                recover_running=False,
            )
        finally:
            db.close()
    return dispatch
```

Production startup:

```python
services.jobs.recover(
    db,
    lambda job: resolve_recovered_job_submission(
        services,
        fields,
        job,
    ),
    recover_running=True,
)
```

Production must no longer use `dispatch_recovered_jobs`; that name remains compatibility-only.

- [x] **Step 8: Add source-boundary recovery guards**

In `tests/test_job_service.py`:

```python
def test_recovery_wrappers_do_not_own_raw_repository_loop(self):
    source = (
        Path(__file__).parents[1] / "bridge" / "main.py"
    ).read_text(encoding="utf-8")
    chunk = function_chunk(source, "def dispatch_recovered_jobs")
    self.assertNotIn("recover_jobs(", chunk)
    self.assertNotIn("json.loads(", chunk)
    self.assertIn(".recover(", chunk)


def test_job_service_is_ordinary_import_boundary(self):
    source = (
        Path(__file__).parents[1] / "bridge" / "job_service.py"
    ).read_text(encoding="utf-8")
    self.assertNotIn("bridge.runtime", source)
    self.assertNotIn("bridge.main", source)
    self.assertNotIn("process_message_job", source)
    self.assertNotIn("process_image_job", source)
```

Add a reusable `function_chunk` helper in that test module.

- [x] **Step 9: Verify Task 5 GREEN**

Run:

```bash
python -m unittest   tests.test_job_service   tests.test_job_service_workers   tests.test_composition   tests.test_audit_regressions   tests.test_background_lifecycle   tests.test_runtime_loader -v
```

Expected: all pass.

- [x] **Step 10: Commit Task 5**

Commit:

```text
refactor: recover durable jobs through JobService
```

---

### Task 6: Whole-phase verification, review, documentation, and PR

**Verified implementation evidence before final checklist commit:**
- RED CI: run #356 at `78e3f387ed7a150a68878cd7a0ba481dd6202dbb` failed as intended because `bridge.job_service` did not yet exist.
- GREEN integrated-code CI: run #375 at `0df40bf8303846dcfee23bcc6ff9482a1cff1bf2` passed compile, 469 unittest tests, pytest with 469 passed plus 103 subtests, and dependency audit with no known vulnerabilities.
- Source review: production `main()` has zero raw `enqueue_job` / `submit_durable_chat_job` calls and uses `services.jobs.enqueue/submit/recover`; migrated workers have zero raw `mark_job_running`, `finish_job`, or `job_actor_id` lifecycle calls.
- Upstream `main` remained at baseline `1dc67befe02bc84e49be224c40506d44a557e52c` during review.
- Final exact-head CI, PR review-state inspection, and Ready transition remain open until this checklist commit itself passes.


**Files:**
- Modify: `docs/superpowers/specs/2026-09-20-job-service-design.md` only if a genuine implementation ruling changes the approved design.
- Modify: `docs/superpowers/plans/2026-09-20-phase-5-job-service.md` only to mark checklist items backed by evidence.

**Interfaces:**
- Consumes the complete Phase 5E branch.
- Produces exact-head verification and a Ready-for-review PR; does not merge it.

- [x] **Step 1: Run focused Phase 5E verification**

Run:

```bash
python -m unittest   tests.test_job_service   tests.test_job_service_workers   tests.test_composition   tests.test_audit_regressions   tests.test_background_lifecycle   tests.test_runtime_loader   tests.test_database_optimization -v
```

Expected: zero failures/errors.

- [x] **Step 2: Run repository CI-equivalent suite**

Inspect `.github/workflows` first and use its exact commands. The expected current equivalents are:

```bash
python -m compileall bridge tests
python -m unittest discover -s tests -v
python -m pytest -q
pip-audit
```

Do not substitute a smaller suite for the workflow.

- [x] **Step 3: Review the whole branch against baseline**

Compare final branch head against:

```text
1dc67befe02bc84e49be224c40506d44a557e52c
```

Review specifically for:

- any durable update-loop path still calling raw `enqueue_job` or raw submission;
- any worker still calling raw `mark_job_running`, `finish_job`, or `job_actor_id` where JobService owns lifecycle;
- rejected admission accidentally transitioning queued jobs;
- recovery paths that drop malformed/unsupported jobs without final state;
- recovered job args containing job_id twice;
- stored-model or `resolve_active` regressions;
- native-edit `local_committed` failure becoming failed;
- per-chat/background executor changes outside scope;
- new runtime stage, public override, `_ORIGINAL_*` capture, global locator, Job schema/migration, or unrelated Phase 6 work.

For every Critical/Important finding, add a failing regression first, verify RED, fix minimally, and rerun focused/full suites.

- [x] **Step 4: Verify source boundaries**

Search the migrated functions specifically:

- `main()`
- `process_message_job`
- `process_image_job`
- `process_callback_job`
- `process_edit_job`
- `process_voice_job`
- `process_document_job`
- `dispatch_recovered_jobs`
- `make_durable_backlog_dispatcher`

Expected:
- production intake uses `services.jobs.enqueue/submit`;
- worker lifecycle uses JobService;
- recovery loops use JobService;
- raw database job functions remain only repository/compatibility-construction concerns.

Also verify `job_service.py` is absent from `DEFAULT_RUNTIME_STAGES`.

- [x] **Step 5: Update verified checklist state**

Mark plan items `[x]` only when test/CI evidence exists. Update the approved spec only for genuine design rulings.

Commit:

```text
docs: close verified Phase 5E checklist
```

- [ ] **Step 6: Create/update Draft PR and obtain exact-final-head CI**

Open against `cepeter/SillyTavern-Telegram-Bridge:main` with:

```text
refactor: extract durable JobService
```

The PR body must state:

- durable enqueue/submission/start/final/recovery orchestration is owned by JobService;
- business workers remain outside JobService;
- update-loop durable paths use `services.jobs`;
- startup/backlog recovery use the same injected service;
- rejected background admission remains queued/recoverable;
- operation/idempotency and native-edit local-commit behavior remain intact;
- BackgroundRuntime ordering/shutdown internals were not redesigned;
- `job_service.py` remains outside runtime stages;
- exact final head SHA and actual CI totals;
- design and plan paths.

A Draft PR may be opened earlier for RED evidence but remains Draft until the exact final head passes.

- [ ] **Step 7: Inspect exact-head CI and PR review state**

For the workflow attached to the exact final SHA, verify:
- compile succeeds,
- unittest passes with actual count,
- pytest passes with actual test/subtest counts,
- dependency audit reports no known vulnerabilities.

Also inspect:
- mergeability,
- PR comments,
- review threads,
- formal reviews,
- upstream main drift from the Phase 5E baseline.

Do not reuse an earlier green run.

- [ ] **Step 8: Mark Ready only after exact-head GREEN**

Only after exact-head CI is green, the branch remains compatible with current upstream, and there is no unresolved Critical/Important issue, mark the PR Ready for maintainer review.

Do not merge it.

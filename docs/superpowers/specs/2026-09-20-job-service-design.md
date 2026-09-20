# Phase 5E JobService Design

Date: 2026-09-20
Status: Proposed implementation design for Phase 5E
Parent: docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md
Baseline: upstream main at 1dc67befe02bc84e49be224c40506d44a557e52c
Predecessor: Phase 5D SyncService merged in PR #39.

## 1. Purpose

Phase 5E extracts durable background-job application orchestration behind one ordinary-import JobService while preserving the current SQLite durability, per-chat ordered execution, startup recovery, backlog recovery, operation-idempotency behavior, worker-specific recovery behavior, and shutdown semantics.

The phase establishes one application owner for durable job lifecycle and dispatch without turning JobService into a generation/media/controller god-service and without redesigning the background executor subsystem.

## 2. Current architecture

Durable job behavior is currently split across several modules:

- bridge/database.py owns durable job repository/state-transition primitives:
  - enqueue_job
  - job_actor_id
  - mark_job_scheduled
  - mark_job_running
  - finish_job
  - recover_jobs
- bridge/common.py owns the in-memory background executor and per-chat ordered admission:
  - submit_background
  - submit_chat_background
  - background_jobs_accepting
  - register_durable_backlog_dispatcher
  - begin_background_shutdown
  - drain_background_jobs
  - chat_job_lock
- bridge/main.py owns:
  - submit_durable_chat_job
  - dispatch_recovered_jobs
  - make_durable_backlog_dispatcher
  - process_message_job
  - process_callback_job
  - process_edit_job
  - process_image_job
  - the Telegram update-loop enqueue/dispatch decisions
  - startup recovered-job dispatch
- bridge/media.py / bridge/help.py own voice/document worker business behavior.
- recovery/operation functions continue to own command/edit/generation idempotency and local-commit recovery semantics.

The result is that durable lifecycle decisions are distributed across repository primitives, update-loop code, worker wrappers, startup recovery, and background admission.

## 3. Existing guarantees that must remain unchanged

Phase 5E must preserve these behaviors:

1. A durable jobs row is persisted before background execution is attempted.
2. enqueue_job also records processed_updates so a handed-off Telegram update is not duplicated.
3. Background admission rejection does not lose the durable job; the row remains queued for later recovery.
4. Accepted background admission moves a queued job to scheduled.
5. Worker start accepts queued or scheduled jobs, moves them to running, and increments attempts once.
6. A successfully completed worker moves the job to done.
7. A failed worker moves the job to failed and stores a truncated error message.
8. SQLite busy/locked failure while finishing a job retains the current best-effort failure behavior rather than crashing a worker solely because final-state persistence was contended.
9. Per-chat in-memory ordering remains unchanged.
10. Queue/semaphore capacity behavior remains unchanged.
11. Background shutdown rejects new in-memory work while leaving durable rows recoverable in SQLite.
12. Startup recovery requeues running/scheduled jobs and dispatches queued work in creation order.
13. Non-startup backlog recovery is bounded to the existing limit.
14. Recovered jobs preserve their stored model.
15. resolve_active payload semantics remain unchanged.
16. Unsupported recovered job kinds become failed rather than silently disappearing.
17. A worker DB startup failure leaves a durable job recoverable.
18. Callback operation-idempotency still prevents duplicate callback application.
19. Existing generation/edit/recovery operation phases remain unchanged.
20. A locally committed native edit whose external delivery failed still resolves through the existing special recovery rule.
21. Background worker DB handles remain isolated and are closed by their worker.
22. Startup/shutdown draining and maintenance ordering remain unchanged.

## 4. Architectural choice

Phase 5E uses a lifecycle-owner design with an injected worker resolver.

Rejected alternatives:

- Thin facade: wrapping repository functions without moving submission/recovery ownership would leave the application workflow scattered.
- Full worker owner: moving message/image/document/callback/edit business logic into JobService would couple durable persistence to Telegram, media, generation, Persona, Memory, and document-processing concerns.

Selected model:

    Telegram/update adapter
            |
            v
        JobService
      enqueue + submit
            |
            v
     BackgroundRuntime
            |
            v
    existing worker function
            |
       JobService.start
            |
      business workflow
            |
    JobService.complete/fail

Startup/backlog:

    startup/backlog trigger
            |
            v
      JobService.recover
            |
            v
       injected resolver
       DurableJob -> JobSubmission
            |
            v
     BackgroundRuntime

## 5. Service responsibilities

Create bridge/job_service.py as an ordinary Python module.

JobService owns application orchestration for:

- durable enqueue,
- background admission,
- scheduled/running/final state transitions,
- actor lookup,
- recovery iteration,
- recovered job decode,
- resolver invocation,
- unsupported/recovery-error finalization.

JobService does not own:

- Telegram parsing,
- generation,
- callback business logic,
- image/voice/document processing,
- edit semantics,
- chat locks,
- executor pools,
- semaphores,
- operation phase logic,
- failed-turn logic,
- schema creation.

## 6. Public value types

Use immutable ordinary-import dataclasses.

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

DurableJob is the decoded application representation of a recoverable repository row.

JobSubmission describes how the already-durable job should enter BackgroundRuntime.

## 7. Proposed JobService interface

Conceptually:

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
            db,
            update_id,
            chat_id,
            session_id,
            telegram_message_id,
            kind,
            payload,
        ) -> int: ...

        def submit(
            self,
            db,
            job_id,
            submission: JobSubmission,
        ) -> bool: ...

        def start(self, db, job_id) -> bool: ...
        def complete(self, db, job_id) -> bool: ...
        def fail(self, db, job_id, error) -> bool: ...
        def actor_id(self, db, job_id) -> str: ...

        def recover(
            self,
            db,
            resolver,
            *,
            recover_running: bool = True,
        ) -> None: ...

Exact callable annotations may be narrowed during implementation, but this application ownership is binding.

## 8. Enqueue semantics

JobService.enqueue delegates to the durable repository enqueue primitive.

The repository remains responsible for the atomic durable handoff semantics currently implemented by enqueue_job:

- insert-or-reuse one jobs row keyed by update_id,
- ensure processed_updates contains the update,
- commit before returning,
- return the existing job_id for duplicate update_id.

Phase 5E does not move that SQL into JobService.

## 9. Submission semantics

JobService.submit owns the current submit_durable_chat_job application behavior.

Given an existing durable job and a JobSubmission:

    accepted = submit_chat(
        submission.label,
        submission.chat_id,
        submission.worker,
        *submission.args,
        job_id,
    )

If accepted:

- call schedule_backend(db, job_id),
- return True.

If rejected:

- do not mark scheduled,
- do not mark failed,
- leave the job queued,
- return False.

This preserves recovery after shutdown, queue saturation, executor refusal, or temporary admission pressure.

## 10. Worker lifecycle semantics

Existing business worker functions remain outside JobService.

Workers use JobService for durable state decisions:

    if job_id is not None and not services.jobs.start(db, job_id):
        return

    ... business workflow ...

    if job_id is not None:
        services.jobs.complete(db, job_id)

    ... exception path ...

    if job_id is not None:
        services.jobs.fail(db, job_id, exc)

JobService.start delegates to mark_job_running.

JobService.complete delegates to finish_job(..., "done").

JobService.fail delegates to finish_job(..., "failed", str(error)).

Worker-specific business decisions remain outside the service. For example, native_edit_committed_after_failure still decides whether an edit exception should be treated as done; the worker then calls complete or fail accordingly.

## 11. Actor identity

JobService.actor_id delegates to the existing repository actor lookup.

process_message_job and process_callback_job may use services.jobs.actor_id instead of job_actor_id directly.

Payload interpretation for actor_id remains the repository primitive's concern in Phase 5E.

## 12. Recovery semantics

JobService.recover owns the recovery loop.

It calls recover_backend(db, recover_running=...) and converts every repository row to DurableJob.

Payload JSON decoding belongs inside the service because recovery orchestration should receive a stable decoded application object.

For each recovered DurableJob:

1. call the injected resolver(job),
2. if the resolver returns JobSubmission, call self.submit(...),
3. if resolver returns None, mark the durable job failed with "unsupported recovered job kind",
4. if payload decode or resolver execution raises, mark the durable job failed with the exception text and log the recovery error,
5. if background submission returns False, leave the job queued and log that it remains recoverable.

Recovery must not duplicate worker-specific business logic.

## 13. Worker resolver boundary

The resolver stays outside JobService because it knows concrete worker functions and BridgeServices.

Preferred shape in main.py or a small ordinary-import adapter:

    def resolve_recovered_job_submission(
        services,
        fields,
        job: DurableJob,
    ) -> JobSubmission | None:
        ...

Mapping remains behaviorally identical:

- generation / command -> process_message_job
- callback -> process_callback_job
- edit -> process_edit_job
- voice -> process_voice_job
- image -> process_image_job
- document -> process_document_job
- unknown -> None

The resolver preserves:

- stored model selection,
- resolve_active behavior,
- stored callback payload,
- image caption/file_size,
- document payload,
- message/session IDs.

## 14. Compatibility wrappers

Phase 5E should preserve existing public helper names temporarily for direct tests and compatibility-runtime callers:

- submit_durable_chat_job
- dispatch_recovered_jobs
- make_durable_backlog_dispatcher

These wrappers become thin delegates around JobService instead of owning lifecycle logic.

They must not become a new runtime override layer.

Where an injected services.jobs exists, use it.

For direct legacy callers without services.jobs, a late-bound compatibility JobService may be constructed from the final repository/background collaborators only if required by existing direct callers/tests.

Do not introduce a global current-service locator.

## 15. Composition

Extend BridgeServices with:

    jobs: JobService | None = None

Extend build_bridge_services with an optional JobService.

Production startup constructs JobService from:

- enqueue_job,
- job_actor_id,
- mark_job_scheduled,
- mark_job_running,
- finish_job,
- recover_jobs,
- BackgroundRuntime.submit_chat.

Because these primitives are repository/background capabilities rather than late safety overrides, no new staged runtime module is needed.

job_service.py must remain outside DEFAULT_RUNTIME_STAGES.

## 16. Telegram update-loop integration

The Telegram polling adapter continues deciding what job should exist.

It still determines:

- kind,
- payload,
- chat/session/message identifiers,
- worker-specific user-facing queued/recoverable messages.

But durable lifecycle goes through services.jobs.

Conceptually:

    job_id = services.jobs.enqueue(
        db,
        update_id,
        chat_id,
        session_id,
        message_id,
        "image",
        payload,
    )

    queued = services.jobs.submit(
        db,
        job_id,
        JobSubmission(
            label="image",
            chat_id=chat_id,
            worker=process_image_job,
            args=(services, ...),
        ),
    )

This applies to:

- callback,
- edit,
- voice,
- image,
- document,
- command,
- generation.

The update loop no longer directly calls raw enqueue_job or mark_job_scheduled for those paths.

## 17. Startup recovery

Startup recovery uses JobService.recover with recover_running=True.

The existing startup behavior remains:

- scheduled/running rows are requeued,
- queued jobs are returned oldest-first,
- startup recovery is bounded by the current repository behavior,
- every valid recovered job is submitted through the same JobService admission path.

The service does not own application startup timing; main.py decides when startup recovery runs.

## 18. Backlog recovery

make_durable_backlog_dispatcher becomes a thin closure that:

1. opens one services.db_factory connection,
2. calls services.jobs.recover(..., recover_running=False),
3. closes the connection.

The existing BackgroundRuntime.register_backlog_dispatcher mechanism remains unchanged.

The existing bounded non-startup backlog behavior remains unchanged.

## 19. BackgroundRuntime relationship

Phase 5E does not redesign BackgroundRuntime.

The following stay infrastructure concerns:

- generation/utility executor selection,
- bounded semaphores,
- per-chat queue structures,
- _CHAT_ACTIVE and _CHAT_IN_FLIGHT,
- submit_chat_background ordering,
- tracked futures,
- shutdown admission control,
- backlog callback registration,
- executor shutdown/drain.

JobService receives submit_chat as a capability and does not import those globals.

## 20. Database/repository relationship

Phase 5E leaves the existing SQL primitives in database.py.

The service composes repository primitives; it does not duplicate their SQL.

No new schema creation, migration, transaction ownership, or request-path DDL is introduced.

Versioned schema/migrations remain authoritative for job-table structure.

## 21. Operation/recovery relationship

JobService does not absorb operation-phase/idempotency rules.

These stay with their existing workflows:

- begin_operation,
- operation_phase,
- operation_was_applied,
- record_operation,
- local_committed phases,
- recovery_delivery phases,
- regen/continue/edit recovery.

The job_id continues to act as operation_id where currently required.

Callback/generation/edit workers keep their current operation guards.

## 22. Failure semantics

The service preserves the distinction between durable lifecycle failure and business failure.

Background admission failure:

- job remains queued,
- return False,
- do not mark failed.

Worker business exception:

- worker decides any domain-specific recovery rule,
- otherwise call JobService.fail.

Unsupported recovered job kind:

- JobService marks failed exactly once.

Recovery payload/resolver exception:

- mark failed,
- log,
- continue to the next recovered job.

Final-state SQLite lock/busy behavior remains whatever finish_job currently provides.

## 23. Ordinary-import/runtime relationship

bridge/job_service.py must never be listed in DEFAULT_RUNTIME_STAGES.

Phase 5E must not add:

- a runtime stage,
- a public-callable override,
- an _ORIGINAL_* capture,
- a global JobService locator.

Existing compatibility-runtime modules may temporarily expose wrapper names that delegate into JobService.

## 24. Testing strategy

Use strict RED -> GREEN TDD.

Add JobService unit tests for:

- enqueue delegates and returns job_id,
- accepted submit schedules exactly once,
- rejected submit leaves state untouched,
- start delegates and preserves True/False,
- complete writes done,
- fail writes failed and error text,
- actor_id delegates,
- recover decodes a DurableJob,
- recover submits valid jobs,
- unsupported kind/resolver None fails exactly once,
- malformed payload/resolver exception fails and continues,
- rejected recovered submission remains queued.

Add composition/runtime tests proving:

- BridgeServices.jobs injection,
- startup constructs JobService from the expected primitives and injected BackgroundRuntime,
- job_service.py is not a runtime stage,
- Phase 5 extracted-service source guard now stops at JobService.

Add worker tests proving:

- message, callback, edit, image, voice, and document workers use services.jobs.start/complete/fail rather than raw state-transition calls,
- message/callback actor lookup uses JobService where migrated,
- locally committed edit exception still completes the job,
- callback operation-applied fast path still completes once.

Add update-loop tests proving:

- callback, edit, voice, image, document, command, and generation paths enqueue/submit through services.jobs,
- rejected admission keeps user-facing "saved for ... after restart" semantics where currently present,
- normal accepted admission keeps existing queued messages.

Add recovery tests proving:

- startup calls services.jobs.recover(..., recover_running=True),
- backlog dispatcher calls services.jobs.recover(..., recover_running=False),
- stored model is preserved,
- resolve_active behavior is preserved,
- unsupported job kinds fail,
- startup recovery remains bounded,
- worker DB startup failure leaves the job queued.

Retain existing background-lifecycle tests for:

- tracked-future draining,
- shutdown blocking new dispatch,
- per-chat ordered execution,
- executor/semaphore admission limits.

Add source-boundary regression guards so migrated main.py update/recovery paths do not directly orchestrate raw enqueue/schedule/recover state transitions where JobService owns the use case.

The complete exact-head CI suite remains the release gate.

## 25. Out of scope

Phase 5E does not:

- move process_message_job, process_image_job, process_voice_job, process_document_job, process_callback_job, or process_edit_job business logic into JobService,
- redesign BackgroundRuntime,
- change queue/semaphore limits,
- change per-chat ordering,
- change job schema,
- change processed_updates semantics,
- redesign failed-turn retry,
- redesign operation phases,
- remove chat_job_lock,
- change graceful shutdown/drain behavior,
- create a new scheduler,
- remove compatibility runtime loading.

## 26. Acceptance criteria

Phase 5E is complete when:

- JobService is ordinary-importable,
- durable enqueue/submission/state/recovery orchestration has one application-service owner,
- production update-loop durable job paths use services.jobs,
- production workers use services.jobs for lifecycle transitions,
- startup and backlog recovery use services.jobs.recover,
- concrete business workers remain outside JobService,
- rejected background admission leaves jobs queued,
- startup/recovery/model/resolve_active/idempotency semantics remain unchanged,
- locally committed edit recovery remains unchanged,
- BackgroundRuntime queue/order/shutdown behavior remains unchanged,
- no new runtime stage, override, _ORIGINAL_* capture, or global service locator is introduced,
- job_service.py remains outside runtime stages,
- focused job/composition/background/recovery tests pass,
- the complete exact-head CI suite passes.

# Phase 5D SyncService Design

Date: 2026-09-20
Status: Proposed implementation design for Phase 5D
Parent: docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md
Baseline: upstream main at 470bc4e28420e097f47fcd336abe5c94903f180f

## 1. Purpose

Phase 5D extracts Live API Sync application orchestration behind one ordinary-import SyncService while preserving the current SillyTavern API transport, transcript/checkpoint algorithms, realtime polling safeguards, conflict handling, retry/backoff behavior, and state-integrity hardening.

The phase establishes one application owner for Live Sync use cases without prematurely performing the Phase 6 migration that removes late runtime safety overrides.

## 2. Current architecture

Live Sync is distributed across several compatibility-runtime layers:

- bridge/sync_core.py owns transcript/checkpoint primitives such as sync_binding, build_sync_records, apply_sync_snapshot, and set_sync_state.
- bridge/sync_api.py owns loopback HTTP transport, API configuration, phase3_sync_now, phase3_toggle_realtime, raw polling, and worker lifecycle.
- bridge/recovery.py owns Live Sync status text, Telegram menu, and callback orchestration as recovery-stage overrides.
- bridge/sync_safety.py late-overrides phase3_sync_poll and captures _ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL to add chat-lock and durable-job exclusion, bounded fairness, and hardened failure handling.
- bridge/state_integrity.py late-overrides apply_sync_snapshot so explicit Persona/world clears are respected and Hindsight is refreshed after imports.
- bridge/main.py starts and stops the realtime worker directly.
- command and callback routers invoke the current runtime Sync functions directly.

The application workflow is therefore split across UI, transport, recovery, safety, and startup lifecycle code.

## 3. Reliability behavior that must remain unchanged

Phase 5D preserves all current Sync guarantees:

1. Only loopback SillyTavern API origins are accepted.
2. CSRF, cookie login, timeout, redirect, payload-size, and allowed-route protections remain unchanged.
3. A missing remote chat is created from bridge state.
4. Remote-only changes import when local state still matches the checkpoint.
5. Local-only changes export when remote state still matches the checkpoint.
6. Sync ID mismatch stops realtime sync.
7. Initial divergence without a common checkpoint stops realtime sync.
8. Concurrent local and remote changes create a conflict and stop realtime sync.
9. Manual API or validation failures disable realtime sync and return the existing Live API unavailable result instead of escaping through the callback path.
10. Realtime polling skips a chat when its Telegram chat lock is held or when a queued, scheduled, or running durable job exists.
11. Polling scans past locked candidates, attempts at most 32 eligible bindings per cycle, and prioritizes oldest last_checked_at.
12. Transient failures retain bounded exponential retry/backoff.
13. Non-transient failures and the fifth unexpected failure disable realtime sync.
14. SQLite-level worker failures may discard the current connection and reconnect on the next interval.
15. Session deletion and startup cleanup continue removing orphaned sync_bindings without request-time structural DDL.
16. Imported snapshots still traverse the final hardened apply_sync_snapshot path, including explicit Persona/world clears and Hindsight refresh.
17. Startup and shutdown preserve worker lifecycle and graceful stop behavior.

## 4. Target boundary

Target dependency flow:

    Telegram command / callback adapters        realtime worker
                    |                                |
                    +---------------+----------------+
                                    |
                                    v
                               SyncService
                                    |
                    +---------------+---------------+
                    |               |               |
                    v               v               v
                 status          sync_now        poll/toggle
                    |               |               |
                    +---------------+---------------+
                                    |
                                    v
                         existing Sync backend
                       sync_core + sync_api
                                    |
                         +----------+----------+
                         |                     |
                         v                     v
                    sync_safety          state_integrity

SyncService owns application decisions. HTTP mechanics, transcript serialization, hashing, low-level import mutation, polling safety implementation, and runtime hardening remain backend concerns in Phase 5D.

## 5. Service responsibilities

Create bridge/sync_service.py as an ordinary Python module.

SyncService owns:

- structured Live Sync status for one session,
- one manual synchronization,
- realtime toggle,
- one realtime polling cycle,
- one stable boundary for UI, callback, startup, and worker callers.

The service must not import Telegram modules, bridge.runtime, filesystem paths, or SillyTavern HTTP implementation details.

## 6. Proposed public types

Use immutable ordinary-import dataclasses.

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
        load_binding: Callable
        count_messages: Callable
        sync_now_backend: Callable
        toggle_realtime_backend: Callable
        poll_backend: Callable
        disable_realtime: Callable
        api_configured: Callable
        expected_errors: tuple[type[Exception], ...]

        def status(self, db, chat_id, session_id) -> SyncStatus: ...
        def sync_now(self, db, chat_id, session_id) -> str: ...
        def toggle_realtime(self, db, chat_id, session_id) -> str: ...
        def poll(self, db) -> None: ...

Exact annotations may be narrowed during implementation, but this use-case ownership is binding.

## 7. Status semantics

SyncService.status returns structured application data, not Telegram markup.

It reads the current Sync binding, the session message count, and whether the API is configured.

Telegram presentation remains responsible for formatting session ID, message count, sync ID, last direction/timestamp or never, realtime on/off, and API configured/not configured.

Status rendering must no longer own Sync application decisions.

## 8. Manual sync semantics

SyncService.sync_now owns the application failure behavior currently embedded in handle_sync_callback.

Normal path:

    sync_now_backend(...)
        -> return backend result unchanged

Expected API or validation failure:

    expected error
        -> disable_realtime(db, chat_id, session_id, str(error))
        -> return "Live API unavailable: <error>"

Unexpected programming/runtime failures are not converted into successful result strings.

## 9. Realtime toggle semantics

SyncService.toggle_realtime delegates to the final realtime-toggle backend.

The existing backend continues to own:

- API configuration checks,
- initial sync-before-enable,
- disabling on API/validation failures,
- conflict/divergence stop behavior,
- enabling only after an eligible successful initial sync.

Phase 5D does not duplicate those rules.

## 10. Polling semantics

SyncService.poll delegates to the final hardened polling backend.

Production construction must happen after runtime safety stages have loaded so the injected collaborator is the sync_safety.py phase3_sync_poll, not the raw implementation from sync_api.py.

The following remain backend behavior in Phase 5D:

- non-blocking chat-lock acquisition,
- durable-job exclusion,
- oldest-binding ordering,
- scanning beyond locked candidates,
- 32 eligible attempts per polling cycle,
- transient exponential retry/backoff,
- disable after terminal or fifth failure,
- lock release on all paths.

## 11. Compatibility service

Direct legacy callers and tests that do not receive BridgeServices must still cross the same Sync application boundary.

The preferred compatibility constructor lives in an existing staged Sync module, normally bridge/sync_api.py.

Conceptually:

    def compatibility_sync_service():
        return SyncService(
            load_binding=sync_binding,
            count_messages=count_session_messages,
            sync_now_backend=phase3_sync_now,
            toggle_realtime_backend=phase3_toggle_realtime,
            poll_backend=phase3_sync_poll,
            disable_realtime=_phase3_disable,
            api_configured=phase3_api_configured,
            expected_errors=(SillyTavernApiError, ValueError),
        )

    def resolve_sync_service(sync_service=None):
        return sync_service if sync_service is not None else compatibility_sync_service()

The collaborator names must be resolved inside the compatibility constructor at call time. This preserves final late-stage replacements rather than capturing pre-safety functions.

## 12. Repository primitive

Add a narrow read-only repository helper for the status message count if no equivalent helper exists:

    count_session_messages(db, chat_id, session_id) -> int

Requirements:

- read-only,
- no schema creation,
- no commit or rollback,
- no transaction ownership.

If equivalent behavior already exists at implementation time, reuse it instead of adding a duplicate.

## 13. Composition

Extend BridgeServices with:

    sync: SyncService | None = None

Extend build_bridge_services with an optional Sync service argument.

_build_startup_services constructs the production SyncService from the final runtime-resolved collaborators after all staged overrides have loaded.

The production service must receive the final versions of sync_binding, phase3_sync_now, phase3_toggle_realtime, phase3_sync_poll, _phase3_disable, and phase3_api_configured.

No global current-service locator is introduced.

## 14. UI and callback propagation

Telegram presentation remains outside SyncService.

Keep in UI/runtime presentation code:

- panel text formatting,
- inline keyboard construction,
- callback answering,
- panel close and refresh behavior.

Application calls change so:

- /sync resolves or receives the Sync service,
- sync:status uses SyncService.status,
- sync:now uses SyncService.sync_now,
- sync:realtime uses SyncService.toggle_realtime.

Production callback processing already receives BridgeServices; Phase 5D propagates services.sync to the Sync callback path in the same style as services.persona.

Production command routing similarly passes services.sync to the Sync menu/status path.

Legacy direct callers resolve the compatibility service.

## 15. Realtime worker integration

The worker must no longer bypass the application service.

Worker lifecycle functions may remain in sync_api.py during Phase 5D, but the loop executes polling through an injected or resolved SyncService.

Preferred shape:

    def _phase3_worker_loop(sync_service=None):
        sync_service = resolve_sync_service(sync_service)
        ...
        sync_service.poll(db)

    def start_phase3_sync_worker(*, sync_service=None):
        ...

Production startup calls:

    start_phase3_sync_worker(sync_service=services.sync)

Shutdown may remain stop_phase3_sync_worker because stopping an already-owned worker is lifecycle plumbing rather than a Sync application decision.

A legacy start with no injected service resolves the compatibility service.

## 16. Relationship to recovery.py

recovery.py currently defines sync_status_text, send_sync_menu, and handle_sync_callback as public-callable recovery-stage overrides.

Phase 5D routes their application decisions through SyncService.

It does not have to remove the recovery-stage function definitions or their override allowlist entries because that would expand scope into Phase 6 or 7.

However:

- these functions must no longer directly invoke raw phase3_sync_now, phase3_toggle_realtime, or _phase3_disable for migrated use cases;
- status rendering must consume SyncService.status;
- callbacks must use the injected or resolved service.

## 17. Relationship to sync_safety.py

Phase 5D does not delete or rewrite sync_safety.py.

The existing _ORIGINAL_PHASE3_SYNC_NOW_FOR_POLL capture and late phase3_sync_poll override remain temporarily because they carry established safety behavior.

Phase 6 owns converting this into an explicit collaborator or decorator boundary such as:

    SyncBackend / Poller
            |
            v
    JobAwareSafeSyncPoller
            |
            v
    SillyTavernSyncBackend

Phase 5D only requires production callers to reach the final hardened backend through SyncService.

## 18. Relationship to state_integrity.py

Phase 5D does not remove or rewrite the apply_sync_snapshot late override.

Remote imports must continue through the final hardened implementation that provides:

- explicit Persona clear propagation,
- explicit world-file clear propagation,
- Hindsight refresh after import,
- existing failure isolation for memory refresh.

Tests must prove that service-backed production Sync still reaches this final import path.

## 19. Ordinary-import/runtime relationship

sync_service.py must never be listed in DEFAULT_RUNTIME_STAGES.

The following remain staged compatibility modules in Phase 5D:

- sync_core.py,
- sync_api.py,
- recovery.py,
- sync_safety.py,
- state_integrity.py.

Phase 5D must not add:

- a new runtime stage,
- a new public-callable override,
- another _ORIGINAL_* capture,
- a global service locator.

## 20. Failure and transaction semantics

The service does not redesign backend transaction ownership in Phase 5D.

Existing Sync commit/rollback semantics stay unchanged unless a focused regression test proves a service-level requirement demands otherwise.

Unexpected programming errors must not be hidden behind generic success text.

Current user-visible result strings remain unchanged, including created, unchanged, imported, exported, mismatch, divergence, conflict, realtime enabled/disabled/not configured, realtime API unavailable, and manual Live API unavailable results.

## 21. Testing strategy

Use strict RED to GREEN TDD.

Add SyncService unit tests for:

- structured status data,
- status message count,
- configured/unconfigured API flag,
- successful manual sync delegation,
- expected manual API error disables realtime and returns current feedback,
- unexpected manual exception propagates,
- realtime toggle delegates and preserves result,
- poll delegates to the injected backend.

Add compatibility/composition tests proving:

- BridgeServices.sync is injected,
- startup constructs SyncService from final runtime collaborators,
- compatibility construction resolves collaborators late,
- compatibility polling sees the final hardened phase3_sync_poll,
- sync_service.py is not a runtime stage.

Add command/callback tests proving:

- /sync receives the injected service,
- status rendering consumes SyncService.status,
- sync:now calls SyncService.sync_now rather than raw phase3_sync_now,
- sync:realtime calls SyncService.toggle_realtime,
- legacy direct callers still cross the compatibility service.

Add worker tests proving:

- production startup passes services.sync into the realtime worker,
- the worker calls SyncService.poll,
- direct legacy worker startup resolves compatibility service.

Retain existing hardening regressions for:

- chat-lock exclusion,
- durable-job exclusion,
- lock release after job-query failure,
- scan past 32 locked candidates,
- oldest-binding fairness,
- fifth-failure disable,
- API auth failure disable,
- round-trip import/export/conflict stop,
- orphan binding cleanup.

Add source-boundary tests so migrated UI/worker functions do not directly call raw phase3_sync_now, phase3_toggle_realtime, phase3_sync_poll, or _phase3_disable where SyncService owns the use case.

Add an integration regression proving a service-backed remote import still executes the final hardened apply_sync_snapshot behavior.

The complete exact-head CI suite remains the release gate.

## 22. Out of scope

Phase 5D does not:

- remove sync_safety.py,
- remove state_integrity.py Sync hardening,
- remove recovery.py runtime override entries,
- redesign the SillyTavern API protocol,
- change loopback-only security policy,
- change Sync conflict policy,
- change retry/backoff constants,
- change the 32-attempt fairness bound,
- redesign the Sync Telegram UI,
- replace the realtime worker with the durable Telegram job scheduler,
- remove the compatibility runtime loader.

## 23. Acceptance criteria

Phase 5D is complete when:

- SyncService is ordinary-importable,
- manual sync, realtime toggle, status, and polling have one application-service owner,
- production /sync and Sync callbacks use the injected service,
- the production realtime worker polls through the injected service,
- direct legacy callers use a late-bound compatibility service,
- current loopback/API/client behavior is unchanged,
- current chat-lock/job exclusion, fairness, retry/backoff, and terminal-disable semantics remain effective,
- current hardened apply_sync_snapshot behavior remains effective,
- no new runtime stage, override, or _ORIGINAL_* capture is introduced,
- sync_service.py is outside runtime stages,
- focused Sync, safety, composition, callback, and runtime tests are green,
- the complete exact-head CI suite passes.

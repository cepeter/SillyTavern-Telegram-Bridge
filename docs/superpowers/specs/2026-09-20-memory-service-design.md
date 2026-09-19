# Memory Application Service — Design

Date: 2026-09-20
Status: Phase 5B implementation design
Parent architecture: `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`
Migration phase: Phase 5 — Application service extraction
Repository: `cepeter/SillyTavern-Telegram-Bridge`
Baseline: synchronized upstream/fork `main` at `845d057e231f57fbdf20b1fcdf6b1de29e4f0ab0`

## 1. Purpose

Extract the conversation-memory application workflow from the compatibility runtime into one ordinary-import `MemoryService` without changing user-visible behavior.

The service becomes the canonical application owner of:

- assembling memory context for model prompts,
- combining Hindsight recall with continuity summaries,
- suppressing a stale summary when regenerating an edited turn already covered by that summary,
- requesting post-turn memory retention,
- purging remote/session memory through the currently active guarded backend.

The existing staged memory functions remain transitional backend and compatibility collaborators.

## 2. Constraints

This slice must:

- preserve current Hindsight recall, retention, purge, and summary behavior,
- preserve the stale-retain and purge invalidation behavior currently supplied by `state_integrity.py`,
- preserve Memory Curator and Scene State hook behavior,
- preserve recovery behavior for `/regen`, `/continue`, and edited-turn regeneration,
- add no runtime override, service locator, or mutable active-service registry,
- keep `bridge/memory_service.py` out of `DEFAULT_RUNTIME_STAGES`,
- keep `memory.py`, `state_integrity.py`, and `memory_curator.py` staged in this slice,
- keep direct callers that do not supply `BridgeServices` working through legacy fallbacks,
- inject the production service at the existing composition root,
- make the service independently unit-testable with fake collaborators,
- leave MemoryBackend decorator extraction to Phase 6.

## 3. Service boundary

Create `bridge/memory_service.py` as an ordinary-import module.

The constructor receives explicit collaborators:

- recall-context provider,
- session-summary provider,
- summary-state reader,
- retention provider,
- purge provider.

Public interface:

```python
@dataclass(frozen=True)
class MemoryPromptContext:
    recall: str
    summary: str


@dataclass(frozen=True)
class MemoryService:
    def prompt_context(
        self,
        db,
        chat_id: str,
        session: dict[str, str],
        fields: dict[str, str],
        query: str,
        *,
        edited_user_rowid: int | None = None,
    ) -> MemoryPromptContext:
        ...

    def retain(
        self,
        db,
        chat_id: str,
        session: dict[str, str],
        fields: dict[str, str],
    ) -> None:
        ...

    def purge_session(
        self,
        db,
        chat_id: str,
        session_id: str,
    ) -> int:
        ...
```

The service must not import `bridge.runtime`.

## 4. Prompt-context semantics

For a normal prompt:

1. obtain Hindsight recall context,
2. obtain the current session continuity summary,
3. return both values unchanged.

For edited-turn regeneration:

1. obtain Hindsight recall context,
2. read the persisted summary state,
3. if `covered_until_rowid >= edited_user_rowid`, return an empty summary without calling the summary-generation provider,
4. otherwise obtain the normal summary for prompt.

This preserves the existing edit behavior and avoids regenerating a summary from transcript state that is about to be rewritten.

## 5. Retention and purge semantics

`MemoryService.retain()` delegates to the injected retention provider.

At production startup that provider resolves to the final staged `retain_session_memory` implementation, including the current `state_integrity.py` stale-snapshot protection and post-retain hooks.

`MemoryService.purge_session()` similarly delegates to the final staged `purge_hindsight_session` implementation so successful purges continue to clear mappings and increment the invalidation epoch.

The application service does not reproduce those backend algorithms.

## 6. Composition

Extend `BridgeServices` with an optional `memory: MemoryService | None` field during the compatibility period.

The startup composition root builds the production service from the runtime-resolved collaborators:

- `recall_memory_context`
- `session_summary_for_prompt`
- `get_session_summary`
- `retain_session_memory`
- `purge_hindsight_session`

Because `_build_startup_services()` executes after staged runtime composition has completed, the injected retain/purge functions preserve the late-loaded safety behavior.

No global `CURRENT_SERVICES`, getter, setter, or equivalent locator is introduced.

## 7. Message workflow

`process_message(..., services=services)` already carries the explicit service graph.

Phase 5B makes the ordinary generation path use `services.memory` when present for:

- prompt memory context,
- continuity summary,
- post-persist retention.

Direct legacy callers with no injected service keep the current calls to `recall_memory_context`, `session_summary_for_prompt`, and `retain_session_memory`.

The durable `/retry` recursion continues passing the same `services` object.

## 8. Recovery workflow

Recovery operations must use the same injected memory service:

- `/regen`
- `/continue`
- edited-turn regeneration.

For edited turns, the service receives `edited_user_rowid` and owns the stale-summary suppression decision.

Recovery helpers retain direct-call compatibility through optional service arguments and legacy fallback behavior.

## 9. Reset relationship

Active-session reset keeps its existing durable phase ordering.

Where an injected memory service is available, the purge step may delegate through `MemoryService.purge_session()`; local transcript/summary deletion and operation-phase transitions remain in the reset workflow because they are part of the durable reset transaction rather than the remote memory backend.

If wiring reset through the service would require changing callback/service propagation in this slice, the existing guarded purge call remains a compatibility path; behavior must not be weakened.

## 10. Runtime-loader relationship

`memory_service.py` is an ordinary Python module and must never be listed in `DEFAULT_RUNTIME_STAGES`.

The following remain staged compatibility modules:

- `memory.py`
- `state_integrity.py`
- `memory_curator.py`
- `scene_state.py`

Phase 6 will replace the stale-memory override with an explicit `MemoryBackend` decorator chain after equivalent regression coverage exists.

## 11. Testing

Add ordinary service unit tests with fake collaborators for:

- normal prompt context,
- edit-covered summary suppression,
- edit-not-covered summary use,
- retention delegation,
- purge delegation.

Add composition/integration tests proving:

- startup constructs `MemoryService`,
- production message generation uses the injected service,
- recovery generation uses the injected service,
- direct legacy callers remain supported,
- `memory_service.py` is not a runtime stage,
- Phase 5 still has no `PersonaService`, `SyncService`, or `JobService`.

Existing Hindsight cleanup, state-integrity, Memory Curator, Scene State, and full CI suites remain regression gates.

## 12. Acceptance criteria

Phase 5B is complete when:

- `MemoryService` is ordinary-importable,
- prompt-memory orchestration has one application-service owner,
- normal and recovery generation paths use the injected service,
- edited-turn stale-summary suppression is owned by the service,
- production retention/purge still preserve state-integrity protections,
- direct legacy calls continue to work,
- no new runtime stage or override is introduced,
- `memory_service.py` is outside runtime stages,
- all existing memory safety/curation tests remain green,
- the complete CI suite passes.

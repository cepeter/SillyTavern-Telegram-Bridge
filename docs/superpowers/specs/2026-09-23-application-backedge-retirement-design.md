# Application Back-Edge Retirement Design

Date: 2026-09-23
Status: approved for implementation
Base: `main` at `461d1525ecff673024987bc3be533c442aff1c23`
Parent: `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`

## 1. Purpose

Retire three application-to-higher-level dependency back edges that now act as a
single structural choke point after the ConversationService and callback-token
changes merged.

Fresh merged-main graph measurements are:

- 71 `bridge` modules;
- 420 internal import edges;
- largest strongly connected component: 27 modules;
- total cyclic modules: 27;
- 18 reciprocal pairs;
- 32 modules importing `bridge.telegram`.

No remaining single import edge reduces the largest SCC by more than one module.
The coordinated removal of these three edges is materially different:

- `persona_sync -> input_flows`
- `telegram -> commands`
- `telegram -> session_naming`

Removing all three in the graph model splits the 27-module SCC into cyclic
components of 12 and 11 modules. Removing any one, or any pair, leaves the SCC
at 27. Therefore they form one coordinated architecture wave rather than three
independent micro-PRs.

## 2. Goals

1. Remove all three back edges without compatibility aliases or dynamic imports.
2. Preserve Persona write serialization and integrity checks.
3. Preserve session-title normalization and validation byte-for-byte.
4. Move Telegram image execution upward so the Telegram module no longer calls
   application command/generation behavior.
5. Keep document PNG fallback behavior while making the application callback
   explicit instead of imported by the Telegram module.
6. Expand the existing injected `TelegramRuntime` only with the transport
   capability required by the image worker.
7. Achieve the measured SCC split without creating a newly cyclic module.
8. Keep user-visible commands, messages, persistence, durable jobs, and recovery
   semantics unchanged.

## 3. Non-goals

- no GroupService extraction in this PR;
- no ModelRouter/provider transport extraction;
- no `route_update()` decomposition;
- no broad Telegram/session repository rewrite;
- no global transaction-purity sweep;
- no fix to the separately identified streaming continuation defect;
- no compatibility re-export for moved symbols.

After this wave merges, the graph is re-measured before choosing the next cut.

## 4. Ownership changes

### 4.1 Persona edit serialization

`input_flows.py` currently defines `PERSONA_EDIT_LOCK` even though the UI flow
does not use it. The native Persona store in `persona_sync.py` consumes that
lock and startup composition imports it only to inject the same serialization
primitive into `PersonaService`.

The lock belongs with the native Persona persistence/integrity adapter it
protects.

Required result:

- `persona_sync.py` owns `PERSONA_EDIT_LOCK`;
- `_PERSONA_STORE` continues to use the same process-wide `threading.RLock`;
- `main.py` and native test composition import the lock from `persona_sync`;
- `input_flows.py` no longer owns or imports threading solely for that lock;
- `persona_sync.py` no longer imports `input_flows.py`.

No new lock, lock hierarchy, or concurrency behavior is introduced.

### 4.2 Session-title validation

`telegram.create_session()` imports `normalize_session_title()` from
`session_naming.py`, while `session_naming.py` is a higher-level UI/input flow
that also imports Telegram session operations.

Create a pure `bridge/session_titles.py` owner containing:

- `SESSION_TITLE_MAX_CHARS = 80`
- `normalize_session_title(value: str) -> str`

Both `telegram.py` and `session_naming.py` import from this pure owner.
Validation, whitespace normalization, slash rejection, exception type, and
message remain unchanged. `session_naming.py` does not re-export the symbol.

### 4.3 Telegram image/application boundary

`telegram.py` currently owns transport, file download, session operations, and
also calls `commands.process_image_message()`. That creates a low-level adapter
to application-workflow back edge.

The worker becomes the image application orchestration owner:

1. `process_image_job()` keeps durable-job/idempotency handling.
2. It rejects files over the existing `IMAGE_MAX_BYTES` limit with the existing
   user-visible message.
3. It downloads bytes through `services.telegram.download_file`.
4. It resolves the queued/current session as today.
5. It loads character fields.
6. It calls canonical `commands.process_image_message()` with the same memory,
   Persona, Group Director, model/API key, caption, Telegram message ID, and
   persisted session semantics.

`TelegramRuntime` gains one required transport callable:

```python
download_file: Callable[..., bytes]
```

Startup composes it from `telegram.download_telegram_file`. Tests receive an
explicit fake. The old `telegram.process_telegram_image()` owner is deleted.

### 4.4 Document PNG fallback

`telegram.import_telegram_document()` also calls `process_image_message()` when
an uploaded PNG is not a character card. The Telegram module must not retain
that application import after the worker migration.

Keep document classification/import in its existing owner for this wave, but
make the application dependency explicit:

```python
import_telegram_document(
    ...,
    api_key: str,
    process_image: Callable[..., None],
    ...
)
```

`help.process_document_job()` passes `services.config.api_key` and canonical
`commands.process_image_message`. The PNG fallback calls that injected
collaborator. The Telegram module no longer imports `bridge.commands` and no
longer reads `LLM_API_KEY` from ambient environment for this branch.

This is an intermediate explicit boundary, not the final document service.

## 5. Dependency direction

Before:

```text
input_flows <- persona_sync
session_naming <- telegram
commands <- telegram
```

After:

```text
persona_sync owns Persona edit lock
session_titles <- session_naming
session_titles <- telegram

TelegramRuntime.download_file
        |
        v
worker_orchestration -> commands.process_image_message

help.process_document_job
        |
        +-- explicit process_image collaborator
        v
telegram.import_telegram_document
```

The change must not replace the retired edges with dynamic imports, globals,
registries, or aliases.

## 6. Error and durability behavior

Image job start/complete/fail transitions remain in `process_image_job()`.
Existing committed-response recovery remains unchanged. Download/provider
exceptions continue to fail the durable job and produce the existing user-facing
image-processing failure message.

File-size rejection remains a normal handled return, not a durable job error.

Document job failures retain the existing generic document-import failure path.

Persona locking remains re-entrant and process-wide. No persistence commit moves
in this PR.

Session-title validation remains synchronous and deterministic.

## 7. Testing

TDD starts with architecture and behavior tests that fail on merged `main`.

Required architecture guards:

- `persona_sync` does not import `input_flows`;
- `input_flows` does not own `PERSONA_EDIT_LOCK`;
- `session_titles` is the sole owner of title normalization;
- `telegram` does not import `session_naming`;
- `telegram` does not import `commands`;
- `telegram` no longer owns `process_telegram_image`;
- `TelegramRuntime.download_file` is required by composition.

Required behavior coverage:

- Persona lock identity is shared by native store and composed PersonaService.
- Valid/invalid session title behavior is unchanged.
- Image worker rejects oversize input before download.
- Image worker downloads through the injected runtime and forwards the exact
  session/model/service/message identity to `process_image_message`.
- Existing committed image delivery bypass remains unchanged.
- Document PNG fallback forwards the explicit API key and image collaborator.
- Character-card PNG and Data Bank document branches do not call the image
  collaborator.

Full verification:

- focused tests for all moved ownership and behavior;
- `python -m compileall -q bridge tests`;
- `git diff --check`;
- full pytest-xdist suite;
- fresh-process import checks for affected modules;
- import-graph before/after measurement;
- GitHub `test` and `dependency-audit` jobs on the exact PR head.

## 8. Acceptance criteria

The PR is complete when:

1. all three target import edges are absent;
2. largest SCC is no greater than 12 on this base lineage;
3. no previously acyclic production module becomes cyclic;
4. the new `session_titles` module remains acyclic;
5. Persona serialization, session-title behavior, image durable-job behavior,
   and document PNG fallback are regression-covered;
6. no compatibility alias or dynamic import is introduced;
7. exact-head local and GitHub CI are green.

## 9. Follow-up rule

Merge this wave only after exact-head verification. Then fetch merged `main`,
recompute the full graph, and select the next architecture work from that
evidence. GroupService remains a planned service target, but graph evidence
after this split decides whether it is next.

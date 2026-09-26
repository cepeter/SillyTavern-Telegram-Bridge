# Light Novel Conversation Mode Design

Date: 2026-09-26
Status: Written spec approved; implementation authorized on 2026-09-26

## Summary

Add a session-scoped Light Novel conversation mode to the Telegram bridge while preserving the existing Normal conversation path. `/character` becomes the setup entrypoint. It guides the user through Character -> Conversation Mode -> optional Light Novel strategy -> Persona -> World -> System Prompt -> Session. `/start` becomes a strict, one-time opening-message chooser for an unstarted session. Light Novel sessions immediately receive a durable panel of 2-4 next-action choices after the opening greeting and after each later story turn. Selecting a choice enters the existing durable user-message pipeline exactly as if the user had typed that text.

The design reuses existing character, persona, world, system-prompt, session, provider-routing, panel ownership, durable job, memory, RAG, Humanizer, language, and Telegram delivery boundaries. It adds a dedicated conversation-setup coordinator and a Light Novel domain/service layer rather than expanding `character_callbacks.py` into a general workflow owner.

## Goals

- Provide two conversation modes: `normal` and `lightnovel`.
- Let `/character` configure a target session through a consistent wizard.
- Support three Light Novel choice-generation strategies:
  - A: Story Inline
  - B: Utility Model
  - C: Story Second Pass
- Generate a random 2, 3, or 4 choices for every Light Novel choice panel.
- Make `/start` a one-time opening-message chooser after `/new` or `/reset`.
- Reject ordinary conversation input before `/start` without storing or generating anything.
- Immediately show the first Light Novel choice panel after the selected opening greeting is committed.
- Make choice selection durable, restart-safe, single-use, and session/actor scoped.
- Allow manual typed replies after a session has started; a manual reply invalidates any older open choice panel.
- Preserve Normal-mode behavior after `/start` with no Light Novel model calls or choice UI.
- Preserve existing `/reset` configuration while returning the session to an unstarted state.

## Non-goals

- Do not replace `/swipe`; swipe variants remain alternate assistant responses to one user turn.
- Do not treat a Light Novel choice as an assistant variant. A selected choice is a new user turn.
- Do not add Light Novel controls to `/settings`; setup is owned by `/character`.
- Do not require a fixed number of choices per session.
- Do not regenerate an already-committed story solely because choice generation failed.
- Do not make Telegram callback tokens the sole durable storage for Light Novel choices.
- Do not change existing group-session setup in this first implementation. Group-specific Light Novel orchestration is out of scope; the current group setup branch remains intact.

## Existing boundaries to reuse

The implementation should preserve current ownership:

- `bridge/character_callbacks.py`: character management and character selection dispatch.
- existing character/persona/world/system-prompt/session panels: canonical entity selectors.
- `bridge/session_core.py` / `bridge/session_repository.py`: session reads and writes.
- `bridge/model_selection.py`: story/utility task-model routing.
- `bridge/message_commands.py`: normal turn assembly, persistence, post-processing, and delivery.
- durable jobs and `bridge/worker_orchestration.py`: exactly-once/restart-safe user-turn execution.
- panel ownership/session binding: callback authorization.
- `bridge/callback_tokens.py`: short-lived opaque handles for setup UI where appropriate, but not the only persistence layer for story choices.
- response language, Humanizer, RAG, memory, streaming, and group-turn policy remain canonical in their existing services.

## Session-scoped conversation state

Conversation lifecycle/configuration is explicit and session scoped. Use the existing metadata ownership pattern for these keys:

- `conversation_mode:{chat_id}:{session_id}` -> `normal` or `lightnovel`
- `lightnovel_strategy:{chat_id}:{session_id}` -> `a`, `b`, `c`, or empty for Normal
- `conversation_started:{chat_id}:{session_id}` -> `0` or `1`

Helpers in a focused module such as `conversation_lifecycle.py` own normalization, defaults, reads, writes, and reset behavior. Callers must not assemble these keys ad hoc.

Default behavior for a newly created standard session:

- mode: `normal`
- strategy: empty
- started: `0`

Existing sessions need a one-time compatibility backfill so introducing this feature does not unexpectedly block active conversations. At migration/backfill time only:

- an existing session with any transcript rows becomes `conversation_started=1`
- an existing empty session becomes `conversation_started=0`

After backfill, runtime decisions use only the explicit lifecycle state, not transcript emptiness.

## `/character` setup wizard

### Entry

`/character` still opens the existing character menu and keeps existing management actions such as Info, Optimizer, Upload, Delete, and Close.

Selecting a character for a standard-session setup starts `ConversationSetupService`. Existing group-setup handling remains before this branch and is unchanged.

### Temporary setup state

Store one chat-scoped, expiring setup document under:

`conversation_setup:{chat_id}`

Fields:

- source/owner session id used to bind the wizard
- actor id
- selected `character_file`
- `conversation_mode`: `normal` or `lightnovel`
- `lightnovel_strategy`: empty, `a`, `b`, or `c`
- selected `persona_id`
- selected `world_file`
- selected `system_prompt`
- target `session_id` after the final step
- current wizard stage
- expiration timestamp

The wizard may use existing callback-token infrastructure for entity identifiers. The authoritative setup state is the DB-backed setup document, not Telegram callback data.

### Wizard order

Normal:

1. Character
2. Mode -> Normal
3. Persona
4. World
5. System Prompt
6. Session
7. Apply

Light Novel:

1. Character
2. Mode -> Light Novel
3. Strategy -> A / B / C
4. Persona
5. World
6. System Prompt
7. Session
8. Apply

The Normal path skips the strategy stage completely.

### Apply semantics

Nothing in the target session changes until final Session selection/Apply. Apply performs one transaction that writes:

- character
- conversation mode
- strategy
- persona
- world
- system prompt

The target session must be unstarted. If the selected target session is already started, Apply is refused with guidance to use `/reset` or `/new` first. This prevents changing the story identity/configuration halfway through an active transcript.

Successful Apply does not start the story. It responds with guidance equivalent to:

`Session configured. Use /start to choose the opening message.`

The temporary setup state is then cleared.

## `/new`, `/reset`, and `/start` lifecycle

### `/new`

The existing new-session flow continues to create and activate a session. The new session is initialized with `conversation_started=0`. Existing default character/model/session creation semantics remain. The user may run `/character` to configure it before `/start`.

### `/reset`

Reset continues to clear the current session's transcript, memory/summary, response variants, failures, and other existing reset-owned state. It additionally:

- invalidates all open/pending Light Novel choice records for the session
- best-effort disables/removes their Telegram choice panels
- sets `conversation_started=0`

Reset preserves:

- character
- Normal/Light Novel mode
- Light Novel A/B/C strategy
- persona
- world
- system prompt
- model routing and existing generation configuration unless current reset semantics already clear them

After reset the required next conversational action is `/start`.

### Strict pre-start gate

When `conversation_started=0`, ordinary user dialogue must be rejected before durable generation enqueue and before transcript persistence:

`Please use /start command.`

The plain-text `start` alias is retired. Only `/start` opens the greeting chooser.

Slash commands remain routable while unstarted, so setup/management commands such as `/start`, `/character`, `/new`, `/reset`, `/session`, `/persona`, `/world`, `/systemprompt`, `/providers`, `/settings`, `/help`, and `/status` keep working. Existing commands that require transcript state may return their normal empty-state feedback. The strict gate blocks non-command conversational input only. This policy is centralized in the lifecycle module rather than distributed across command handlers.

### `/start`

`/start` no longer performs the current Persona/World/System Prompt readiness recommendation flow and no longer uses a plain `start` alias. Its conversation responsibility is only:

1. Require `conversation_started=0`.
2. Open the existing Default/Alternate greeting chooser for the current character.
3. Commit the selected greeting as the first assistant message.
4. Atomically mark the session started with that committed greeting.

A later `/start` returns:

`This session has already started.`

The opening greeting selection is still protected by existing panel/session ownership and operation idempotency.

For Normal mode, the flow ends after greeting commit.

For Light Novel mode, greeting commit is followed immediately by creation/generation of the first 2-4-choice panel. Because the opening greeting is card-authored rather than generated by the story model, the first-choice generation uses a choice-only pass: Strategy A uses the story model, Strategy B uses the utility task-model route, and Strategy C uses the story model. Strategy A switches to its normal inline story+choices contract on subsequent generated story turns.

If Light Novel choice generation fails after the greeting was committed, the greeting and `conversation_started=1` remain valid. `/start` must not resend the greeting. A Retry Choices action is shown instead.

## Light Novel choice count

For every new choice-producing assistant turn, select the requested count uniformly from `{2, 3, 4}` exactly once.

The selected count is persisted before choice-model execution. Retries and restarts reuse the same requested count. An already-shown turn must never reshuffle from, for example, three choices to four merely because the bridge restarted or choice generation was retried.

The randomness source should be injectable/testable; it is not security-sensitive.

## Choice-generation strategies

All strategies return one logical result:

- visible story text
- exactly the previously selected 2-4 concise next-action choices

Choice text is bounded and normalized for Telegram button display and later use as an exact synthetic user turn. The model is instructed to produce distinct, actionable options rather than paraphrases of the same action.

### Strategy A — Story Inline

The story model is asked in the main generation call to return both the story and structured choice metadata. Parsing happens before visible-story language/Humanizer/Telegram post-processing so structural metadata is never stored or displayed as assistant prose.

A structured envelope is required by the Light Novel parser. The parser must separately recover the story and choices so that missing/malformed choices do not invalidate usable story text.

Normal assistant persistence stores only the visible story, never the structured envelope.

If the story is usable but choices are missing or invalid:

- commit/deliver the story
- persist the chosen requested count
- show Retry Choices
- repair choices in a separate choice-only pass using the story model; do not regenerate the committed story

Strategy A remains the cheapest expected path because successful turns need no dedicated choice-generation call beyond the story generation already required.

### Strategy B — Utility Model

The normal story model produces the story first. The story is processed and committed normally. The bridge then generates choices from the completed story using:

`task_model_for_session(..., task="utility")`

This automatically preserves the repository's existing utility -> main model fallback.

Choice-generation failure does not fail or regenerate the committed story. It produces Retry Choices for the same assistant row and requested count.

### Strategy C — Story Second Pass

The normal story model produces and commits the story. A second request to the same story model generates the choices from that completed story.

Failure behavior is the same as Strategy B: the story remains committed and retry targets only the choice-generation step.

### Response language and Humanizer

The visible story remains governed by the existing response-language and Humanizer pipeline.

Choice-generation prompts explicitly include the session's target response-language instruction so choice buttons match the story language. Choice metadata is not passed through the Humanizer because it is short action UI rather than assistant prose.

For Strategy A, the inline structured response is parsed before visible-story language/Humanizer processing. The story is post-processed exactly as existing assistant prose; the extracted choices are separately validated/normalized.

## Durable Light Novel choice storage

Add a focused table owned by a repository module, for example `light_novel_choice_sets`:

- `id` primary key
- `chat_id`
- `session_id`
- `assistant_rowid`
- random opaque `nonce`, unique
- strategy
- persisted `requested_count`
- serialized validated choices, nullable until generation succeeds
- generation status: `pending`, `ready`, or `failed`
- panel lifecycle state: `open`, `consumed`, or `invalidated`
- selected choice index, nullable
- Telegram panel message id, nullable
- created/updated timestamps

Indexes must support active-session lookup and nonce lookup.

Successful visible panels follow exactly this lifecycle:

`open -> consumed`

or

`open -> invalidated`

`pending`/`failed` describe generation before a usable choice panel exists; they do not create additional successful-panel lifecycle states.

The DB record, not a 15-minute callback token, is the durability source of truth. Choice callbacks use a compact opaque nonce plus bounded choice index. Panel/session/actor checks remain mandatory; nonce unpredictability is defense in depth, not authorization.

## Choice panel behavior

A ready set renders 2-4 buttons beneath/after the story response. The panel belongs to the same chat/session as its assistant row.

On a choice click:

1. Validate callback panel ownership, actor, chat, active session, nonce, index, and `conversation_started=1`.
2. Start one transaction.
3. Confirm the set is still `open` and is the current valid set for the session.
4. Atomically mark it `consumed` and record selected index.
5. Enqueue the selected choice text through the same durable user-message job path used by typed Telegram input, with an operation/idempotency identity tied to the consumed set.
6. Commit.
7. Best-effort replace/disable the panel with `Selected: <choice>`.
8. Let the normal durable worker generate the next assistant story turn.

Only the first successful click can consume a set. Repeated clicks report `Choice already used`. Invalidated or superseded sets report `Choice expired`.

The selected choice is stored as the new user message exactly once and therefore participates normally in memory, RAG, world info, persona, history, retry, streaming, group/session policy where applicable, response generation, and later transcript inspection.

## Manual replies during Light Novel mode

After the session has started, the user may ignore the offered buttons and type a normal reply.

Before accepting any new manual conversational turn, the bridge atomically invalidates all currently open Light Novel choice sets for that session. Their Telegram panels are best-effort disabled/closed. The typed text then proceeds through the existing durable generation path.

This prevents an old choice button from branching the story after the user has already continued manually.

## Turn finalization and choice generation

Normal mode:

`user -> existing generation -> existing assistant delivery`

No Light Novel storage, panel, or additional model calls occur.

Light Novel mode:

`user -> existing context/generation pipeline -> visible story commit/delivery -> choice-set generation/finalization -> choice panel`

The Light Novel service should attach at explicit pre/post generation points rather than duplicate the normal conversation stack.

Suggested focused components:

- `conversation_lifecycle.py`: mode/strategy/started keys and start-gate policy.
- `conversation_setup.py`: wizard state machine and atomic setup Apply.
- `conversation_setup_panels.py`: mode/strategy and wizard navigation UI.
- `light_novel_service.py`: strategy orchestration, random-count selection, prompt construction, validation, retry behavior.
- `light_novel_repository.py`: SQL-only durable choice-set storage and atomic consume/invalidate operations.
- `light_novel_panels.py`: choice and Retry Choices rendering/editing.
- `light_novel_callbacks.py`: thin callback adapter delegating to service/repository boundaries.

Exact filenames may change during implementation if an existing canonical owner is a better fit, but ownership boundaries must remain equivalent and static architecture must stay acyclic.

## Choice-generation failure and Retry Choices

If story generation itself fails before an assistant story is committed, existing durable failure/retry semantics remain authoritative.

If the story succeeds but choice generation fails:

- mark the choice-generation record `failed`
- preserve assistant row and requested count
- consider the user/story durable job successfully completed because its assistant response exists
- render a Retry Choices action tied durably to the assistant row/session

Retry Choices uses the durable failed choice-set nonce (`lnretry:<nonce>`) rather than a short-lived generic callback token. It:

- validates the session and that no newer valid turn has superseded this assistant row
- reuses the persisted requested count
- for A, performs a story-model choice-only repair pass
- for B, uses the utility route
- for C, uses the story model
- never regenerates or re-inserts the story

A successful retry moves generation status to `ready`, opens the set, and renders the normal choices.

## Restart and idempotency behavior

Choice state is SQLite-backed, so restart does not alter an already generated panel or requested count.

If a choice was consumed and its synthetic durable user job was committed before restart, recovery sees the job and executes it once. Re-clicking the old panel cannot enqueue a second turn because the set is no longer open.

If restart occurs while choice generation is pending/failed, the persisted assistant row and requested count allow safe retry without story regeneration.

If panel editing/deletion fails, DB state still wins. A stale visible button remains harmless because callback consumption checks durable state.

## Stale and mismatched callbacks

Reject without generating when any of these are false:

- panel belongs to the requesting actor/session as required by existing ownership rules
- callback chat matches stored choice set chat
- active session matches stored choice set session
- session is started
- nonce exists
- index is in range
- generation is ready
- panel state is open
- set has not been superseded by a manual/newer turn

No callback-provided choice text is trusted. The durable stored choice text is the only text submitted as the synthetic user turn.

## Reset invalidation

Before/within reset's owned transaction boundary, all Light Novel records for that session that can still be acted on are invalidated. Telegram cleanup is best effort and must not cause reset rollback after durable local state is safely invalidated.

The Light Novel choice table must be included in session deletion ownership so deleting a session removes its choice records.

## Interaction with existing features

### `/swipe`

Unchanged. Swipe remains assistant-response variant selection and must not be used for Light Novel user choices.

### Streaming

Normal visible story streaming continues where structurally possible. Strategy A must not leak its structured envelope/choice metadata into the streaming preview. The implementation must either stream only parsed visible story content safely or disable live preview for Strategy A until the structured response can be separated. The preferred initial implementation is to suppress Strategy A raw structured streaming rather than risk metadata leakage; B and C can keep normal story streaming because their story output is ordinary prose.

### Humanizer

Applies to visible assistant story only, as today.

### RAG / memory / world / persona / system prompt

A selected Light Novel choice enters the normal user path, so these systems require no parallel implementation.

### Response variants and regeneration

Existing assistant variants remain separate. Regeneration of a Light Novel story must invalidate the superseded story's open choice set before a replacement story/choice set becomes current.

### Group sessions

Existing group setup/turn handling remains unchanged in this first implementation. Light Novel setup is only applied to standard sessions. This avoids silently redefining speaker-turn semantics.

## Telegram UI behavior

Example Light Novel story result:

```
The lantern flickers as footsteps stop outside the door.

[ Open the door ]
[ Hide behind the curtain ]
[ Call out to whoever is there ]
```

The number of buttons is the persisted random 2-4 count for that assistant turn.

After selection, the old panel is best-effort replaced/disabled with a compact selection confirmation. The story message itself remains unchanged.

If choice generation failed:

`[ Retry Choices ]`

No panel-width assumptions are introduced; Telegram remains responsible for final client layout.

## Error handling

- Setup-state expiration: show `Setup expired; run /character again.` and clear stale state.
- Target session started during wizard: refuse Apply and ask for `/reset` or `/new`.
- Missing/deleted character/persona/world resource at Apply: refuse atomically; do not partially configure session.
- Invalid Light Novel strategy: normalize/refuse before persistence.
- Story failure: existing failure/retry path.
- Choice failure: preserve story and expose Retry Choices.
- Choice panel Telegram send/edit failure: preserve DB state; allow retry/re-render from durable state.
- Duplicate choice click: no-op with `Choice already used`.
- Old/superseded choice: no-op with `Choice expired`.
- User input before start: no enqueue, no transcript row, no provider call.

## Testing requirements

### Lifecycle

- `/new` produces `conversation_started=0`.
- `/reset` sets `conversation_started=0` while preserving conversation configuration.
- existing non-empty sessions are backfilled started; existing empty sessions are unstarted.
- ordinary text before start returns `Please use /start command.` and creates no job/message/model call.
- plain `start` no longer acts as `/start`.
- allowed management commands remain usable before start.
- `/start` opens greeting chooser only when unstarted.
- selected greeting is stored once and marks started once.
- second `/start` returns already-started feedback and cannot duplicate greeting.

### Setup wizard

- Normal path is Character -> Mode -> Persona -> World -> System Prompt -> Session and never shows A/B/C.
- Light Novel path requires exactly one of A/B/C before Persona.
- wizard state is chat/actor/session scoped and expires safely.
- no target-session mutation occurs before Apply.
- Apply writes all selected configuration atomically.
- Apply refuses a started target session.
- existing group-character setup path is unchanged.

### Light Novel generation

For A, B, and C:

- requested count is always 2, 3, or 4.
- deterministic tests can force each count.
- requested count is persisted before choice generation.
- retries/restarts reuse the same count.
- exactly requested-count validated choices are rendered.
- opening greeting immediately creates the first choice set.
- later story turns create a new choice set.

Strategy-specific:

- A parses story and choices while storing only story in transcript.
- A malformed/missing choices preserves story and offers retry.
- A repair retry does not regenerate story.
- B uses the existing utility task-model route.
- B failure preserves story and retries only choices.
- C uses the story model for the second pass.
- C failure preserves story and retries only choices.

### Choice consumption

- valid click atomically consumes exactly one set and enqueues one synthetic durable user turn.
- durable stored choice text, not callback text, becomes the user message.
- double click cannot create a second turn.
- stale nonce/index/session/chat/actor callbacks are rejected.
- consumed/invalidated callbacks are harmless even if Telegram UI cleanup failed.
- restart after consumption but before generation resumes the queued user turn exactly once.
- manual typed reply invalidates open choice set before its own enqueue.
- `/reset` invalidates open/pending sets.
- regeneration invalidates superseded choice sets.
- session deletion removes Light Novel records.

### Regression and architecture

- Normal mode after `/start` performs no Light Novel choice call or table mutation.
- existing `/swipe`, Humanizer, response-language, RAG, memory, streaming, group, retry, session, character-management, and panel tests remain green.
- Strategy A streaming does not expose structured metadata.
- static architecture analysis remains zero cycles/zero prohibited reciprocal dependencies.
- Ruff, format, mypy, dependency audit, `git diff --check`, full pytest suite, and CodeQL/CI remain green before merge.

## Rollout and documentation

Update command/help documentation for:

- `/character` setup wizard and Normal/Light Novel mode choice
- strategies A/B/C
- `/start` one-time semantics
- strict pre-start requirement
- `/reset` preserving configuration but requiring `/start` again
- manual Light Novel replies and choice behavior

The release changelog must call out the behavior change that plain `start` is no longer an alias and that unstarted sessions reject ordinary conversation until `/start`.

## Acceptance criteria

The feature is complete when a user can:

1. create/reset an unstarted standard session;
2. run `/character` and configure Normal or Light Novel mode through the approved wizard;
3. run `/start`, choose a character greeting, and start exactly once;
4. in Light Novel mode, immediately receive a persisted random 2-4 choice panel;
5. click exactly one choice and have it behave as a normal durable Telegram user reply;
6. continue indefinitely through story -> choices -> selected user turn loops using A, B, or C;
7. type a manual reply instead, safely invalidating old choices;
8. restart the bridge without reshuffling choices or duplicating consumed turns;
9. reset and retain setup configuration while returning to the required `/start` state;
10. use Normal mode without any Light Novel generation/UI overhead after start.

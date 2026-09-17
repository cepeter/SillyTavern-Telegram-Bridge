# SillyTavern Telegram Bridge

[![CI](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/cepeter/SillyTavern-Telegram-Bridge?display_name=tag)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> Current release: **v0.2.011**

A local Telegram sidecar for SillyTavern-style character chat. The bridge reads
native character/world data, stores private session state in SQLite, and routes
replies through a private provider catalog. It does not patch, launch, or
execute the SillyTavern source tree.

## Contents

- [Feature overview](#feature-overview)
- [Requirements](#requirements)
- [Install and test](#install-and-test)
- [Configuration](#configuration)
- [Provider catalog](#provider-catalog)
- [Commands](#commands)
- [Session lifecycle and memory](#session-lifecycle-and-memory)
- [Generation and reply delivery](#generation-and-reply-delivery)
- [Characters, Personas, prompts, and World Info](#characters-personas-prompts-and-world-info)
- [Voice, images, and documents](#voice-images-and-documents)
- [Live Sync and Forum Topic groups](#live-sync-and-forum-topic-groups)
- [Reliability and safety](#reliability-and-safety)
- [Run and update](#run-and-update)
- [Architecture](#architecture)
- [License](#license)

## Feature overview

### Native SillyTavern data

- Discovers PNG character cards from the configured native SillyTavern installation.
- Opens a panel for character selection, refresh, card metadata, upload guidance,
  and destructive deletion.
- Accepts uploaded cards as Telegram Documents, validates SillyTavern metadata,
  and keeps verified backups before replacing or deleting a card.
- Uses native Persona settings and native `User Avatars` storage. The bridge
  stores only the native avatar filename and session references; it does not
  maintain a second Persona catalog.
- Selects one or more validated World Info/lorebook JSON files and merges active
  entries deterministically during prompt assembly.

### Sessions and state

- Isolates conversations by chat and session, with custom names and technical
  session IDs shown together in `/status`.
- Stores per-session model, generation settings, reply language, Persona,
  Author's Note, World Info, system prompt, group state, variants, and summary.
- Uses panel-first workflows for destructive or multi-step changes. Free-form
  values are collected as the next scoped message and support `/cancel`.
- Keeps panel callbacks bound to the session that opened them, so an old panel
  cannot mutate a newly selected session.
- `/reset` requires explicit confirmation, purges only the active session's
  transcript and session-scoped Hindsight documents, then leaves the session
  empty. It does **not** send the character opening greeting; use `/start` when
  the greeting is wanted.
- `/session` can delete only inactive, non-busy sessions. Deletion is fail-closed:
  Hindsight cleanup must succeed before local rows are removed, while other
  sessions and their memories remain untouched.

### Providers and model selection

- Uses one private provider catalog with a paginated provider/model panel.
- Supports OpenAI-compatible Chat Completions, optional Anthropic Messages, and
  the keyless OpenCode Muse `/responses` transport when explicitly configured.
- Separates adapter-enabled models from catalog-only entries; a visible model is
  not automatically runnable.
- Provides bounded health checks, model discovery refresh, endpoint validation,
  SSE streaming configuration, and credential-safe error handling.
- Supports an opt-in OpenAI-compatible Images provider separately from chat models.

### Generation and delivery

- Separates temporary streaming preview from final delivery: the preview is
  hidden/deleted and the complete persisted response is delivered afterward.
- Recovers output-token stops, including reasoning-only streaming stops, with
  bounded continuation/retry budgets. `/continue` remains available for a
  deliberate extra segment.
- Splits long Telegram replies using UTF-16 code units and prefers paragraphs,
  newlines, sentences, whitespace, then a hard safe boundary.
- Preserves response text across persistence, continuation, edits, swipes, and
  final Telegram delivery; no tail-only finalization is used.
- Supports response variants (`/regen`, `/swipe`), branches, continuation,
  latest-user-turn editing, failed-turn retry, prompt diagnostics, presets,
  per-session language rendering, and reasoning controls.

### Memory and retrieval

- Uses Hindsight for explicit memory, active-session recall, and summaries.
- Every automatic recall is strictly limited to the active `session:<id>` tag;
  broader user/character recall is not used by the bridge.
- `/remember` stores one explicitly submitted fact after a scoped text prompt;
  `/memory search` searches the active session only.
- Data Bank RAG accepts PDF, DOCX, TXT, Markdown, JSON, YAML, CSV, HTML, and XML.
  It provides FTS5 search, optional namespaced embeddings, status, remove, and
  reindex controls with bounded extraction and embedding work.

### Media and orchestration

- Quote-driven automatic Edge TTS speaks only model dialogue in straight double
  quotes; narration and unquoted text are not synthesized.
- Local Faster-Whisper handles voice input with configurable model and language.
- Native expression sprites support automatic local classification, manual choice,
  and off mode. Sprites are sent only when the effective expression changes, with
  neutral/avatar/text fallbacks.
- `/imagine` is opt-in and accepts a validated 1–4,000 character prompt for an
  explicitly enabled Images provider.
- Forum Topic groups support round-robin, contextual, manual owner-gated, and
  bounded autonomous modes. Group state is isolated per topic.
- Durable SQLite jobs provide per-chat/topic FIFO ordering, separate utility/media
  capacity, update deduplication, restart recovery, and `/retry` for failed turns.
- `/update` checks the current release and is a no-op when already latest;
  otherwise it requires confirmation and performs a clean, fast-forward-only update.

## Requirements

- Python 3.11.
- A Telegram bot token and an allowlisted Telegram user ID.
- A local SillyTavern installation with at least one PNG character card.
- An OpenAI-compatible chat provider, or an explicitly configured Anthropic Messages provider.
- Optional: Hindsight, embedding, image, STT, and TTS services.

## Install and test

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install -r requirements-dev.txt
```

Run the private test gate from the repository root:

```bash
python3 -m unittest discover -s tests -q
python3 -m compileall -q bridge tests
```

## Configuration

Keep the real environment file outside Git:

```bash
mkdir -p ~/.local/share/sillytavern-telegram
cp .env.example ~/.local/share/sillytavern-telegram/.env
chmod 600 ~/.local/share/sillytavern-telegram/.env
```

Required values in `.env`:

```dotenv
SILLYTAVERN_TELEGRAM_BOT_TOKEN=replace-me
SILLYTAVERN_TELEGRAM_ALLOWED_USERS=123456789
SILLYTAVERN_DIR=/path/to/SillyTavern
SILLYTAVERN_DEFAULT_CHARACTER=example-character.png
SILLYTAVERN_MODEL=provider-one::provider-one/model-a
```

Optional path and runtime values:

```dotenv
SILLYTAVERN_ENV_FILE=/path/to/private/.env
SILLYTAVERN_BRIDGE_HOME=/path/to/private-bridge-data
SILLYTAVERN_PROVIDER_CONFIG=/path/to/private/providers.yaml
SILLYTAVERN_BRIDGE_SOURCE_DIR=/path/to/sillytavern-telegram-bridge
SILLYTAVERN_CHARACTER_DIR=/path/to/SillyTavern/data/default-user/characters
SILLYTAVERN_WORLD_DIR=/path/to/SillyTavern/data/default-user/worlds
SILLYTAVERN_SYSTEM_PROMPTS_DIR=/path/to/SillyTavern/data/default-user/sysprompt
```

Provider-specific credentials are named by each private provider catalog entry's
`api_key_env`. Never put real credentials in Git, README files, release assets,
or Telegram messages.

### Hindsight and RAG services

Hindsight is optional. When enabled, set `HINDSIGHT_API_URL` and its private
`HINDSIGHT_API_KEY`. The bridge requires a reachable service for session-scoped
purge and recall; reset/session deletion fail closed when cleanup cannot be
verified. The configured Hindsight bank and bridge SQLite mapping are private
runtime state.

Data Bank works without embeddings through local FTS5. For semantic retrieval,
configure an OpenAI-compatible embeddings endpoint and model:

```dotenv
SILLYTAVERN_RAG_EMBEDDING_URL=http://127.0.0.1:8891/v1/embeddings
SILLYTAVERN_RAG_EMBEDDING_MODEL=text-embedding-3-small
SILLYTAVERN_RAG_EMBEDDING_DIMENSIONS=1536
SILLYTAVERN_RAG_EMBEDDING_REVISION=1
```

Use loopback HTTP only for local services. External Hindsight and embedding
endpoints must use HTTPS and an explicit host allowlist. Changing embedding
model, dimensions, or revision requires a Data Bank reindex.

## Provider catalog

The bridge has its own private provider catalog; it does not read another
application's provider settings. Start from the generic example:

```bash
cp config/providers.example.yaml /path/to/private/providers.yaml
```

A provider entry may contain:

```yaml
providers:
  provider-one:
    name: Provider One
    api_endpoint: https://provider.example/v1
    api_key_env: PROVIDER_ONE_API_KEY
    transport: chat_completions
    streaming: true
    models:
      - provider-one/model-a
```

Use `streaming: true` only when the endpoint returns SSE chunks. Use
`discover_models: false` for endpoints without `GET /models`. Such endpoints
can set `health_check: chat_completion`; `/providers health` then sends a
bounded streaming `POST /chat/completions` probe instead of reporting a false
model-discovery failure. Provider health never displays credentials.

Use `/providers` as the canonical model selector:

```text
/providers          Open provider and model panel
/providers health   Check configured provider endpoints
/providers refresh  Refresh discoverable model catalogs
```

The old `/model` command is not a model-selection alias. Unknown slash commands
are rejected before normal generation.

## Commands

### Sessions and content

```text
/start              Send the character card opening message
/status             Show detailed active session and generation state
/new                Create and activate a named isolated session
/reset              Confirm active-session reset and memory purge
/session            Switch, create, or delete an inactive session
/character          Open character management
/persona            Open native Persona controls
/world              Open World Info/lorebook controls
/systemprompt       Choose a native SillyTavern System Prompt
/note               Open Author's Note controls
/providers          Open provider/model, Health, and Refresh controls
```

### Replies and settings

```text
/settings           Configure reasoning and generation values
/stream             Toggle streaming preview or fixed-language delivery
/preset             Use, save, or delete a generation preset
/regen              Generate another response variant
/swipe              Browse stored response variants
/branch             Choose the active response branch
/continue           Continue the latest assistant response
/edit               Edit the latest user turn and regenerate
/retry              Retry the latest failed response
/prompt             Show safe prompt diagnostics
/language           Choose the model reply language
/expression         Choose manual, automatic, or off expressions
/macro              Preview supported SillyTavern macros
/stscript           Open the allowlisted STscript panel
/cancel             Cancel the current pending input
```

### Voice, files, memory, and groups

```text
/voice              Toggle quote-driven automatic TTS
/voice_input        Configure transcription, STT model, and language
/imagine            Generate an image through an enabled image provider
/memory             Open Hindsight memory and search controls
/remember           Store one explicit long-term fact
/summarize          Regenerate the active-session summary
/databank           Open Data Bank RAG controls
/sync               Open Live API Sync controls
/group              Open Forum Topic group controls
/help               Open the interactive command guide
/help <command>     Show detailed behavior for one command
/update             Check and, after confirmation, update the bridge
```

`/tts` is disabled. Voice replies are automatic when `/voice` is enabled and
model dialogue is enclosed in straight double quotes. Narration and unquoted
text are not synthesized.

### Panel-first input and cancellation

Actions that accept free-form text open a scoped prompt rather than executing
an inline value immediately:

- `/new` collects a session name.
- `/edit` collects replacement text for the latest user turn.
- `/remember` collects one explicit Hindsight fact.
- `/macro` collects macro-preview text.
- `/imagine` collects an image prompt when Images is enabled.
- `/note`, `/voice_input language`, `/settings`, and `/databank search` use the
  same validated input pattern where applicable.

Send `/cancel` to abort the pending action. Invalid input keeps the prompt open
with validation feedback; valid input applies the change and returns to the
relevant panel. Pending prompts expire and are removed, and stale callbacks are
rejected rather than applied to the current session.

`/stscript` exposes only the supported bridge actions in a panel. It cannot run
arbitrary shell commands, filesystem operations, or network requests. The Reset
action opens the same confirmation flow as `/reset`.

### Command details

- `/status` includes the active card, custom session title plus technical ID,
  model, response language, Persona, World Info, Author's Note, expression,
  summary, Hindsight, RAG, group, and generation state.
- `/providers` is the canonical model selector. `Health` runs bounded adapter-
  aware probes; `Refresh models` updates discoverable catalogs. Catalog-only
  models remain view-only and are not used for generation.
- `/settings` exposes reasoning presets and validated temperature, token,
  sampling, penalty, and stop-sequence values. `/preset` supports list, use,
  save, and delete through the panel.
- `/memory` supports on/off/status and active-session search. `/databank`
  supports on/off/status/list/search/remove/reindex. `/language` supports list,
  status, and a session-scoped language choice.
- `/voice_input` supports on/off/status, STT model selection, and language
  choices: Auto, a fixed code, or scoped User input. `/group` supports the
  round-robin, contextual, manual Claim/Pass, and bounded autonomous modes.

### Media and edit inputs

- Send a Telegram photo with an optional caption to queue vision analysis against
  the active session. Vision-capable model support is required; unsupported
  vision fails closed without changing the transcript.
- Send a supported file as a Telegram Document to queue Data Bank ingestion.
  Character-card PNG Documents are routed to card validation before generic image
  handling.
- Send a Telegram voice message when `/voice_input on` is enabled to queue STT;
  the transcription is then handled as a normal session-scoped user turn.
- Editing a Telegram user message queues a native edit/regeneration operation
  against the active session and preserves durable ordering.

## Session lifecycle and memory

### Create and select

`/new` asks for a 1–80 character name, creates a separate session, and activates
it. `/session` lists sessions and provides switch/create/delete controls. Session
names are display labels; the technical ID remains visible in `/status` for
troubleshooting and callback safety.

Each session keeps its own conversation and settings. This includes the selected
model, generation values, response language, Persona, World Info, Author's Note,
System Prompt, response variants, summary, group state, and failed-turn state.
Panel callbacks are short-lived and session-bound; reopening a panel is required
when its binding is missing or expired.

### Reset versus delete

`/reset` is an active-session operation:

1. The command opens a confirmation panel; it does not mutate data.
2. Confirm purges Hindsight documents for the active session only.
3. Local transcript, variants, failed turns, summary, and session metadata are cleared.
4. The session remains available and empty.
5. No opening greeting is sent. Use `/start` to send the character card's
   `first_mes` explicitly.

`/session` deletion targets an inactive session only. It refuses the active
session and sessions with queued, scheduled, or running jobs. Hindsight cleanup
must succeed before SQLite deletion; if it fails, the session is preserved. A
successful deletion removes the target session's Hindsight documents and local
state but never touches another session.

### Memory scopes

Hindsight recall is always active-session-only. Automatic recall uses the exact
`session:<session_id>` scope, and `/memory search` uses the same boundary. The
bridge does not fall back to broad user or character memory during generation.
`/remember` collects one explicit fact through a scoped text prompt. `/summarize`
rebuilds the active-session summary from its stored transcript.

## Generation and reply delivery

Streaming edits one temporary preview message. The preview is hidden or deleted
before the final response is sent. The final response is split into multiple
Telegram messages when needed, preserving the original text exactly.

Splitting uses Telegram's UTF-16 limit and prefers, in order:

1. Paragraph boundaries.
2. Newlines.
3. Sentence boundaries.
4. Whitespace.
5. A hard UTF-16-safe boundary.

When a provider reports `finish_reason: length`, the bridge performs bounded
continuation/recovery. A streaming response with no visible content is retried
with larger budgets up to the configured recovery ceiling. `/continue` remains
available when another segment is still needed.

### Response formatting and automatic voice

Use the following convention in model replies:

```text
**She steps back and watches the doorway.**  Narrative/action; stays as text
"I heard something outside."                   Dialogue; eligible for auto-TTS
The door opens slowly.                          Unquoted text; stays as text
```

When `/voice on` is enabled, the bridge extracts only complete dialogue spans in
straight double quotes (`"..."`) for TTS. Narrative/action—including `**bold
narrative**`—and other unquoted text are never synthesized. The original model
reply remains unchanged in Telegram and SQLite. Smart/curly quotes are not the
TTS delimiter.

For incoming user messages, the prompt formatter recognizes single-star action
spans (`*waves*`) and labels them as `User action`; `**text**` is preserved
literally and is not treated as an action marker. This formatting changes only
the prompt representation, not the stored transcript.

## Characters, Personas, prompts, and World Info

### Character cards

`/character` is panel-only. It can select a native PNG card, refresh the native
catalog, show card metadata, provide upload instructions, and open a separate
delete confirmation. Send a validated SillyTavern PNG as a Telegram Document
when uploading; inline keyboards cannot open a file picker. The active card and
cards referenced by sessions or groups are protected. Card backups are verified
before destructive replacement or deletion.

### Native Personas

`/persona` reads and writes SillyTavern's native Persona settings and avatar
folder. Create and edit operations preserve unrelated native settings. The
active Persona and Personas referenced by another session are excluded from the
`Delete inactive` picker. Import/export of a separate bridge Persona catalog is
not supported.

### Prompts and lorebooks

- `/systemprompt` selects a native SillyTavern System Prompt from
  `data/default-user/sysprompt/` (JSON `name` + `content`, plus TXT fallback).
  Native `post_history` fields are currently ignored by the bridge. Prompt bodies
  stay private and are applied at generation time; Telegram menus show only
  labels. `SILLYTAVERN_SYSTEM_PROMPTS_DIR` can explicitly override the native
  path when a different SillyTavern user profile is required.
- `/note` controls the session Author's Note. `Off` clears it; `User input`
  collects the next scoped text message.
- `/world` selects or disables native World Info files. Multiple validated
  lorebooks can be active simultaneously and their entries are merged during
  prompt assembly.
- `/prompt` reports safe prompt diagnostics without exposing the complete system
  prompt or private provider credentials.

### Expressions

```text
/expression
→ Automatic   Local response classification; send sprite only on change
→ Manual      Choose a discovered native sprite
→ Off         Disable expression delivery
```

Sprites are discovered from the active character's native SillyTavern
expression assets. If no match exists, the bridge falls back to a neutral sprite,
then the fixed character avatar, then text-only delivery. The model reply text
is unchanged.

## Voice, images, and documents

### Voice input and output

- `/voice` toggles automatic TTS. Only text inside straight double quotes is
  synthesized; narration, actions, and unquoted text remain text-only.
- `/voice_input` configures local Faster-Whisper transcription, the STT model,
  and the language. Language can be Auto, a fixed 2–8 letter code, or scoped
  user input.
- Voice and transcription work is queued as durable utility jobs so it cannot
  permanently block normal text generation.

### Image generation

`/imagine` is disabled unless a provider catalog entry explicitly enables Images
and its `image_endpoint`, model, and size are configured. The prompt is bounded
to 1–4,000 characters. Unsupported or unconfigured image requests fail closed;
chat-only models are never silently used as image models.

### Data Bank documents

`/databank` provides status, mode, list, search, remove, and reindex controls.
Documents are routed by filename and validated before extraction. Supported
formats are PDF, DOCX, TXT, Markdown, JSON, YAML, CSV, HTML, and XML. Extraction
size, PDF pages, DOCX expansion, and embedding work are bounded. FTS5 is
available locally; optional embeddings add semantic retrieval with a namespaced
cache and configurable embedding revision.

## Live Sync and Forum Topic groups

Live Sync is off by default and uses SillyTavern's supported loopback API. It
performs an initial reconciliation before enabling realtime updates and stops on
authentication, schema, sync-ID, or two-sided conflict errors.

```dotenv
SILLYTAVERN_SYNC_API_URL=http://127.0.0.1:8000
SILLYTAVERN_SYNC_API_INTERVAL_SECONDS=2
SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS=10
SILLYTAVERN_SYNC_API_HANDLE=
SILLYTAVERN_SYNC_API_PASSWORD=
```

The bridge does not patch SillyTavern, install an extension, or synchronize chat
files. External Live Sync credentials remain environment-only.

### Forum Topic groups

`/group` is available only inside a Telegram Forum Topic. Each topic has its own
session scope and state. The group panel can create or select a group session,
choose characters and World Info, and select a bounded orchestration mode:

- **Round-robin:** invite configured characters in order.
- **Contextual:** choose the next character from the current context.
- **Manual:** an owner claims the turn or passes it; ownership is enforced
  server-side because Telegram cannot disable the native Send button.
- **Autonomous:** continue automatically within configured bounds.

Group state changes and generated turns are durable. Topic identifiers are kept
internally for isolation and are added back to Telegram API payloads only when
sending a message.

## Reliability and safety

- Keep `.env`, provider YAML, SQLite, logs, cards, Personas, and private prompts outside Git.
- Restrict Telegram access with `SILLYTAVERN_TELEGRAM_ALLOWED_USERS`.
- Require HTTPS for external provider, image, and embedding endpoints; loopback is allowed for local services.
- Validate provider hosts before attaching credentials; health output never exposes keys.
- Bound uploaded file size, DOCX expansion, PDF pages, extracted text, embedding work, and image prompts.
- Keep Hindsight recall limited to the active session and treat recalled text as untrusted data.
- Persist updates before acknowledging Telegram, deduplicate update IDs, and recover queued or interrupted jobs after restart.
- Keep per-chat and per-topic FIFO ordering. Failed turns are stored before the Telegram offset advances so `/retry` can replay them.
- Treat provider/model catalog entries as untrusted configuration: adapter, endpoint,
  transport, and streaming behavior are validated before inference.
- Close panel keyboards and remove session bindings on Cancel, Close, expiry, or
  stale callbacks. Unknown slash commands never fall through to generation.
- Use the systemd hardening template for production deployments.

## Run and update

```bash
python sillytavern_telegram_bridge.py --check
python sillytavern_telegram_bridge.py
```

`--check` loads the configured environment, validates the native card and
runtime permissions, checks optional Live Sync authentication, and verifies the
Telegram bot identity without starting the polling loop. Use the same Python
interpreter and environment file as the service deployment.

`/update` is confirmation-gated. It first shows the installed and latest release
and bounded release notes. When already current it performs no fetch, copy, or
restart. When an update is available it requires a clean checkout and a
fast-forwardable `origin/main`, then synchronizes the live bridge and restarts
the service. Cancel never changes files or service state.

A systemd template is available at
`systemd/sillytavern-telegram.service.example`. Set the private
`SILLYTAVERN_BRIDGE_SOURCE_DIR` when `/update` runs from the live launcher copy.

## Architecture

The runtime is loaded through `bridge/runtime.py`; the list below highlights the
main boundaries rather than every source file.

```text
sillytavern_telegram_bridge.py  launcher
bridge/runtime.py               shared compatibility loader
bridge/common.py                configuration, workers, permissions
bridge/cards.py                 cards, prompts, World Info, panels
bridge/database.py              sessions and generation settings
bridge/memory.py                Hindsight and summaries
bridge/rag.py                   Data Bank, FTS5, embeddings
bridge/catalog.py               provider catalog and health
bridge/generation.py            adapters, streaming, continuation
bridge/telegram.py              Telegram transport and splitting
bridge/help*.py/json            Help rendering and editable details
bridge/sync_*.py                Live Sync primitives and API
bridge/groups.py                Forum Topic orchestration
bridge/media.py                 images, voice, STT, TTS
bridge/main.py                  polling and durable job dispatch
```

## License

MIT. See [LICENSE](LICENSE).

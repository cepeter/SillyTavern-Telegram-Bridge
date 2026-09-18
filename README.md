# SillyTavern Telegram Bridge

[![CI](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/cepeter/SillyTavern-Telegram-Bridge?display_name=tag)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> Current release: **v0.2.011**

SillyTavern Telegram Bridge lets you use a SillyTavern-style character chat from
Telegram. It is a small local sidecar: SillyTavern remains the source of truth for
character cards, Personas, World Info, and System Prompts, while the bridge handles
Telegram delivery, provider calls, isolated sessions, memory, retrieval, and
reliable background work.

It does not patch or launch the SillyTavern source tree.

## Contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Install and test](#install-and-test)
- [Configuration](#configuration)
- [Provider catalog](#provider-catalog)
- [Using the bot](#using-the-bot)
- [Sessions and memory](#sessions-and-memory)
- [Generation and delivery](#generation-and-delivery)
- [Native SillyTavern data](#native-sillytavern-data)
- [Voice, images, and documents](#voice-images-and-documents)
- [Live Sync and Forum Topic groups](#live-sync-and-forum-topic-groups)
- [Reliability, privacy, and safety](#reliability-privacy-and-safety)
- [Run and update](#run-and-update)
- [Architecture](#architecture)
- [License](#license)

## What it does

### A familiar Telegram chat

Send a normal Telegram message and the bridge builds a prompt from the active
character, conversation history, Persona, World Info, System Prompt, memory, and
Data Bank context. Replies are delivered as ordinary Telegram messages, with
streaming previews available when the provider supports SSE.

You can keep several named sessions per chat. Each session has its own transcript,
model, generation settings, language, Persona, World Info, notes, variants, summary,
and group state. Telegram panel buttons are bound to the session that opened them,
so an old panel cannot accidentally change a newly selected session.

### Native SillyTavern data

The bridge reads and writes the native data that belongs in SillyTavern:

- PNG character cards from `data/default-user/characters/`.
- World Info/lorebooks from `data/default-user/worlds/`.
- System Prompts from `data/default-user/sysprompt/`.
- Persona names and descriptions from native `settings.json`.
- Persona avatars from `data/default-user/User Avatars/`.
- Native expression sprites associated with the active character.

Character and Persona changes use validation, protected-target checks, verified
backups, and readback before the bridge reports success.

### Providers and generation

A private provider catalog drives model selection. It supports OpenAI-compatible
Chat Completions, optional Anthropic Messages, keyless OpenCode Muse `/responses`,
and opt-in OpenAI-compatible image generation. Provider health checks, model
refresh, endpoint validation, adapter selection, and credential-safe errors are
available from the provider panel.

The bridge also handles response variants, branches, editing the latest user turn,
retrying failed turns, continuation after output-token limits, per-session response
language, reasoning budgets, presets, and validated generation values.

### Memory and retrieval

Hindsight provides explicit memory, active-session recall, and session summaries.
Recall is strictly scoped to the active `session:<id>` tag; the bridge does not fall
back to broad user or character memory during generation.

The Data Bank provides local FTS5 search and optional namespaced embeddings for PDF,
DOCX, TXT, Markdown, JSON, YAML, CSV, HTML, and XML documents. Extraction, PDF
pages, document size, and embedding work are bounded.

### Media and groups

The bridge can process Telegram photos, supported Documents, and voice messages.
It can also run automatic quote-driven TTS, native expression sprites, and an opt-in
image provider. Telegram Forum Topics can host isolated multi-character group
sessions with round-robin, contextual, manual, and bounded autonomous modes.

## Requirements

- Python 3.11.
- A Telegram bot token and an allowlisted Telegram user ID.
- A local SillyTavern installation with at least one PNG character card.
- An OpenAI-compatible chat provider, or an explicitly configured Anthropic Messages provider.
- Optional Hindsight, embedding, image, STT, and TTS services.

## Install and test

Create an isolated Python environment and install the locked dependencies:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install -r requirements-dev.txt
```

Run the test gate from the repository root:

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

Required values:

```dotenv
SILLYTAVERN_TELEGRAM_BOT_TOKEN=replace-me
SILLYTAVERN_TELEGRAM_ALLOWED_USERS=123456789
SILLYTAVERN_DIR=/path/to/SillyTavern
SILLYTAVERN_DEFAULT_CHARACTER=example-character.png
SILLYTAVERN_MODEL=provider-one::provider-one/model-a
```

Useful path overrides:

```dotenv
SILLYTAVERN_ENV_FILE=/path/to/private/.env
SILLYTAVERN_BRIDGE_HOME=/path/to/private-bridge-data
SILLYTAVERN_PROVIDER_CONFIG=/path/to/private/providers.yaml
SILLYTAVERN_BRIDGE_SOURCE_DIR=/path/to/sillytavern-telegram-bridge
SILLYTAVERN_CHARACTER_DIR=/path/to/SillyTavern/data/default-user/characters
SILLYTAVERN_WORLD_DIR=/path/to/SillyTavern/data/default-user/worlds
SILLYTAVERN_SYSTEM_PROMPTS_DIR=/path/to/SillyTavern/data/default-user/sysprompt
```

By default, native Persona settings are read from:

```text
$SILLYTAVERN_DIR/data/default-user/settings.json
```

and native Persona avatars from:

```text
$SILLYTAVERN_DIR/data/default-user/User Avatars/
```

Provider-specific credentials are named by each private provider catalog entry's
`api_key_env`. Never put real credentials in Git, README files, release assets, or
Telegram messages.

### Hindsight and Data Bank

Hindsight is optional. When enabled, configure its private URL and key:

```dotenv
HINDSIGHT_API_URL=http://127.0.0.1:8890
HINDSIGHT_API_KEY=
```

Reset and inactive-session deletion fail closed if session-scoped Hindsight cleanup
cannot be verified. This prevents local SQLite data from being deleted while its
remote session documents remain.

Data Bank works locally through FTS5. For semantic retrieval, configure an
OpenAI-compatible embeddings endpoint:

```dotenv
SILLYTAVERN_RAG_EMBEDDING_URL=http://127.0.0.1:8891/v1/embeddings
SILLYTAVERN_RAG_EMBEDDING_MODEL=text-embedding-3-small
SILLYTAVERN_RAG_EMBEDDING_DIMENSIONS=1536
SILLYTAVERN_RAG_EMBEDDING_REVISION=1
```

Use loopback HTTP only for local services. External Hindsight and embedding
endpoints must use HTTPS and an explicit host allowlist. Reindex the Data Bank when
the embedding model, dimensions, or revision changes.

## Provider catalog

The bridge has its own private provider catalog. It does not read another
application's provider settings. Start from the generic example:

```bash
cp config/providers.example.yaml /path/to/private/providers.yaml
```

A provider entry can look like this:

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

Use `streaming: true` only when the endpoint returns SSE chunks. Set
`discover_models: false` for endpoints without `GET /models`. Such an endpoint can
use `health_check: chat_completion`; the health panel will run a bounded streaming
chat probe instead of reporting a misleading model-discovery failure.

OpenCode Muse is configured as a separate keyless transport when supported by the
provider catalog. Catalog visibility does not imply that a model is runnable:
adapter, transport, endpoint, and streaming settings are validated before
inference.

Use the provider panel as the canonical selector:

```text
/providers          Open the provider and model panel
/providers health   Run provider health checks
/providers refresh  Refresh discoverable model catalogs
```

## Using the bot

The bot is intentionally panel-first. Use commands to open a menu, then use the
buttons or the next scoped text message to complete the action.

### Everyday commands

```text
/start              Send the character card's opening message
/status             Show active card, session, model, memory, RAG, and generation state
/new                Create and activate a named isolated session
/reset              Confirm an active-session reset and memory purge
/session            Switch, create, or delete an inactive session
/character          Manage native character cards
/persona            Choose, create, edit, or disable a native Persona
/world              Choose or disable World Info/lorebooks
/systemprompt       Choose a native SillyTavern System Prompt
/note               Configure the session Author's Note
/providers          Choose a provider and model
```

### Replies and generation

```text
/settings           Configure reasoning and generation values
/stream             Toggle streaming preview
/preset             Use, save, or delete a generation preset
/regen              Generate another response variant
/swipe              Browse stored response variants
/branch             Choose the active response branch
/continue           Continue the latest assistant response
/edit               Edit the latest user turn and regenerate
/retry              Retry the latest failed response
/prompt             Show safe prompt diagnostics
/language           Choose the model reply language
/expression         Choose native expression behavior
/macro              Preview supported SillyTavern macros
/stscript           Open the allowlisted STscript panel
/cancel             Cancel the current pending input
```

### Voice, files, memory, and groups

```text
/voice              Toggle automatic quote-driven TTS
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

`/tts` is not a command. Automatic voice is controlled by `/voice`.

### User message formatting

Use these markers when writing a user message:

```text
*She walks toward the doorway.*  User action/narration
"I heard something outside."       User dialogue and queued TTS
I heard something outside.           User dialogue without a marker
**text**                             Literal text; not an action marker
```

The bridge recognizes single-star spans such as `*waves*` and presents them to the
model as `User action`. The remaining text is presented as `User dialogue`.

Complete straight-quoted dialogue such as `"Hello there."` is also queued for TTS
when `/voice on` is enabled. This applies to both user messages and model replies.
The original user and model text remains unchanged in the stored transcript.

Double-star spans such as `**text**` are preserved literally. They are not treated
as action markers and are not synthesized. Curly or smart quotes are not TTS
delimiters.

### Panel input and cancellation

Commands that need free-form text open a scoped input step. This includes `/new`,
`/edit`, `/remember`, `/macro`, `/imagine`, `/note`, voice-input language, selected
settings, and Data Bank search.

Send `/cancel` to leave the pending action unchanged. Invalid input keeps the prompt
open with validation feedback. Valid input applies the change and returns to the
relevant panel. Pending inputs expire, and stale callbacks are rejected instead of
being applied to the current session.

`/stscript` exposes only allowlisted bridge actions. It cannot execute arbitrary
shell commands, filesystem operations, or network requests. Its Reset action uses
the normal confirmation flow.

## Sessions and memory

### Session lifecycle

`/new` asks for a 1–80 character name, creates a separate session, and activates it.
`/session` lists sessions and provides switch, create, and inactive-delete controls.
The custom title and technical session ID are both visible in `/status`.

Each session stores its own:

```text
Conversation transcript
Selected model and generation settings
Response language
Persona and World Info
Author's Note and System Prompt
Response variants and branch state
Summary and failed-turn state
Forum Topic group state, when applicable
```

### Reset and deletion

`/reset` affects the active session but does not delete the session itself:

1. It opens a confirmation panel without mutating data.
2. Confirmation purges Hindsight documents for that session only.
3. It clears the local conversation, variants, failed turns, summary, and session data.
4. The session remains available and empty.
5. It does not send the character opening greeting. Use `/start` for that.

The confirmation text explicitly warns that the active conversation, Hindsight
memories, SQLite session data, and session documents will be deleted.

`/session` deletion is different. It can target only an inactive session and refuses
sessions with queued, scheduled, or running jobs. Hindsight cleanup must succeed
before local SQLite deletion. If cleanup cannot be verified, the session is
preserved. Other sessions and their memories are not touched.

### Memory boundaries

Automatic Hindsight recall and `/memory search` are hard-scoped to the active
`session:<session_id>` tag. `/remember` stores one explicitly submitted fact after a
scoped prompt. `/summarize` rebuilds the active summary from stored transcript
content.

Memory and retrieved document text are treated as untrusted context during prompt
assembly. They are bounded before reaching the model.

## Generation and delivery

### Prompt assembly

A normal text generation can include:

```text
Character card fields and example dialogue
Native Persona description
Active native World Info entries
Selected native System Prompt
Session Author's Note
Conversation history and summary
Active-session Hindsight recall
Data Bank references
Response-language instruction
Provider-specific generation settings
```

The bridge keeps stored user text and model text intact. User single-star action
formatting is added only to the prompt representation; it does not rewrite the
stored transcript.

### Streaming and incomplete output

With streaming enabled, the bridge edits a temporary Telegram preview while the
provider is generating. It then removes or hides that preview and sends the complete
persisted response through the normal Telegram splitter.

Long responses are split using Telegram's UTF-16 limit. The splitter prefers:

1. Paragraph boundaries.
2. Newlines.
3. Sentence boundaries.
4. Whitespace.
5. A hard UTF-16-safe boundary.

When a provider stops with `finish_reason: length`, the bridge performs bounded
continuation/recovery, including reasoning-only streaming stops that produced no
visible text. `/continue` remains available when another deliberate segment is
needed.

### Variants and recovery

- `/regen` creates a new response variant.
- `/swipe` browses and selects stored variants.
- `/branch` switches the active response branch.
- `/edit` replaces the latest user turn and regenerates it.
- `/retry` replays the latest failed response without creating a duplicate assistant
  turn.
- Durable operation markers make restart recovery and Telegram delivery idempotent.

## Native SillyTavern data

### Characters

`/character` selects native PNG cards, shows metadata, provides upload guidance, and
opens a protected delete flow. Uploaded cards are validated as SillyTavern PNGs.
Verified backups are made before destructive replacement or deletion.

The active card and cards referenced by sessions or groups are protected from
accidental deletion.

### Personas

`/persona` reads and writes native SillyTavern Persona settings and avatar storage.
The bridge does not maintain a second Persona catalog. The active Persona and
Personas referenced by another session are protected from the inactive-delete
picker.

When a session has no valid selected Persona, the bridge resolves SillyTavern's
native default Persona. If no explicit native default is set and exactly one native
Persona exists, that Persona is used. If no safe default can be resolved, the
bridge uses a generic label rather than exposing a private identity.

### World Info and System Prompts

`/world` selects or disables one or more native World Info JSON files. Active
lorebooks are path-validated and their matching entries are merged deterministically
at prompt time.

`/systemprompt` reads native System Prompts from:

```text
$SILLYTAVERN_DIR/data/default-user/sysprompt/
```

Native JSON files use `name` and `content`; TXT files are accepted as a fallback.
The bridge currently ignores native `post_history` fields. Prompt bodies stay
private; menus and `/status` expose labels or status only.

### Expressions

`/expression` supports automatic classification, manual native sprite selection, and
off mode. Sprites are delivered only when the effective expression changes. If a
matching sprite is unavailable, the bridge falls back to a neutral sprite, the
character avatar, and finally text-only delivery.

## Voice, images, and documents

### Automatic TTS

`/voice on` enables quote-driven automatic voice for both sides of the conversation:

```text
User message:  "Please wait for me."
Model reply:   *The character turns.* "I will wait."
```

Both quoted dialogue spans are queued for TTS. The action text and any unquoted text
remain text-only. The user/model transcript is still stored as ordinary text, and
TTS work runs in the utility queue with idempotent operation IDs.

### Voice input

`/voice_input` controls Telegram voice-message transcription with Faster-Whisper.
You can choose the STT model and one of:

```text
Auto
A fixed 2–8 letter language code
Scoped User input
```

Voice transcription is queued as a durable utility job and then enters the active
session as a normal user turn.

### Images

`/imagine` is disabled unless an Images provider is explicitly enabled with an image
endpoint, model, and output size. Prompts are limited to 1–4,000 characters. Chat-only
models are never silently reused for image generation.

Telegram photos with optional captions are queued for vision analysis when the
selected model supports vision. Unsupported vision fails closed without changing the
transcript.

### Documents and Data Bank

Telegram Documents are routed to character-card validation or Data Bank ingestion.
Supported Data Bank formats are PDF, DOCX, TXT, Markdown, JSON, YAML, CSV, HTML, and
XML. File size, PDF pages, DOCX expansion, extracted text, and embedding work are
bounded.

## Live Sync and Forum Topic groups

### Live Sync

Live Sync is off by default and uses SillyTavern's supported loopback API. It performs
an initial reconciliation before enabling realtime updates. Authentication errors,
schema mismatches, sync-ID mismatches, and two-sided conflicts stop synchronization
instead of silently choosing a winner.

```dotenv
SILLYTAVERN_SYNC_API_URL=http://127.0.0.1:8000
SILLYTAVERN_SYNC_API_INTERVAL_SECONDS=2
SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS=10
SILLYTAVERN_SYNC_API_HANDLE=
SILLYTAVERN_SYNC_API_PASSWORD=
```

The bridge does not install an extension, synchronize chat files directly, or expose
Live Sync credentials in Telegram.

### Forum Topic groups

`/group` works only inside a Telegram Forum Topic. Each topic has isolated session
and group state. The wizard can create a group session, choose characters and World
Info, and select a mode:

- **Round-robin:** characters speak in configured order.
- **Contextual:** the current context helps select the next speaker.
- **Manual:** an owner claims or passes the turn; ownership is checked server-side.
- **Autonomous:** characters continue within configured bounds.

Group state changes and generated turns are durable. Topic identifiers are kept
internally for isolation and added to Telegram payloads only when sending.

## Reliability, privacy, and safety

- Keep `.env`, provider YAML, SQLite, logs, cards, Personas, and private prompts out
  of Git.
- Restrict Telegram access with `SILLYTAVERN_TELEGRAM_ALLOWED_USERS`.
- Require HTTPS for external provider, image, and embedding endpoints; loopback is
  allowed for local services.
- Validate provider hosts before attaching credentials. Health output never displays
  keys.
- Treat provider catalogs, model responses, uploaded documents, memories, and RAG
  references as untrusted input.
- Bound uploaded files, document expansion, PDF pages, extracted text, image prompts,
  memory context, and embedding work.
- Persist updates before acknowledging Telegram and deduplicate update IDs.
- Keep per-chat and per-topic FIFO ordering. Failed turns are stored before Telegram
  offset advancement so `/retry` can replay them.
- Close panel keyboards and remove bindings on Cancel, Close, expiry, or stale
  callbacks.
- Unknown slash commands are rejected before normal generation.
- `/stscript` is allowlisted and cannot execute arbitrary commands.
- Use the systemd hardening template for production deployments.

## Run and update

Check the installation without starting the polling loop:

```bash
python sillytavern_telegram_bridge.py --check
```

Start the bridge:

```bash
python sillytavern_telegram_bridge.py
```

`--check` loads the environment, validates the native card and runtime permissions,
checks optional Live Sync authentication, and verifies the Telegram bot identity.

A systemd template is available at
`systemd/sillytavern-telegram.service.example`. Set the private
`SILLYTAVERN_BRIDGE_SOURCE_DIR` when `/update` runs from a live launcher copy.

`/update` is confirmation-gated. If the installed release is already current, it
performs no fetch, copy, or restart. Otherwise it requires a clean checkout and a
fast-forwardable `origin/main`, then synchronizes the live bridge and restarts the
service.

## Architecture

The runtime uses `bridge/runtime.py` to load domain modules into one shared namespace.
The files below are the main boundaries, not an exhaustive list:

```text
sillytavern_telegram_bridge.py  launcher
bridge/runtime.py               shared runtime loader
bridge/common.py                configuration, queues, permissions
bridge/cards.py                 cards, Persona display, prompts, World Info
bridge/database.py              sessions and generation settings
bridge/memory.py                Hindsight and summaries
bridge/rag.py                   Data Bank, FTS5, embeddings
bridge/catalog.py               provider catalog and health
bridge/generation.py            adapters, streaming, continuation
bridge/telegram.py              Telegram transport and splitting
bridge/help*.py/json             help menus and command details
bridge/sync_*.py                Live Sync primitives and API
bridge/groups.py                Forum Topic orchestration
bridge/media.py                 voice, STT, TTS, Telegram media
bridge/main.py                  polling and durable job dispatch
bridge/recovery.py              idempotent operation recovery
bridge/state_integrity.py       native/Hindsight consistency hardening
bridge/scheduler_safety.py      SQLite and durable-job hardening
bridge/pdf_parser.py            isolated PDF worker for Data Bank
```

## License

MIT. See [LICENSE](LICENSE).

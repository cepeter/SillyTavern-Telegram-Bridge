# SillyTavern Telegram Bridge

[![CI](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/cepeter/SillyTavern-Telegram-Bridge?display_name=tag)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> Current release: **v0.2.011**

Talk to your SillyTavern characters from Telegram. That's the idea. The bridge
sits between Telegram and your model provider, handling sessions, memory, voice,
images, and all the plumbing — while SillyTavern stays in charge of character
cards, Personas, World Info, and System Prompts.

It doesn't patch or launch SillyTavern. Think of it as a remote control that
reads the same files SillyTavern uses, then lets you chat with them from your
phone.

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

### Chat with your characters from Telegram

Send a message like you would to any contact. The bridge pulls together the
active character card, your Persona, World Info, System Prompt, conversation
history, memory, and any relevant documents — builds the prompt — and sends it
to your model provider. The reply comes back as a normal Telegram message. If
the provider supports streaming, you'll see a live preview while it generates.

You can have multiple named sessions in the same chat. Each one keeps its own
transcript, model, settings, Persona, World Info, notes, variants, and group
state. Panel buttons are tied to the session that opened them, so an old menu
can't accidentally mess with a different session.

### Reads your native SillyTavern data

The bridge works directly with the files SillyTavern already uses:

- Character cards (PNG) from `data/default-user/characters/`
- World Info / lorebooks from `data/default-user/worlds/`
- System Prompts from `data/default-user/sysprompt/`
- Persona names and descriptions from `settings.json`
- Persona avatars from `data/default-user/User Avatars/`
- Expression sprites tied to the active character

When you swap characters or Personas, the bridge validates everything, checks
for protected targets, makes a backup, and confirms the change before reporting
success. No silent overwrites.

### Providers and generation

A private provider catalog drives model selection. You can use
OpenAI-compatible Chat Completions, Anthropic Messages, the keyless OpenCode
Muse `/responses` transport, or an opt-in image provider — all from the same
panel. Health checks, model discovery, endpoint validation, and streaming
configuration are built in.

Beyond basic generation, the bridge handles response variants, branches,
editing your last message, retrying failed turns, auto-continuation when output
hits the token limit, per-session reply language, reasoning budgets, and
presets.

### Memory and retrieval

Hindsight gives each session its own memory. Recall is locked to the active
session — the bridge never pulls in broad user or character memory during
generation. You can also store explicit facts with `/remember` or rebuild
summaries with `/summarize`.

The Data Bank adds local full-text search and optional embeddings for PDF,
DOCX, TXT, Markdown, JSON, YAML, CSV, HTML, and XML files. Everything is
bounded — file sizes, page counts, extraction limits — so a large upload can't
run away with resources.

### Media and groups

Send photos, documents, or voice messages and the bridge routes them
appropriately — vision analysis, Data Bank ingestion, card validation, or
transcription. Automatic TTS can speak quoted dialogue from both your messages
and character replies. Expression sprites can be sent automatically. And
Telegram Forum Topics can host multi-character group sessions with
round-robin, contextual, manual, or autonomous turn modes.

## Requirements

- Python 3.11
- A Telegram bot token and your Telegram user ID
- A local SillyTavern installation with at least one PNG character card
- An OpenAI-compatible chat provider (or an Anthropic Messages provider)
- Optional: Hindsight, embeddings, image generation, STT, and TTS services

## Install and test

Set up a clean Python environment and install the locked dependencies:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install -r requirements-dev.txt
```

Run the tests from the repository root:

```bash
python3 -m unittest discover -s tests -q
python3 -m compileall -q bridge tests
```

## Configuration

Keep your real environment file outside Git — there's no reason for secrets to
live in version control:

```bash
mkdir -p ~/.local/share/sillytavern-telegram
cp .env.example ~/.local/share/sillytavern-telegram/.env
chmod 600 ~/.local/share/sillytavern-telegram/.env
```

The bare minimum you need to fill in:

```dotenv
SILLYTAVERN_TELEGRAM_BOT_TOKEN=replace-me
SILLYTAVERN_TELEGRAM_ALLOWED_USERS=123456789
SILLYTAVERN_DIR=/path/to/SillyTavern
SILLYTAVERN_DEFAULT_CHARACTER=example-character.png
SILLYTAVERN_MODEL=provider-one::provider-one/model-a
```

If your setup needs custom paths, these overrides are available:

```dotenv
SILLYTAVERN_ENV_FILE=/path/to/private/.env
SILLYTAVERN_BRIDGE_HOME=/path/to/private-bridge-data
SILLYTAVERN_PROVIDER_CONFIG=/path/to/private/providers.yaml
SILLYTAVERN_BRIDGE_SOURCE_DIR=/path/to/sillytavern-telegram-bridge
SILLYTAVERN_CHARACTER_DIR=/path/to/SillyTavern/data/default-user/characters
SILLYTAVERN_WORLD_DIR=/path/to/SillyTavern/data/default-user/worlds
SILLYTAVERN_SYSTEM_PROMPTS_DIR=/path/to/SillyTavern/data/default-user/sysprompt
```

By default, the bridge reads native Persona settings from:

```text
$SILLYTAVERN_DIR/data/default-user/settings.json
```

and Persona avatars from:

```text
$SILLYTAVERN_DIR/data/default-user/User Avatars/
```

Each provider entry in your private catalog names its own `api_key_env` for
credentials. **Never** put real keys in Git, README files, release assets, or
Telegram messages.

### Hindsight and Data Bank

Hindsight is optional. When enabled, point it at your private instance:

```dotenv
HINDSIGHT_API_URL=http://127.0.0.1:8890
HINDSIGHT_API_KEY=
```

If Hindsight cleanup can't be verified during a reset or session deletion, the
bridge refuses to delete local data. This prevents orphaned remote documents
that no longer match any local session.

The Data Bank works locally through FTS5. For semantic retrieval, configure an
OpenAI-compatible embeddings endpoint:

```dotenv
SILLYTAVERN_RAG_EMBEDDING_URL=http://127.0.0.1:8891/v1/embeddings
SILLYTAVERN_RAG_EMBEDDING_MODEL=text-embedding-3-small
SILLYTAVERN_RAG_EMBEDDING_DIMENSIONS=1536
SILLYTAVERN_RAG_EMBEDDING_REVISION=1
```

Use loopback HTTP for local services. Anything external needs HTTPS and an
explicit host allowlist. Reindex the Data Bank when you change the embedding
model, dimensions, or revision.

## Provider catalog

The bridge keeps its own provider catalog. It doesn't read SillyTavern's
provider settings or any other application's configuration. Start from the
example file:

```bash
cp config/providers.example.yaml /path/to/private/providers.yaml
```

A typical entry looks like this:

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

Set `streaming: true` only if the endpoint actually returns SSE chunks. For
endpoints without a `GET /models` route, set `discover_models: false` and use
`health_check: chat_completion` — the health panel will run a small streaming
probe instead of reporting a misleading failure.

OpenCode Muse works as a separate keyless transport when configured in the
catalog. Just because a model shows up in the catalog doesn't mean it's
runnable — the bridge validates adapter, transport, endpoint, and streaming
settings before attempting inference.

The provider panel is the canonical way to select models:

```text
/providers          Open the provider and model panel
/providers health   Run provider health checks
/providers refresh  Refresh discoverable model catalogs
```

## Using the bot

The bot is built around panels. Send a command, get a menu, then tap buttons or
send the next message to complete the action. It keeps things predictable and
prevents accidental changes.

### Everyday commands

```text
/start              Show the greeting when Persona, World Info, and System Prompt are enabled; otherwise show recommendations
/greeting           Choose the primary or an alternate character-card opening greeting
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

### How to format your messages

When you send a message, you can use a couple of markers to tell the bridge
what's action and what's dialogue:

```text
*She walks toward the doorway.*   Action/narration
"I heard something outside."       Dialogue — also queued for TTS
I heard something outside.           Plain dialogue, no markers
**bold text**                         Literal text, not an action marker
```

Here's how it works:

- `*text*` (single stars) → sent to the model as an action. The stored
  transcript stays unchanged.
- `"text"` (straight double quotes) → treated as dialogue and queued for TTS
  when `/voice on` is enabled. This works for both your messages and character
  replies.
- `**text**` (double stars) → preserved literally. Not an action, not spoken.
- Curly or "smart" quotes → not recognized as TTS delimiters. Use straight
  quotes.

### Panels and cancellation

Some commands need you to type something — a session name, a memory fact, an
image prompt, etc. When that happens, the bot opens a scoped input step and
waits for your next message.

Send `/cancel` to back out. Invalid input keeps the prompt open with feedback.
Valid input applies the change and returns you to the panel. Pending inputs
expire after a while, and stale callbacks are rejected rather than applied to
the wrong session.

`/stscript` only exposes allowlisted bridge actions. It can't run shell
commands, touch the filesystem, or make network requests. Its Reset action goes
through the normal confirmation flow.

## Sessions and memory

### Session lifecycle

`/new` asks for a name (1–80 characters), creates a fresh session, and switches
to it. `/session` lists all sessions and lets you switch, create, or delete
inactive ones. Both the custom name and the internal session ID show up in
`/status`.

Each session carries its own:

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

`/reset` clears the active session without deleting it:

1. Opens a confirmation panel — nothing is touched yet.
2. On confirm, purges Hindsight documents for that session only.
3. Clears the local conversation, variants, failed turns, summary, and data.
4. The session stays available, now empty.
5. Does **not** send the character greeting. It sends a short reset-complete
   confirmation instead. Type `start` if you want the character's opening message.

The confirmation text spells out exactly what gets deleted: the conversation,
Hindsight memories, SQLite session data, and session documents.

`/session` deletion is stricter. It only targets inactive sessions, refuses
sessions with running or queued jobs, and requires Hindsight cleanup to succeed
before removing local data. If cleanup can't be verified, the session is kept.
Other sessions and their memories are never touched.

### Memory boundaries

All automatic Hindsight recall and `/memory search` are scoped to the active
`session:<session_id>` tag. `/remember` stores one fact at a time through a
scoped prompt. `/summarize` rebuilds the active session's summary from its
stored transcript.

Memory and retrieved documents are treated as untrusted context — bounded and
sanitized before they reach the model.

## Generation and delivery

### What goes into a prompt

When you send a message, the bridge assembles the prompt from:

```text
Character card fields and example dialogue
Native Persona description
Active World Info entries
Selected System Prompt
Session Author's Note
Conversation history and summary
Active-session Hindsight recall
Data Bank references
Response-language instruction
Provider-specific generation settings
```

Your original text and the model's replies are stored as-is. The single-star
action formatting is only added to the prompt representation — it never
rewrites what's stored.

### Streaming and long responses

With streaming on, the bot posts a temporary preview that updates as the model
generates. Once the full response is ready, the preview is removed and the
final message is sent through the normal splitter.

Long messages are split at Telegram's UTF-16 limit. The splitter looks for
natural break points in this order:

1. Paragraph boundaries
2. Newlines
3. Sentence boundaries
4. Whitespace
5. A hard UTF-16-safe cut

When a provider stops at the token limit (`finish_reason: length`), the bridge
tries bounded auto-continuation — including cases where a reasoning-only
stream produced no visible text. `/continue` is always there if you want
another deliberate segment.

### Variants and recovery

- `/regen` → new response variant
- `/swipe` → browse and pick from stored variants
- `/branch` → switch the active response branch
- `/edit` → replace your last message and regenerate
- `/retry` → replay a failed response without creating a duplicate

Every operation gets a durable marker, so restart recovery and Telegram
delivery are idempotent — no duplicates after a crash.

## Native SillyTavern data

### Characters

`/character` lets you pick from native PNG cards, view metadata, get upload
guidance, and delete cards through a protected flow. Uploaded cards are
validated as real SillyTavern PNGs. Backups are made before any replacement or
deletion.

The active card and any cards referenced by sessions or groups are protected —
you can't accidentally delete a card that's in use.

### Personas

`/persona` reads and writes native SillyTavern Persona settings and avatar
storage. There's no second Persona catalog — the bridge uses what SillyTavern
already has. The active Persona and any Personas referenced by other sessions
are protected from the inactive-delete picker.

If a session has no valid Persona selected, the bridge tries to resolve
SillyTavern's native default. If there's no explicit default but exactly one
Persona exists, that one is used. If nothing safe can be found, the bridge
falls back to a generic label rather than exposing a private identity.

### World Info and System Prompts

`/world` selects or disables one or more native World Info JSON files. Active
lorebooks are path-validated and merged deterministically at prompt time.

`/systemprompt` reads native System Prompts from:

```text
$SILLYTAVERN_DIR/data/default-user/sysprompt/
```

JSON files use `name` and `content` fields. TXT files work as a fallback. The
bridge currently ignores native `post_history` fields. Prompt bodies stay
private — menus and `/status` only show labels or status.

### Expressions

`/expression` supports automatic classification, manual sprite selection, and
off mode. Sprites are sent only when the effective expression actually changes.
If a matching sprite isn't available, the bridge falls back to a neutral
sprite, then the character avatar, and finally text-only.

## Voice, images, and documents

### Automatic TTS

Turn on `/voice on` and the bridge will speak any dialogue wrapped in straight
double quotes — from both your messages and character replies:

```text
You send:     "Please wait for me."
Character:    *turns to look* "I will wait."
```

Both quoted lines get queued for TTS. Actions, narration, and unquoted text
stay text-only. Ordinary text messages also disable Telegram link previews, so a
character card URL cannot turn into a footer image. The transcript is always stored
as plain text, and TTS jobs run
in the utility queue with idempotent operation IDs so retries never duplicate
audio.

### Voice input

`/voice_input` sets up transcription for Telegram voice messages using
Faster-Whisper. Pick a STT model and choose:

```text
Auto
A fixed 2–8 letter language code
Scoped User input
```

Transcription runs as a durable background job, then enters the session as a
normal text turn.

### Images

`/imagine` stays disabled until you explicitly configure an image provider with
an endpoint, model, and output size. Prompts must be 1–4,000 characters.
Chat-only models are never silently reused for image generation.

Telegram photos with captions are queued for vision analysis when the active
model supports vision. If it doesn't, the bridge fails closed — no changes to
the transcript.

### Documents and Data Bank

Telegram Documents are routed to either character-card validation or Data Bank
ingestion. The Data Bank accepts PDF, DOCX, TXT, Markdown, JSON, YAML, CSV,
HTML, and XML. File size, PDF page count, DOCX expansion, extracted text
length, and embedding work are all bounded.

## Live Sync and Forum Topic groups

### Live Sync

Live Sync is off by default. It uses SillyTavern's loopback API and does an
initial reconciliation before turning on realtime updates. If anything looks
wrong — auth errors, schema mismatches, sync-ID mismatches, two-sided
conflicts — sync stops rather than silently picking a side.

```dotenv
SILLYTAVERN_SYNC_API_URL=http://127.0.0.1:8000
SILLYTAVERN_SYNC_API_INTERVAL_SECONDS=2
SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS=10
SILLYTAVERN_SYNC_API_HANDLE=
SILLYTAVERN_SYNC_API_PASSWORD=
```

The bridge doesn't install extensions, sync chat files directly, or expose Live
Sync credentials in Telegram.

### Forum Topic groups

`/group` only works inside a Telegram Forum Topic. Each topic gets its own
isolated session and group state. The setup wizard lets you create a group
session, pick characters and World Info, and choose a turn mode:

- **Round-robin:** characters speak in a set order.
- **Contextual:** the bridge picks the next speaker based on context.
- **Manual:** an owner claims or passes the turn; ownership is verified
  server-side.
- **Autonomous:** characters continue on their own within configured bounds.

Group state changes and generated turns are durable. Topic IDs are kept
internal for isolation and only attached to Telegram payloads when sending.

## Reliability, privacy, and safety

- **Keep secrets out of Git.** That includes `.env`, provider YAML, SQLite
  files, logs, cards, Personas, and private prompts.
- **Lock down access** with `SILLYTAVERN_TELEGRAM_ALLOWED_USERS`.
- **HTTPS for anything external.** Loopback is fine for local services.
- **Credentials are never displayed.** Provider hosts are validated before
  keys are attached. Health output never shows them.
- **Everything from outside is untrusted.** Provider catalogs, model responses,
  uploaded documents, memories, and RAG references are all treated as
  untrusted input and bounded before use.
- **Uploads are bounded.** File sizes, document expansion, PDF pages, extracted
  text, image prompts, memory context, and embedding work all have limits.
- **Persistence before acknowledgment.** Updates are saved before the bot
  acknowledges Telegram, and update IDs are deduplicated.
- **FIFO ordering per chat and topic.** Failed turns are stored before offset
  advancement so `/retry` can replay them.
- **Panels clean up after themselves.** Keyboards close and bindings are
  removed on Cancel, Close, expiry, or stale callbacks.
- **Unknown commands are rejected** before normal generation — no accidental
  messages to the character.
- **`/stscript` is allowlisted** and cannot execute arbitrary commands.
- **Use the systemd hardening template** for production deployments.

## Run and update

Check your installation without starting the bot:

```bash
python sillytavern_telegram_bridge.py --check
```

This loads the environment, validates the native card, checks permissions, tests
optional Live Sync auth, and verifies the Telegram bot identity.

Start the bridge:

```bash
python sillytavern_telegram_bridge.py
```

A systemd template lives at `systemd/sillytavern-telegram.service.example`. Set
`SILLYTAVERN_BRIDGE_SOURCE_DIR` if `/update` runs from a live launcher copy.

`/update` is confirmation-gated. If you're already on the latest release, it
does nothing. Otherwise it requires a clean checkout and fast-forwards only to
the exact published release tag shown in the panel, then syncs the live bridge
and restarts the service.

## Architecture

The runtime loads domain modules into one shared namespace via
`bridge/runtime.py`. Here are the main boundaries (not an exhaustive list):

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

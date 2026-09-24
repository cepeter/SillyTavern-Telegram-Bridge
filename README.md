# 🌉 SillyTavern Telegram Bridge

[![CI](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/cepeter/SillyTavern-Telegram-Bridge?display_name=tag)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/releases/latest)
[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

> Talk to your SillyTavern characters from Telegram. That's the whole pitch.

---

You know that feeling when you've spent hours building the perfect character card,
tuning World Info, crafting personas — and then you step away from your computer
and can't talk to any of them? or when you take a shit, and want to chat with your wai-fu/s when looking for inspiration?, no more, pals.

That's what this fixes.

The bridge sits between Telegram and your model provider. It handles sessions,
memory, voice, images, and all the plumbing. SillyTavern stays in charge of
character cards, Personas, World Info, and System Prompts. The bridge just reads
those files and lets you chat from your phone.

It doesn't patch SillyTavern. It doesn't launch SillyTavern. Think of it as a
remote control that reads the same files SillyTavern uses, then lets you
text your characters from anywhere (and sync it to SillyTavern too, so you can continue later)

---

## 📋 Contents

- [✨ What it does](#-what-it-does)
- [🔧 Requirements](#-requirements)
- [📦 Install and test](#-install-and-test)
- [⚙️ Configuration](#-configuration)
- [🌐 Provider catalog](#-provider-catalog)
- [🤖 Using the bot](#-using-the-bot)
- [🗂️ Sessions and memory](#-sessions-and-memory)
- [📝 Generation and delivery](#-generation-and-delivery)
- [🎭 Native SillyTavern data](#-native-sillytavern-data)
- [🎙️ Voice, images, and documents](#-voice-images-and-documents)
- [🔄 Live Sync and Forum Topic groups](#-live-sync-and-forum-topic-groups)
- [🎬 Director goals and scene state](#-director-goals-and-scene-state)
- [🔒 Reliability, privacy, and safety](#-reliability-privacy-and-safety)
- [🚀 Run and update](#-run-and-update)
- [🏗️ Architecture](#-architecture)
- [📄 License](#-license)

---

## ✨ What it does

### 💬 Chat with your characters from Telegram

Send a message like you would to any contact. The bridge pulls together your
character card, Persona, World Info, System Prompt, conversation history, memory,
and any relevant documents — builds the prompt — and sends it to your model
provider. The reply comes back as a normal Telegram message.

If your provider supports streaming, you'll even see a live preview while it
generates. Pretty satisfying, honestly.

You can run multiple named sessions in the same chat. Each one keeps its own
transcript, model, settings, Persona, World Info, notes, variants, and group
state. Panel buttons are tied to the session that opened them, so an old menu
can't accidentally mess with a different session.

### 📂 Reads your native SillyTavern data

No duplicate catalogs. No sync conflicts. The bridge works directly with the
files SillyTavern already uses:

| What | Where |
|---|---|
| Character cards (PNG) | `data/default-user/characters/` |
| World Info / lorebooks | `data/default-user/worlds/` |
| System Prompts | `data/default-user/sysprompt/` |
| Persona names & descriptions | `settings.json` |
| Persona avatars | `data/default-user/User Avatars/` |
| Expression sprites | Tied to the active character |

Persona review and editing happen from `/persona`. The description appears in
Telegram's copyable code block above the edit buttons — handy when you're
tweaking a description on mobile.

When you swap characters or Personas, the bridge validates everything, checks
for protected targets, makes a backup, and confirms the change before reporting
success. **No silent overwrites. Ever.**

### 🔌 Providers and generation

A private provider catalog drives model selection. You can use
OpenAI-compatible Chat Completions, Anthropic Messages, the keyless OpenCode
Muse `/responses` transport, or an opt-in image provider — all from the same
panel.

Beyond basic generation, the bridge handles:

- 🎲 Response variants and branches
- ✏️ Editing your last message and regenerating
- 🔁 Retrying failed turns
- ➡️ Auto-continuation when output hits the token limit
- 🌐 Per-session reply language
- 🧠 Reasoning budgets
- 💾 Presets

Health checks, model discovery, endpoint validation, and streaming configuration
are all built in.

### 🧠 Memory and retrieval

Hindsight gives each session its own memory. Recall is locked to the active
session — the bridge never pulls in broad user or character memory during
generation. You can also store explicit facts with `/remember` or rebuild
summaries with `/summarize`.

The Data Bank adds local full-text search and optional embeddings for PDF,
DOCX, TXT, Markdown, JSON, YAML, CSV, HTML, and XML files. Everything is
bounded — file sizes, page counts, extraction limits — so a large upload
can't run away with resources.

### 🎨 Media and groups

Send photos, documents, or voice messages and the bridge routes them where they
need to go — vision analysis, Data Bank ingestion, card validation, or
transcription. Automatic TTS can speak quoted dialogue from both your messages
and character replies. Expression sprites can be sent automatically.

And Telegram Forum Topics can host multi-character group sessions with
round-robin, contextual, manual, or autonomous turn modes.

---

## 🔧 Requirements

- **Python 3.11**
- A Telegram bot token and your Telegram user ID
- A local SillyTavern installation with at least one PNG character card
- A configured Story model from the private provider catalog — OpenAI-compatible, Anthropic Messages, or OpenCode Muse
- Optional: a separate Utility model, Hindsight, embeddings, image generation, STT, and TTS services

---

## 📦 Install and test

Set up a clean Python environment and install the locked runtime plus development
dependencies:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install -r requirements-dev.txt
```

Run the same core checks used by CI from the repository root:

```bash
python -m pytest -q -n 2 --dist=loadfile
python tools/static_analysis.py

TARGETS=$(python tools/static_analysis.py --print-targets | tr '\n' ' ')
python -m ruff check $TARGETS
python -m mypy $TARGETS
python -m compileall -q bridge tests tools
```

`main` is protected by the `test`, `dependency-audit`, and
`static-analysis` checks. The static policy also rejects import cycles and
reverse dependencies from the isolated service/port layer.

---

## ⚙️ Configuration

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

The default character name used as a display fallback is derived from
`SILLYTAVERN_DEFAULT_CHARACTER` (the filename without extension), so renaming
the file renames the fallback too.

The executable loads the environment file **before importing application
modules**. The default file is
`~/.local/share/sillytavern-telegram/.env`, or use
`SILLYTAVERN_ENV_FILE` to choose another path. A missing file is allowed when
the process environment already supplies configuration; existing process
environment values always take precedence over file values. Environment files
use strict `KEY=VALUE` syntax (with optional `export ` and matching quotes),
and malformed assignments stop startup instead of being silently ignored.

Use `python3 sillytavern_telegram_bridge.py` as the supported entry point.
`python -m bridge.main` imports the application module before the launcher
bootstrap can run, so it does not load the environment file and is not the
supported startup path. It can only rely on values already present in the
process environment.


`SILLYTAVERN_TELEGRAM_ALLOWED_USERS` is required and every retained
comma-separated value must be a numeric Telegram user ID.

**Need custom paths?** These overrides are available:

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
credentials. **Never put real keys in Git, README files, release assets, or
Telegram messages.**

### Smart context compaction

Long sessions use a budget-aware prompt planner instead of a fixed recent-history
cutoff. The bridge considers up to 96 recent transcript messages by default,
keeps the newest turns, then reduces older history, Data Bank context, Hindsight
recall, and finally the continuity summary when needed. Character and fixed
system instructions plus the current user turn are never silently truncated.

The defaults assume a 32k-token context window with 4k reserved for output:

```dotenv
SILLYTAVERN_CONTEXT_WINDOW_TOKENS=32768
SILLYTAVERN_CONTEXT_OUTPUT_RESERVE_TOKENS=4096
SILLYTAVERN_CONTEXT_HISTORY_CANDIDATES=96
```

Set the context window to match the models you actually use. `/prompt` shows
the current estimated input budget and history candidate limit.

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

---

## 🌐 Provider catalog

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
catalog. OpenAI-compatible relay responses are normalized by the bridge when
needed. A model appearing in the catalog does not automatically make it runnable:
transport, endpoint, credentials, and streaming settings are validated before
inference.

The provider panel is the canonical way to select models:

```text
/providers          Open the provider and model panel
/providers health   Run provider health checks
/providers refresh  Refresh discoverable model catalogs
```

---

## 🤖 Using the bot

The bot is built around panels. Send a command, get a menu, then tap buttons or
send the next message to complete the action. It keeps things predictable and
prevents accidental changes.

### 📋 Everyday commands

| Command | What it does |
|---|---|
| `/start` | Show the greeting when Persona, World Info, and System Prompt are enabled |
| `/status` | Show formatted read-only session status text |
| `/new` | Create and activate a named isolated session |
| `/reset` | Confirm an active-session reset and memory purge |
| `/session` | Switch, create, or delete an inactive session |
| `/character` | Manage native character cards |
| `/persona` | Choose, create, edit, or disable a native Persona |
| `/world` | Choose or disable World Info/lorebooks |
| `/systemprompt` | Choose a native SillyTavern System Prompt |
| `/note` | Configure the session Author's Note |
| `/providers` | Choose a provider and model |

### 🔄 Replies and generation

| Command | What it does |
|---|---|
| `/settings` | Configure reasoning and generation values |
| `/stream` | Toggle streaming preview |
| `/preset` | Use, save, or delete a generation preset |
| `/prompt` | Open the read-only prompt inspector and safe prompt diagnostics |
| `/regen` | Generate another response variant |
| `/swipe` | Browse stored response variants |
| `/branch` | Choose the active response branch |
| `/continue` | Continue the latest assistant response |
| `/edit` | Edit the latest user turn and regenerate |
| `/retry` | Retry the latest failed response |
| `/language` | Choose the model reply language |
| `/expression` | Choose native expression behavior |
| `/macro` | Preview supported SillyTavern macros |
| `/stscript` | Open the allowlisted STscript panel |
| `/cancel` | Cancel the current pending input |

### 🎙️ Voice, files, memory, and groups

| Command | What it does |
|---|---|
| `/voice` | Toggle automatic quote-driven TTS |
| `/voice_input` | Configure transcription, STT model, and language |
| `/imagine` | Generate an image through an enabled image provider |
| `/memory` | Open Hindsight memory and search controls |
| `/memory search <query>` | Search Hindsight memory within the active session |
| `/memory curated` | View or refresh curated durable memory |
| `/remember` | Store one explicit long-term fact |
| `/summarize` | Confirm before regenerating the active-session summary |
| `/databank` | Open Data Bank RAG controls |
| `/sync` | Open Live API Sync controls |
| `/group` | Open Forum Topic group controls |
| `/group goal` | View the hidden Director scene objective |
| `/group goal <objective>` | Set a session-local hidden objective for Director mode |
| `/scene` | Open structured scene state panel |
| `/scene refresh` | Rebuild scene state with the utility model |
| `/scene clear` | Clear structured scene state |
| `/help` | Open the interactive command guide |
| `/help <command>` | Show detailed behavior for one command |
| `/update` | Check and, after confirmation, update the bridge |

> `/tts` is not a command. Automatic voice is controlled by `/voice`.

### How to format your messages

When you send a message, you can use a couple of markers to tell the bridge
what's action and what's dialogue:

```text
*She walks toward the doorway.*    → Action/narration
"I heard something outside."        → Dialogue (also queued for TTS)
I heard something outside.          → Plain dialogue, no markers
**bold text**                       → Literal text, not an action marker
```

Here's how it works:

- `*text*` (single stars) → sent to the model as an action. The stored
  transcript stays unchanged.
- `"text"` (straight double quotes) → treated as dialogue and queued for TTS
  when `/voice on` is enabled. Works for both your messages and character
  replies.
- `**text**` (double stars) → preserved literally. Not an action, not spoken.
- Curly or "smart" quotes → not recognized as TTS delimiters. Use straight
  quotes.

### Panels and cancellation

Some commands need you to type something — a session name, a memory fact, an
image prompt. When that happens, the bot opens a scoped input step and waits
for your next message.

Send `/cancel` to back out. Invalid input keeps the prompt open with feedback.
Valid input applies the change and returns you to the panel. Pending inputs
expire after a while, and stale callbacks are rejected rather than applied to
the wrong session.

`/stscript` only exposes allowlisted bridge actions. It can't run shell
commands, touch the filesystem, or make network requests. Its Reset action goes
through the normal confirmation flow.

---

## 🗂️ Sessions and memory

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

`/reset` clears the active session **without deleting it**:

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

---

## 📝 Generation and delivery

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

When a provider stops at the token limit (`finish_reason: length`), the
bridge tries bounded auto-continuation. Streaming continuation stays streaming,
keeps the preview cumulative across segments, honors cancellation between and
during continuation requests, and makes at most three automatic continuation
requests after the initial visible segment. Reasoning-only length stops can
retry with a larger output budget. `/continue` is always available for a
deliberate additional segment.

### Variants and recovery

| Command | Action |
|---|---|
| `/regen` | New response variant |
| `/swipe` | Browse and pick from stored variants |
| `/branch` | Switch the active response branch |
| `/edit` | Replace your last message and regenerate |
| `/retry` | Replay a failed response without creating a duplicate |

Every operation gets a durable marker, so restart recovery and Telegram
delivery are idempotent — **no duplicates after a crash**.

---

## 🎭 Native SillyTavern data

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
lorebooks are path-validated and merged deterministically at prompt time. The
panel also supports uploading a `.json` World Info document. Uploads must use
SillyTavern's `{ "entries": { ... } }` format, are limited to 10 MB, and refuse
to overwrite an existing filename. The trash button deletes only inactive
World Info files; files referenced by any session are protected.

`/systemprompt` reads native System Prompts from:

```text
$SILLYTAVERN_DIR/data/default-user/sysprompt/
```

JSON files use `name` and `content` fields. TXT files in that directory are
also supported. The directory is the sole System Prompt source; the bridge no
longer supports a separate single-file prompt fallback. The bridge currently
ignores native `post_history` fields. Prompt bodies stay private — menus and
`/status` only show labels or status.

### Expressions

`/expression` supports automatic classification, manual sprite selection, and
off mode. Sprites are sent only when the effective expression actually changes.
If a matching sprite isn't available, the bridge falls back to a neutral
sprite, then the character avatar, and finally text-only.

---

## 🎙️ Voice, images, and documents

### Automatic TTS

Turn on `/voice on` and the bridge will speak any dialogue wrapped in straight
double quotes — from both your messages and character replies:

```text
You send:     "Please wait for me."
Character:    *turns to look* "I will wait."
```

Both quoted lines get queued for TTS. Actions, narration, and unquoted text
stay text-only. Ordinary text messages also disable Telegram link previews, so
a character card URL can't turn into a footer image. The transcript is always
stored as plain text, and TTS jobs run in the utility queue with idempotent
operation IDs so retries never duplicate audio.

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

---

## 🔄 Live Sync and Forum Topic groups

### Live Sync

Live Sync is off by default. It uses SillyTavern's loopback API and processes
the API's chat-record response directly. The bridge performs an initial
reconciliation before turning on realtime updates. If anything looks wrong —
auth errors, schema mismatches, sync-ID mismatches, two-sided conflicts, or
oversized records — **sync stops rather than silently picking a side**.

```dotenv
SILLYTAVERN_SYNC_API_URL=http://127.0.0.1:8000
SILLYTAVERN_SYNC_API_INTERVAL_SECONDS=2
SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS=10
SILLYTAVERN_SYNC_API_HANDLE=
SILLYTAVERN_SYNC_API_PASSWORD=
```

Live API Sync is the only conversation synchronization path. The bridge does
not install extensions, poll chat files, import/export JSONL transcripts, or
expose Live Sync credentials in Telegram.

### Forum Topic groups

`/group` only works inside a Telegram Forum Topic. Each topic gets its own
isolated session and group state. The setup wizard lets you create a group
session, pick characters and World Info, and choose a turn mode:

| Mode | How it works |
|---|---|
| **Round-robin** | Characters speak in a set order |
| **Contextual** | The bridge picks the next speaker based on context |
| **Manual** | An owner claims or passes the turn; ownership is verified server-side |
| **Autonomous** | Characters continue on their own within configured bounds |

Group state changes and generated turns are durable. Topic IDs are kept
internal for isolation and only attached to Telegram payloads when sending.

---

## 🎬 Director goals and scene state

Director mode can keep a hidden, session-local objective for a Forum Topic group:

```text
/group goal <objective>   Set or replace the objective
/group goal               Show the current objective
/group goal status        Show the current objective
/group goal clear         Remove it
```

The objective helps the invisible Director choose the next speaker and guide the
scene without entering the roleplay transcript or being revealed to the
characters. The operator can inspect it with `/group goal status`. It is bounded
to 1,200 characters and applies only while the session is in Director mode.

The bridge also maintains structured scene state — location, weather, participants,
known facts, and other bounded continuity details — outside the transcript:

```text
/scene          Show the current structured state
/scene refresh  Rebuild it with the configured utility model
/scene clear    Remove it
```

Scene refresh is a background utility-model task. It is optional, session-scoped,
and never replaces the original conversation history.

---

## 🔒 Reliability, privacy, and safety

- **🔐 Keep secrets out of Git.** That includes `.env`, provider YAML, SQLite
  files, logs, cards, Personas, and private prompts.
- **👥 Lock down access** with the required numeric
  `SILLYTAVERN_TELEGRAM_ALLOWED_USERS` allowlist.
- **🌐 HTTPS for anything external.** Loopback is fine for local services.
- **🚫 Credentials are never displayed.** Provider hosts are validated before
  keys are attached. Health output never shows them.
- **📦 Everything from outside is untrusted.** Provider catalogs, model
  responses, uploaded documents, memories, and RAG references are all treated
  as untrusted input and bounded before use.
- **📤 Uploads are bounded.** File sizes, document expansion, PDF pages,
  extracted text, image prompts, memory context, and embedding work all have
  limits.
- **💾 Persistence before acknowledgment.** Updates are saved before the bot
  acknowledges Telegram, and update IDs are deduplicated.
- **📋 FIFO ordering per chat and topic.** Failed turns are stored before
  offset advancement so `/retry` can replay them.
- **🧹 Panels clean up after themselves.** Keyboards close and bindings are
  removed on Cancel, Close, expiry, or stale callbacks.
- **⚠️ Unknown commands are rejected** before normal generation — no accidental
  messages to the character.
- **✅ `/stscript` is allowlisted** and cannot execute arbitrary commands.
- **🧱 Architecture is CI-enforced.** The repository rejects import cycles and
  reverse imports from the isolated service/port layer; Ruff and mypy run on
  that stabilized boundary on every protected PR.
- **🛡️ Use the systemd hardening template** for production deployments.

---

## 🚀 Run and update

Check your installation without starting the bot:

```bash
python sillytavern_telegram_bridge.py --check
```

This loads the environment, validates the native card, checks permissions, tests
optional Live Sync auth, and verifies the Telegram bot identity.

### Pre-production database reset

SQLite now starts from one `initial_schema` migration containing the complete
current schema and constraints. Databases created by earlier pre-production
revisions are intentionally unsupported.

Before starting this revision with an older development database, stop the
bridge, archive the existing SQLite file if you need its data for inspection,
then remove or rename the active database and let the bridge create a fresh
one. The bridge does not automatically convert or delete an older database.

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

---

## 🏗️ Architecture

The bridge uses ordinary imports, explicit composition, and an acyclic internal
dependency graph. Startup enters through `sillytavern_telegram_bridge.py`
and composes required services/ports in `bridge.main`; there is no runtime
loader, module override chain, or shared execution namespace.

The current boundaries are deliberately small:

- `sillytavern_telegram_bridge.py` bootstraps the environment and starts the app.
- `bridge.main` composes required services and ports.
- Application services own conversation, jobs, groups, memory, Persona, sync,
  pending input, and Director policy behavior.
- Provider routing/transport and Telegram delivery sit behind explicit ports.
- SQLite schema/persistence, RAG, Help, and Telegram ingress each have focused
  owners rather than a shared runtime namespace.

`tools/static_analysis.py` enforces two repository invariants in CI:

1. the complete `bridge` import graph must stay acyclic;
2. the stabilized service/port modules may not import back into `bridge.*`.

Historical migration plans are intentionally not kept in the product tree. Git
history and the changelog preserve that development history without presenting
retired architecture as current documentation.

---

## 📄 License

GNU General Public License v3.0. See [LICENSE](LICENSE).

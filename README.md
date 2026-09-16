# SillyTavern Telegram Bridge

[![CI](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/cepeter/SillyTavern-Telegram-Bridge?display_name=tag)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> A lightweight Telegram sidecar for SillyTavern-style character chat.

The bridge keeps chat state in SQLite, reads compatible character data from
SillyTavern, and routes generation through an OpenAI-compatible provider. It
runs beside SillyTavern without patching or executing the SillyTavern source.

## Contents

- [✨ Features](#-features)
- [🧩 Requirements](#-requirements)
- [🔗 SillyTavern prerequisite](#-sillytavern-prerequisite)
- [⚙️ Configuration](#️-configuration)
- [🧭 Command guide](#-command-guide)
- [🔄 Synchronization](#-synchronization)
- [🔐 Security and privacy](#-security-and-privacy)
- [🏗️ Architecture](#️-architecture)

## ✨ Features

- Character-card discovery from PNG `chara` metadata.
- Character selection, upload, info, and safe deletion.
- Per-session chat history, personas, multiple active World Info/lorebooks, Author's Note, and generation settings.
- Provider/model selection from a configurable catalog.
- `/regen`, `/swipe`, `/branch`, `/continue`, and native Telegram edit handling.
- Image input and local Faster-Whisper voice transcription.
- Text-to-speech responses through Edge TTS.
- Hindsight long-term memory with user, character, and session scopes.
- Auto-summary and bounded context compression.
- Data Bank RAG for PDF, DOCX, TXT, Markdown, JSON, YAML, CSV, HTML, and XML.
- FTS5 plus optional OpenAI-compatible embeddings with lexical fallback.
- Multi-character groups with round-robin, contextual, manual user-turn gating, topic-only New group sessions, and bounded autonomous modes.
- Telegram Forum Topics with topic-scoped sessions, history, queues, group state, media, and replies.
- Inline help menu with command categories.
- Live Sync through the loopback API with explicit conflict stops; manual JSONL and automatic file sync remain recovery fallbacks.
- Bounded background workers for STT, TTS, document indexing, and memory retention.
- Durable SQLite jobs for normal generation, long-running commands, voice transcription, image analysis, document imports, callbacks, and native edits, with per-chat ordering and session-aware execution.
- UTF-16-safe Telegram splitting, typed World Info editing, bounded processed-update retention, and isolated generation/utility worker pools.
- All dynamic inline panels paginate at 8 options per page with Previous/Next navigation.

## 🧩 Requirements

- Python 3.11 (canonical locked runtime)
- A Telegram bot token
- An OpenAI-compatible chat-completions provider
- SillyTavern installed locally, or equivalent character/world directories; Live Sync additionally requires the local SillyTavern server to be running
- Optional Hindsight API for long-term memory
- Optional OpenAI-compatible embedding endpoint for semantic RAG

## 🔗 SillyTavern prerequisite

This bridge **requires a SillyTavern character-card/data installation by default**. It does not launch SillyTavern, automate its browser UI, or patch its source tree. It reads the following data locally:

```text
<SILLYTAVERN_DIR>/data/default-user/characters/*.png
<SILLYTAVERN_DIR>/data/default-user/worlds/*.json
```

At least one valid PNG character card with embedded `chara` metadata is required. World Info, multiple active lorebooks, multiple character cards, and card upload management use the same directories.

Configure a non-default installation with:

```dotenv
SILLYTAVERN_DIR=/path/to/SillyTavern
SILLYTAVERN_CHARACTER_DIR=/path/to/SillyTavern/data/default-user/characters
SILLYTAVERN_CHARACTER_BACKUP_DIR=/path/to/private-character-backups
SILLYTAVERN_WORLD_DIR=/path/to/SillyTavern/data/default-user/worlds
SILLYTAVERN_DEFAULT_CHARACTER=example-character.png
```

The bridge can keep its own SQLite history and provider catalog separately, but it cannot load a character or World Info file that is not present in the configured directories.

Install dependencies in a virtual environment:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Dependency files:

- `requirements.txt` contains compatible version ranges for users who need resolver flexibility.
- `requirements.lock` contains exact versions and SHA-256 hashes for reproducible Python 3.11 installs.
- `requirements-dev.txt` pins the pytest test runner; install it only in a development/quality environment.
- The lock file is intentionally long: one package version can have many hashes for Linux, macOS, Windows, x86_64, ARM, source archives, and binary wheels.
- Use `requirements.lock` for production/release installs; use `requirements.txt` only when intentionally resolving a different environment.

## ⚙️ Configuration

Copy the example environment file and edit it outside Git:

```bash
mkdir -p ~/.config/sillytavern-telegram
cp .env.example ~/.config/sillytavern-telegram/.env
chmod 600 ~/.config/sillytavern-telegram/.env
```

For a dedicated bridge-local environment file, set this variable in the service unit:

```ini
Environment=SILLYTAVERN_ENV_FILE=%h/.config/sillytavern-telegram/.env
```

The bridge reads that file before processing credentials. Keep it outside Git.
Required variables:

```dotenv
SILLYTAVERN_TELEGRAM_BOT_TOKEN=replace-me
SILLYTAVERN_TELEGRAM_ALLOWED_USERS=123456789
SILLYTAVERN_DIR=/path/to/SillyTavern
SILLYTAVERN_DEFAULT_CHARACTER=example-character.png
SILLYTAVERN_MODEL=provider-one::provider-one/model-a
```

The selected provider's configured `api_key_env` must be present. `LLM_API_KEY` is optional and is used only for providers that intentionally use the generic fallback; provider-specific credentials are preferred and explicit provider keys fail closed.

The bridge also reads provider-specific credentials from the environment referenced by the provider catalog. Never commit credentials. External provider endpoints must use HTTPS; use `SILLYTAVERN_PROVIDER_ALLOWED_HOSTS` when you need an explicit hostname allowlist.

## 🧠 Provider catalog

The bridge has its **own provider catalog**. It does not read another application's provider configuration.

Default path is controlled by the bridge data directory. Set an explicit path for portable deployments:

```dotenv
SILLYTAVERN_BRIDGE_HOME=/path/to/private-bridge-data
SILLYTAVERN_PROVIDER_CONFIG=/path/to/sillytavern_telegram_providers.yaml
```

Start from the included example:

```bash
mkdir -p ~/.config/sillytavern-telegram
cp config/providers.example.yaml ~/.config/sillytavern-telegram/providers.yaml
export SILLYTAVERN_PROVIDER_CONFIG="$HOME/.config/sillytavern-telegram/providers.yaml"
```

The catalog contains provider names, `api_endpoint` URLs, model lists, and `api_key_env` names. The actual API keys remain only in `.env`. The included basic example has two generic Chat Completions providers and uses `PROVIDER_ONE_API_KEY` and `PROVIDER_TWO_API_KEY`.

The bridge supports OpenAI-compatible Chat Completions. Set `streaming: true` in a provider entry when that endpoint returns SSE chunks; omit it for normal JSON responses. The optional `transport: anthropic_messages` adapter is also supported when explicitly configured; it is not enabled in the basic two-provider example. `discover_models` and `extra_headers` are optional provider-specific fields; the basic example does not need them.

A provider is selectable for inference when its `adapter` or `transport` is one of `chat_completions`, `openai`, `openai_compatible`, or `anthropic_messages`. Providers with a missing or unsupported adapter remain visible as **catalog-only** and cannot generate replies. The `/providers` panel shows 8 provider/model options per page with Previous/Next navigation.

### Optional Anthropic Messages provider

Anthropic Messages is supported by the bridge adapter, but it is kept out of the basic two-provider example. Add this provider to your standalone catalog when needed:

```yaml
providers:
  provider-anthropic:
    name: Anthropic Messages Example
    api_endpoint: https://api.anthropic.com/v1
    api_key_env: ANTHROPIC_API_KEY
    transport: anthropic_messages
    anthropic_version: "2023-06-01"
    models:
      - claude-sonnet
```

Then set `ANTHROPIC_API_KEY` in the bridge environment file. The adapter converts the bridge prompt to `/messages`, sends `x-api-key` and `anthropic-version`, translates image blocks, and parses Anthropic text streaming events.

The `/providers` panel is the single provider/model entry point and reads this bridge catalog:

```text
/providers
→ provider panel
→ provider list
→ Health / Refresh models actions
→ model list
→ select model

/providers health
→ opens the same provider panel

/providers refresh
→ opens the same provider panel
```

The old `/model` command is removed, and `/model ...` is handled as an unknown
command rather than a model-selection alias. Unknown slash commands are rejected
before normal model generation. Unknown `/providers <action>` values are also
rejected; use `/providers`, `/providers health`, or `/providers refresh`.

The bridge checks `discover_models: true` providers when the catalog is opened. It calls the provider's OpenAI-compatible `/models` endpoint, keeps the result in a separate JSON cache, and falls back to static YAML models if discovery fails.

Default refresh interval:

```dotenv
SILLYTAVERN_MODEL_REFRESH_SECONDS=3600
```

Open `/providers` and tap **Refresh models** for an immediate refresh.

The cache is runtime state and must not be committed.

The bridge keeps its active SillyTavern model independent from any other agent runtime's active model. Providers without a bridge adapter are marked **catalog-only** and cannot be selected for inference. The selected model is stored per SillyTavern session.

Optional settings:

```dotenv
SILLYTAVERN_CHARACTER_DIR=/path/to/SillyTavern/data/default-user/characters
SILLYTAVERN_CHARACTER_BACKUP_DIR=/path/to/private-character-backups
SILLYTAVERN_WORLD_DIR=/path/to/SillyTavern/data/default-user/worlds
SILLYTAVERN_PERSONA_FILE=/path/to/sillytavern_personas.json
HINDSIGHT_API_URL=http://127.0.0.1:8890
HINDSIGHT_API_KEY=replace-me
SILLYTAVERN_RAG_EMBEDDING_URL=http://127.0.0.1:8891/v1/embeddings
SILLYTAVERN_RAG_EMBEDDING_MODEL=text-embedding-3-small
SILLYTAVERN_RAG_EMBEDDING_DIMENSIONS=1536
SILLYTAVERN_STT_MODEL=base
SILLYTAVERN_TTS_VOICE=id-ID-GadisNeural
```

## ▶️ Run

```bash
python sillytavern_telegram_bridge.py
```

Check prerequisites and Telegram identity:

```bash
python sillytavern_telegram_bridge.py --check
```

Normal text and image generation run in bounded background workers, so one slow provider does not block Telegram polling. Failed generation is retained and can be retried with `/retry`. Normal generation and long-running commands are handed off to a durable SQLite job before Telegram marks the update complete. Jobs capture the active session ID, run in per-chat order, and unfinished jobs are requeued after restart.

A systemd template is available at:

```text
systemd/sillytavern-telegram.service.example
```

## 🧭 Command guide

Open the interactive command guide:

```text
/help
→ Choose a topic
→ Choose a command
→ Read “What it does”
→ Back or Close
```

Categories use plain-language labels such as **Start & Sessions**, **Replies & Settings**, and **Memory & Files**. Each category renders one button per command, with up to eight command buttons per page. Use Next/Previous for larger categories, then select a command to replace the page with a clear **What it does** explanation and a Back button.

Core commands:

```text
/start                         Send the character card first message only
/status                       Show active runtime state
/new                          Create a new isolated session
/reset                        Confirm active-session reset, purge all chat Hindsight memory, and restart from the character opening greeting
/session                      Switch, create, or safely delete an inactive session
/sync                         Open Live Sync with file and manual fallback controls
/providers                    Open the synchronized provider catalog
/character                    Open character panel: select, info, delete, upload guidance
/persona                      Choose, create, edit, or disable a user persona
/world                         Open the World Info/lorebook panel
/systemprompt                  Open the configured TXT System Prompt panel
/language                      Choose the model reply language for this session
/note                         Open Author's Note panel: Off or User input
```

Author's Note panel:

```text
/note                         Open the panel
→ 🚫 Off                      Clear the session Author's Note
→ ✏️ User input               Close the panel and enter note text in the next message
→ /cancel                     Cancel pending input without changing the note
```

The note is session-scoped, validated to 2,000 characters, and never treated as
a normal chat message while the input prompt is pending. User input closes the
old panel before waiting; `/cancel` opens one fresh panel without duplicates. Use
`/note` as the canonical command; the legacy `/authornote` alias has been removed.

Session deletion:

```text
/session                      Open the session panel
→ 🗑️ Delete session           List inactive sessions only
→ Select a session            Open a separate confirmation
→ ✅ Confirm delete            Remove that session's local SQLite data
```

The active session cannot be deleted, and sessions with queued or running jobs
are protected. Deleting a session removes its transcript, variants, summary,
generation settings, group state, failed turns, and session record. Hindsight
memories are retained because the current Hindsight bank is shared by the
Telegram chat; use `/reset` when the entire chat memory should be purged.

World Info/lorebook panel:

```text
/world                         Open the panel
Tap a lorebook                 Toggle it on/off
Next / Previous                Browse 8 lorebooks per page
✅ Close                       Exit the panel; toggles apply immediately
🚫 Clear all World Info        Disable every active lorebook
```

Generation and branches:

```text
/settings                     Open session generation panel; choose reasoning
/stream                       Open streaming on/off panel
/preset                       Open preset use/save/delete panel
/macro {{char}}               Preview a supported macro
/stscript note <text>         Apply a safe session note command
/stscript reset               Open the reset confirmation panel
/regen                        Generate a new response variant
/swipe                        Browse variants with buttons
/branch                       Open the active branch selector
/continue                     Continue the latest assistant response
/edit <text>                  Edit the latest user turn and regenerate
/retry                        Retry the latest failed response
/prompt                       Inspect prompt composition
/summarize                    Force a session summary
```

`/settings` re-renders with a `Current values` line after every valid change, showing
temperature, max tokens, top P, penalties, and stop sequences. Invalid values are
rejected without changing the previous value; their feedback and input prompt are
removed together when `/cancel` or Close is used.

Response language:

```text
/language                     Open the response language panel
/language auto                Match the language of the user's latest message
/language id                  Reply in Bahasa Indonesia
/language en                  Reply in English
```

The response language is stored per session and added as a model instruction. It
controls generated replies, not SillyTavern's UI language or voice transcription.
The panel also supports `ja`, `zh`, `ko`, `es`, `fr`, `de`, `pt`, `ru`, `ar`,
`hi`, `vi`, and `th`.

Persona editing:

```text
/persona
→ Edit current persona
→ Review ID, name, description, and tags
→ Edit name, Edit description, or Edit name + description
→ Send only the field(s) you want to change

/persona
→ Create persona
→ Send: id | display name | persona description
```

Persona changes are validated, backed up, and written atomically to the private
`SILLYTAVERN_PERSONA_FILE`. Input is scoped to the session and expires; `/cancel`
leaves the existing persona unchanged.

Memory and RAG:

```text
/memory                       Open memory mode/scope panel
/memory search <query>        Search Hindsight (free-text query)
/remember <fact>              Queue an explicit memory (free text)
/databank                     Open RAG/list/remove/reindex panel
/databank search <query>      Search Data Bank (free-text query)
/sync                        Open Live Sync with file and manual fallback controls
```

## 🔄 Synchronization

### Live Sync — recommended

- Live Sync is **off by default**. Configure a loopback SillyTavern API origin,
  open `/sync`, and choose **Realtime API: toggle** for the active session.
- The bridge uses SillyTavern's supported `/csrf-token`, `/api/users/login`,
  `/api/chats/get|save`, and `/api/chats/group/get|save` routes. It does not
  patch SillyTavern or install an extension.
- A dedicated worker checks enabled bindings every two seconds by default,
  while retaining the same `sync_id`, transcript hash, swipe, and three-way
  conflict protections used by file sync.
- Only loopback hosts (`127.0.0.1`, `::1`, or `localhost`) are accepted.
  Credentials and CSRF/session state are never stored in chat metadata.
- Authentication, schema, sync-ID, or conflict failures disable realtime sync
  for that binding. File sync and manual JSONL transfer remain available.

Optional Live Sync settings:

```text
SILLYTAVERN_SYNC_API_URL=http://127.0.0.1:8000
SILLYTAVERN_SYNC_API_INTERVAL_SECONDS=2
SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS=10
SILLYTAVERN_SYNC_API_HANDLE=
SILLYTAVERN_SYNC_API_PASSWORD=
```

Quick start:

1. Start SillyTavern on a loopback address and confirm its local web UI responds.
2. Set `SILLYTAVERN_SYNC_API_URL` to that origin, then restart the bridge.
3. If SillyTavern user accounts are enabled, also set the matching API handle and
   password. Leave both empty when user accounts are disabled.
4. Open `/sync` and tap **Refresh status**. The panel must show
   `Live API sync: off (configured)` before activation.
5. Tap **Realtime API: toggle**. The bridge performs an initial reconciliation;
   realtime becomes `on` only when that succeeds.

The sync controls are scoped to the active Telegram session. **Sync now** uses
Live Sync while realtime is on; otherwise it runs file sync. **Refresh
status** only redraws current state. A sync-ID mismatch, initial divergence, or
two-sided edit conflict stops realtime instead of selecting a winner. Resolve
the divergence manually, then enable realtime again.

### Fallback and recovery

**Manual JSONL transfer** exports the active session or imports a validated
SillyTavern-compatible JSONL file into a separate session. It never overwrites
the active session. The file carries transcript, title, character reference,
persona, World Info references, Author's Note, and compatible generation settings.

**Automatic file sync** is also off by default. Enable **Auto sync: toggle** for
one session to exchange compatible chat-file changes every 30 seconds by default.
Two-sided edits stop with a conflict instead of silently choosing one side.

```text
SILLYTAVERN_SYNC_INTERVAL_SECONDS=30
SILLYTAVERN_SYNC_MAX_FILE_BYTES=10485760
SILLYTAVERN_SYNC_CHAT_DIR=/path/to/SillyTavern/data/default-user/chats
SILLYTAVERN_SYNC_GROUP_DIR=/path/to/SillyTavern/data/default-user/groups
```

Credentials must remain environment-only and must not be printed, committed, or packaged.

## 🎙️ Voice, media, and groups

```text
/tts <text>                   Send one voice message (free text)
/voice                        Open automatic TTS panel
/voice_input                  Open transcription/model/language panel
/voice_input language         Open language panel: choose Auto or User input
```

In the voice input panel, choose **Language** and then either a fixed language
choice such as `id` or `en`, **Auto** for detection, or **User input** to enter a
custom 2–8 letter language code. The custom input is session-bound and expires
after the pending-input timeout; `/cancel` leaves the existing language unchanged.

Group control panel:

```text
/group                        Open topic-only group control panel in a Forum Topic
→ Add character              Choose a character card
→ Remove character           Remove a group member
→ Choose speaker             Choose the next speaker
→ Mode                      Select round robin/contextual/manual/autonomous
→ Claim turn                Claim the user turn in manual mode
→ Pass user turn            Pass to the next known group user
→ New group session          Start a clean topic-local Character → World Info setup
→ Enable / Disable           Change group state
→ Next speaker               Advance manual mode
```

`/group` is available only inside a Telegram Forum Topic. In a direct 1-on-1
chat, the command is rejected because group sessions are topic-local. **New
group session** creates a clean session for the current topic, then opens the
Character picker and World Info picker before returning to the Group panel.

In `manual` mode, the first allowed user to send a normal message becomes the
turn owner. Other users receive a not-your-turn response and their message is
not queued. The owner can use **Pass user turn**; Telegram bots cannot disable
the native Send button in another user's client, so enforcement happens before
the durable message job is created.

Disabling group chat is idempotent: repeating **Disable** safely keeps the group
off and does not produce a callback-processing error when the panel already has
the requested state.

Telegram Forum Topics are isolated by `message_thread_id`; each topic keeps separate history, queue, group state, media routing, and replies.
## 📝 System Prompt choices

Keep reusable prompts as separate `.txt` files in a private directory and point the bridge to it. **One TXT file produces one panel choice.**

```dotenv
SILLYTAVERN_SYSTEM_PROMPTS_DIR=/path/to/sillytavern-telegram-bridge/systemprompt
```

Each file contains ordinary multiline text. The panel label comes from the filename:

```text
Stay in character, answer naturally,
and do not speak for the user.
```

If `SILLYTAVERN_SYSTEM_PROMPTS_DIR` points outside the bridge home, set `SILLYTAVERN_ENFORCE_PROMPT_PERMISSIONS=true` to apply 0700/0600 protection; otherwise the bridge only hardens bridge-local/default prompt paths.

Examples:

```text
balanced.txt → Balanced
concise.txt  → Concise
natural.txt  → Natural
```

Public examples are in `config/system_prompts.example/`: `balanced.txt`, `concise.txt`, and `natural.txt`.

Telegram commands:

```text
/systemprompt
→ opens an inline choice panel
```

The panel loads TXT choices from `SILLYTAVERN_SYSTEM_PROMPTS_DIR` and includes `Off`. Close deletes the panel message; if Telegram rejects deletion, the bridge hides the message and removes its buttons. Arbitrary System Prompt text is disabled.

If `SILLYTAVERN_CHARACTER_DIR`, `SILLYTAVERN_WORLD_DIR`, or model/cache paths are changed outside the documented defaults, update the systemd unit's `ReadWritePaths=` entries to match; `ProtectSystem=strict` otherwise blocks writes.


Character panel:

```text
/character                    Open the panel
Select                        Choose a character, then choose the target session
Info                          Show card metadata sizes
Delete                        Choose a non-active card, then confirm deletion
Upload                        Show the safe Telegram Document upload instruction
```

Legacy `/character info`, `/character versions`, `/character restore`, and `/character delete` text forms are removed. Use the panel for all character management actions.

Send a metadata-bearing SillyTavern PNG as a Telegram **Document**, not as a compressed Telegram Photo. The bridge verifies the `chara` PNG text chunk before installing it, writes a private backup, and verifies the backup checksum before reporting success.

Plain avatars are not imported as character cards.

## 📚 Data Bank RAG

Upload a supported document directly to the bot. `.jsonl` files remain chat-import files; other supported documents are indexed in the per-chat Data Bank.

The index uses:

1. SQLite FTS5 lexical search.
2. Optional OpenAI-compatible embeddings.
3. Hybrid ranking.
4. Lexical fallback when the embedding endpoint is unavailable.

Files are limited to 10 MB. JSONL imports reject messages over 12,000 characters or transcripts over 200,000 characters. DOCX XML members, PDF page counts, and extracted text are bounded. Image bytes are not inserted into the text Data Bank. When RAG contributes a source, generated replies include a `Sources:` footer with the indexed filenames. Embeddings are isolated by endpoint/model/dimensions and cached query vectors expire with runtime cache retention. The Data Bank panel shows current embedding coverage and provides `Reindex embeddings`; semantic retrieval currently scans at most 5,000 vectors in Python, while FTS5 remains the complete lexical fallback. Set a dedicated `SILLYTAVERN_RAG_EMBEDDING_API_KEY` for external embedding endpoints; local loopback embedding endpoints may run without a key.
Set `SILLYTAVERN_RAG_EMBEDDING_REVISION` to a new value when the same endpoint/model/dimensions changes implementation; this creates a new namespace and requires reindexing.

PDF extraction runs in an isolated `bridge/pdf_parser.py` subprocess with bounded input, page count, extracted text, CPU time, and memory. The parent worker applies a 45-second timeout and reports parser failures without falling back to in-process PDF parsing.

JSONL import metadata is trusted legacy input: its System Prompt field is preserved for compatibility and is not a new panel choice. New interactive System Prompt selection remains TXT-panel-only.

Hindsight retention and automatic TTS are best-effort auxiliary jobs; a saturated utility queue may defer or drop them without affecting the durable chat turn.

## 🔐 Security and privacy

> **Credentials must remain environment-only and must not be printed, committed, or packaged.**

- Keep `.env`, SQLite databases, exported chats, logs, character cards, and persona files outside the public repository.
- Restrict the bot with `SILLYTAVERN_TELEGRAM_ALLOWED_USERS`.
- Do not expose a tool-capable API publicly without authentication and TLS.
- Treat uploaded documents as private chat data.
- Review Hindsight bank scope before enabling cross-session recall.
- The release publisher is private maintainer tooling and is not included in the public runtime repository or distribution ZIP.
- The repository contains no credentials, live database, user allowlist, or character card.

## 🏗️ Architecture

The entrypoint is intentionally small:

```text
sillytavern_telegram_bridge.py  # launcher
bridge/runtime.py               # compatibility runtime loader
bridge/common.py                # configuration, workers, and runtime permissions
bridge/cards.py                 # character cards, World Info, prompts, panels
bridge/schema.py                # SQLite schema and migrations
bridge/database.py              # SQLite sessions and generation settings
bridge/memory.py                # Hindsight memory and summaries
bridge/rag.py                   # Data Bank, FTS5, and embeddings
bridge/pdf_parser.py           # isolated, resource-limited PDF extraction worker
bridge/groups.py                # multi-character orchestration
bridge/telegram.py              # Telegram transport and imports
bridge/help.py                  # help categories and command registration
bridge/help_details.py          # Help rendering, callbacks, and JSON loader
bridge/help_details.json        # editable detailed Help descriptions
bridge/sync.py                  # opt-in file sync and conflict detection
bridge/catalog.py               # provider/model catalog
bridge/media.py                 # images, voice, STT, and TTS
bridge/generation.py            # prompts, adapters, variants, branches
bridge/commands.py              # message and document commands
bridge/input_flows.py           # scoped pending text-input transitions
bridge/command_routes.py        # normalized command routing
bridge/panel_callback_routes.py # panel callback routing
bridge/callbacks.py             # inline-button callbacks
bridge/main.py                  # polling loop and startup checks
```

Each domain file is kept below 500 lines. Help descriptions are public, editable
JSON data; private runtime state remains outside the repository.

## 🧱 Architecture boundary

This bridge reimplements selected SillyTavern concepts directly. It does not execute every SillyTavern extension or STscript hook. Prompt parity includes character metadata, personas, World Info recursion, macros, summaries, memory, RAG, group context, and generation settings; native extension execution remains outside the bridge.

## 📄 License

MIT. See `LICENSE`.

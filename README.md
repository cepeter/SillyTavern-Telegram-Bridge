# SillyTavern Telegram Bridge

A lightweight Telegram bridge for SillyTavern-style character chat.

The bridge keeps chat state in SQLite, reads character cards from SillyTavern, and routes generation through an OpenAI-compatible provider. It is designed to run beside SillyTavern without patching the SillyTavern repository.

## Features

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
- Multi-character groups with round-robin, contextual, manual, and bounded autonomous modes.
- Telegram Forum Topics with topic-scoped sessions, history, queues, group state, media, and replies.
- Inline help menu with command categories.
- Bounded background workers for STT, TTS, document indexing, and memory retention.
- Durable SQLite jobs for normal generation, long-running commands, voice transcription, image analysis, document imports, callbacks, and native edits, with per-chat ordering and session-aware execution.
- UTF-16-safe Telegram splitting, typed World Info editing, bounded processed-update retention, and isolated generation/utility worker pools.
- All dynamic inline panels paginate at 8 options per page with Previous/Next navigation.

## Requirements

- Python 3.11 (canonical locked runtime)
- A Telegram bot token
- An OpenAI-compatible chat-completions provider
- SillyTavern installed locally, or equivalent character/world directories
- Optional Hindsight API for long-term memory
- Optional OpenAI-compatible embedding endpoint for semantic RAG

## SillyTavern prerequisite

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
```

Dependency files:

- `requirements.txt` contains compatible version ranges for users who need resolver flexibility.
- `requirements.lock` contains exact versions and SHA-256 hashes for reproducible Python 3.11 installs.
- The lock file is intentionally long: one package version can have many hashes for Linux, macOS, Windows, x86_64, ARM, source archives, and binary wheels.
- Use `requirements.lock` for production/release installs; use `requirements.txt` only when intentionally resolving a different environment.

## Configuration

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

The selected provider's configured `api_key_env` must be present. `LLM_API_KEY` is only used for providers that intentionally use the generic fallback; provider-specific credentials are preferred and explicit provider keys fail closed.

The bridge also reads provider-specific credentials from the environment referenced by the provider catalog. Never commit credentials. External provider endpoints must use HTTPS; use `SILLYTAVERN_PROVIDER_ALLOWED_HOSTS` when you need an explicit hostname allowlist.

## Provider catalog

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

A provider is selectable for inference when its `adapter` or `transport` is one of `chat_completions`, `openai`, `openai_compatible`, or `anthropic_messages`. Providers with a missing or unsupported adapter remain visible as **catalog-only** and cannot generate replies. The `/model` and `/providers` panels show 8 provider/model options per page with Previous/Next navigation.

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

The `/model` and `/providers` panels read this bridge catalog:

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
SILLYTAVERN_HINDSIGHT_URL=http://127.0.0.1:8890
HINDSIGHT_API_KEY=replace-me
SILLYTAVERN_RAG_EMBEDDING_URL=http://127.0.0.1:8891/v1/embeddings
SILLYTAVERN_RAG_EMBEDDING_MODEL=text-embedding-3-small
SILLYTAVERN_RAG_EMBEDDING_DIMENSIONS=1536
SILLYTAVERN_STT_MODEL=base
SILLYTAVERN_TTS_VOICE=id-ID-GadisNeural
```

## Run

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

## Command guide

Open the interactive command guide:

```text
/help
```

Core commands:

```text
/start                         Send the character card first message only
/status                       Show active runtime state
/new                          Create a new isolated session
/reset                        Clear the active session
/session                      Switch sessions
/model                        Choose provider and model
/providers                    Open the synchronized provider catalog
/character                    Open character panel: select, info, delete, upload guidance
/persona                      Choose a user persona
/world                         Open the World Info/lorebook panel
/systemprompt                  Open the configured TXT System Prompt panel
/note <text>                 Set Author's Note
```

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
/preset                       Open preset use/delete panel
/preset save <name>           Save a preset name (validated text)
/macro {{char}}               Preview a supported macro
/stscript note <text>         Apply a safe session note command
/regen                        Generate a new response variant
/swipe                        Browse variants with buttons
/branch                       Open the active branch selector
/continue                     Continue the latest assistant response
/edit <text>                  Edit the latest user turn and regenerate
/retry                        Retry the latest failed response
/prompt                       Inspect prompt composition
/summarize                    Force a session summary
```

Memory and RAG:

```text
/memory                       Open memory mode/scope panel
/memory search <query>        Search Hindsight (free-text query)
/remember <fact>              Queue an explicit memory (free text)
/databank                     Open RAG/list/remove/reindex panel
/databank search <query>      Search Data Bank (free-text query)
```

Voice, media, and groups:

```text
/tts <text>                   Send one voice message (free text)
/voice                        Open automatic TTS panel
/voice_input                  Open transcription/model panel
/voice_input language <code>  Set language (validated text)
```

Group control panel:

```text
/group                       Open group control panel
→ Add character              Choose a character card
→ Remove character           Remove a group member
→ Choose speaker             Choose the next speaker
→ Mode                      Select round robin/contextual/manual/autonomous
→ Enable / Disable           Change group state
→ Next speaker               Advance manual mode
```

Telegram Forum Topics are isolated by `message_thread_id`; each topic keeps separate history, queue, group state, media routing, and replies.
## System Prompt choices

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
Select                        Change the active character
Info                          Show card metadata sizes
Delete                        Choose a non-active card, then confirm deletion
Upload                        Show the safe Telegram Document upload instruction
```

Legacy `/character info`, `/character versions`, `/character restore`, and `/character delete` text forms are removed. Use the panel for all character management actions.

Send a metadata-bearing SillyTavern PNG as a Telegram **Document**, not as a compressed Telegram Photo. The bridge verifies the `chara` PNG text chunk before installing it, writes a private backup, and verifies the backup checksum before reporting success.

Plain avatars are not imported as character cards.

## Data Bank RAG

Upload a supported document directly to the bot. `.jsonl` files remain chat-import files; other supported documents are indexed in the per-chat Data Bank.

The index uses:

1. SQLite FTS5 lexical search.
2. Optional OpenAI-compatible embeddings.
3. Hybrid ranking.
4. Lexical fallback when the embedding endpoint is unavailable.

Files are limited to 10 MB. JSONL imports reject messages over 12,000 characters or transcripts over 200,000 characters. DOCX XML members, PDF page counts, and extracted text are bounded. Image bytes are not inserted into the text Data Bank. When RAG contributes a source, generated replies include a `Sources:` footer with the indexed filenames. Embeddings are isolated by endpoint/model/dimensions and cached query vectors expire with runtime cache retention. The Data Bank panel shows current embedding coverage and provides `Reindex embeddings`; semantic retrieval currently scans at most 5,000 vectors in Python, while FTS5 remains the complete lexical fallback. Set a dedicated `SILLYTAVERN_RAG_EMBEDDING_API_KEY` for external embedding endpoints; local loopback embedding endpoints may run without a key.
Set `SILLYTAVERN_RAG_EMBEDDING_REVISION` to a new value when the same endpoint/model/dimensions changes implementation; this creates a new namespace and requires reindexing.

PDF extraction remains in-process; upload byte/page/text limits reduce exposure, but a separate parser subprocess is recommended for untrusted multi-tenant deployments.

JSONL import metadata is trusted legacy input: its System Prompt field is preserved for compatibility and is not a new panel choice. New interactive System Prompt selection remains TXT-panel-only.

Hindsight retention and automatic TTS are best-effort auxiliary jobs; a saturated utility queue may defer or drop them without affecting the durable chat turn.

## Security and privacy

- Keep `.env`, SQLite databases, exported chats, logs, character cards, and persona files outside the public repository.
- Restrict the bot with `SILLYTAVERN_TELEGRAM_ALLOWED_USERS`.
- Do not expose a tool-capable API publicly without authentication and TLS.
- Treat uploaded documents as private chat data.
- Review Hindsight bank scope before enabling cross-session recall.
- The release publisher is private maintainer tooling and is not included in the public runtime repository or distribution ZIP.
- The repository contains no credentials, live database, user allowlist, or character card.

## Architecture

The entrypoint is intentionally small:

```text
sillytavern_telegram_bridge.py  # launcher
bridge/runtime.py               # compatibility runtime loader
bridge/common.py                # configuration, cards, World Info, personas
bridge/database.py              # SQLite sessions and generation settings
bridge/memory.py                # Hindsight memory and summaries
bridge/rag.py                   # Data Bank, FTS5, and embeddings
bridge/groups.py                # multi-character orchestration
bridge/telegram.py              # Telegram transport and imports
bridge/help.py                  # help categories and command registration
bridge/catalog.py               # provider/model catalog
bridge/media.py                 # images, voice, STT, and TTS
bridge/generation.py            # prompts, adapters, variants, branches
bridge/commands.py              # message and document commands
bridge/callbacks.py             # inline-button callbacks
bridge/main.py                  # polling loop and startup checks
```

Each domain file is kept below 500 lines; runtime data remains outside the repository.

## Architecture boundary

This bridge reimplements selected SillyTavern concepts directly. It does not execute every SillyTavern extension or STscript hook. Prompt parity includes character metadata, personas, World Info recursion, macros, summaries, memory, RAG, group context, and generation settings; native extension execution remains outside the bridge.

## License

MIT. See `LICENSE`.

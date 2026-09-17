# SillyTavern Telegram Bridge

[![CI](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/cepeter/SillyTavern-Telegram-Bridge?display_name=tag)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> Current release: **v0.2.011**

A local Telegram sidecar for SillyTavern-style character chat. The bridge reads
native character/world data, stores private session state in SQLite, and routes
replies through a private provider catalog. It does not patch, launch, or
execute the SillyTavern source tree.

## Current features

- Native SillyTavern PNG character-card discovery, selection, upload guidance, metadata, and safe deletion.
- Native Persona names, descriptions, and avatars with active/reference protection.
- Isolated sessions with custom names, per-session model, generation settings, language, Persona, World Info, and summaries.
- Provider/model panel with paginated choices, health checks, model refresh, and adapter validation.
- OpenAI-compatible Chat Completions and optional Anthropic Messages adapters.
- Streaming preview separated from final delivery; complete replies are sent through the Telegram splitter.
- Recovery for output-token stops, including reasoning-only streaming stops, with bounded larger-budget retries.
- UTF-16-safe semantic Telegram splitting at paragraph, newline, sentence, and whitespace boundaries.
- `/regen`, `/swipe`, `/branch`, `/continue`, `/edit`, `/retry`, and prompt diagnostics.
- Quote-driven automatic Edge TTS and local Faster-Whisper voice input.
- Native expression-sprite discovery with manual, automatic, and off modes; sprites are sent only when the effective expression changes.
- Optional OpenAI-compatible image generation through `/imagine`.
- Hindsight memory with active-session-only recall and automatic summaries.
- Data Bank RAG for PDF, DOCX, TXT, Markdown, JSON, YAML, CSV, HTML, and XML, with FTS5 and optional embeddings.
- Loopback SillyTavern Live Sync with initial reconciliation and conflict-stop behavior.
- Forum Topic group chats with round-robin, contextual, manual, and bounded autonomous modes.
- Durable SQLite jobs, per-chat ordering, restart recovery, and retryable failed turns.
- Safe `/update`: no-op when already latest; otherwise clean-checkout, fast-forward-only update with confirmation.

## Requirements

- Python 3.11.
- A Telegram bot token and an allowlisted Telegram user ID.
- A local SillyTavern installation with at least one PNG character card.
- An OpenAI-compatible chat provider, or an explicitly configured Anthropic Messages provider.
- Optional: Hindsight, embedding, image, STT, and TTS services.

## Install

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
SILLYTAVERN_SYSTEM_PROMPTS_DIR=/path/to/private/system-prompts
```

Provider-specific credentials are named by each private provider catalog entry's
`api_key_env`. Never put real credentials in Git, README files, release assets,
or Telegram messages.

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
/status             Show active character, session, model, and runtime state
/new                Create and activate a named isolated session
/reset              Confirm an active-session reset
/session            Switch, create, or delete an inactive session
/character          Open character management
/persona            Open native Persona controls
/world              Open World Info/lorebook controls
/systemprompt       Choose a private TXT System Prompt
/note               Open Author's Note controls
```

### Replies and settings

```text
/settings           Configure generation values
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
/expression         Choose manual, automatic, or off expressions
```

### Voice, files, memory, and groups

```text
/voice              Toggle quote-driven automatic TTS
/voice_input        Configure local transcription
/imagine            Generate an image through an enabled image provider
/memory             Open Hindsight memory and search controls
/remember           Store one explicit long-term fact
/summarize          Regenerate the active-session summary
/databank           Open Data Bank RAG controls
/sync               Open Live API Sync controls
/group              Open Forum Topic group controls
/help               Open the interactive command guide
/update             Check and, after confirmation, update the bridge
```

`/tts` is disabled. Voice replies are automatic when `/voice` is enabled and
model dialogue is enclosed in straight double quotes. Narration and unquoted
text are not synthesized.

## Reply delivery

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

## Character expressions

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

## Live Sync

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

## Security and privacy

- Keep `.env`, provider YAML, SQLite, logs, cards, Personas, and private prompts outside Git.
- Restrict Telegram access with `SILLYTAVERN_TELEGRAM_ALLOWED_USERS`.
- Require HTTPS for external provider, image, and embedding endpoints; loopback is allowed for local services.
- Validate provider hosts before attaching credentials.
- Bound uploaded file size, DOCX expansion, PDF pages, extracted text, and embedding work.
- Keep Hindsight recall limited to the active session.
- Treat uploaded documents, model responses, and recalled text as private data.
- Use the systemd hardening template for production deployments.

## Run

```bash
python sillytavern_telegram_bridge.py --check
python sillytavern_telegram_bridge.py
```

A systemd template is available at
`systemd/sillytavern-telegram.service.example`. Set the private
`SILLYTAVERN_BRIDGE_SOURCE_DIR` when `/update` runs from the live launcher copy.

## Architecture

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

# 🌉 SillyTavern Telegram Bridge

[![CI](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/cepeter/SillyTavern-Telegram-Bridge?display_name=tag)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/releases/latest)
[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

> Talk to your SillyTavern characters from Telegram. That's the whole pitch.

---

Use your SillyTavern characters from Telegram without replacing or patching
SillyTavern. The bridge reads the same character cards, Personas, World Info and
System Prompts, keeps Telegram-side sessions and memory, and sends generation
requests to providers you configure.

SillyTavern remains the owner of its native data. The bridge is a user-scoped
remote interface: chat from your phone, use voice/images/documents, and optionally
sync conversation state back through the SillyTavern Live API.

---

## 📋 Contents

- [✨ What it does](#-what-it-does)
- [🔧 Requirements](#-requirements)
- [📦 Installation guide](#-installation-guide)
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
- [🚀 Downloads, updates, and database compatibility](#-downloads-updates-and-database-compatibility)
- [🧰 Troubleshooting](#-troubleshooting)
- [📄 License](#-license)
- [Contributing and security](#contributing-and-security)

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

## 📦 Installation guide

The recommended Linux setup keeps the bridge, its virtual environment, and its
runtime data under your user account. You do not need a system-wide Python
installation or a root-owned service.

### 1. Install the bridge source

**Recommended:** clone with Git. A clean `main` checkout is required for the
built-in signed `/update` flow.

```bash
cd ~
git clone https://github.com/cepeter/SillyTavern-Telegram-Bridge.git sillytavern-telegram-bridge
cd ~/sillytavern-telegram-bridge
```

For a manual/offline install, the latest GitHub release also includes an explicit
`SillyTavern-Telegram-Bridge-vX.Y.Z.zip` asset and matching `.sha256` checksum.
Release-ZIP installs are supported for manual updates, but `/update` expects a Git
checkout on `main`.

### 2. Create a private virtual environment

Install only the locked runtime dependencies required to run the bridge:

```bash
python3.11 -m venv .venv
./.venv/bin/python -m pip install -r requirements.lock
```

`requirements-dev.txt` is for contributors and CI; it is not required for a
normal bridge installation.

### 3. Create the user-scoped environment file

The launcher reads `~/.local/share/sillytavern-telegram/.env` by default:

```bash
mkdir -p ~/.local/share/sillytavern-telegram
cp .env.example ~/.local/share/sillytavern-telegram/.env
chmod 600 ~/.local/share/sillytavern-telegram/.env
```

Edit that file with your Telegram token, allowed user ID, SillyTavern path,
default character, provider credentials, and model. The
[Configuration](#️-configuration) and [Provider catalog](#-provider-catalog)
sections below describe the available settings.

### 4. Validate and run the bridge

Check the installation without starting Telegram polling:

```bash
cd ~/sillytavern-telegram-bridge
./.venv/bin/python sillytavern_telegram_bridge.py --check
```

When the check passes, run the bridge manually:

```bash
cd ~/sillytavern-telegram-bridge
./.venv/bin/python sillytavern_telegram_bridge.py
```

Press `Ctrl+C` to stop a manual run.

### 5. Run it persistently with user systemd

The repository includes a hardened **user-service** template. The default
template expects:

- bridge checkout: `~/sillytavern-telegram-bridge`
- virtual environment: `~/sillytavern-telegram-bridge/.venv`
- environment file: `~/.local/share/sillytavern-telegram/.env`
- bridge runtime data: `~/.local/share/sillytavern-telegram`
- update staging: `~/.local/share/sillytavern-telegram/live`
- SillyTavern user data: `~/.local/share/SillyTavern/data/default-user`

If your paths differ, edit the copied service file before enabling it. In
particular, keep `WorkingDirectory`, `ExecStart`,
`SILLYTAVERN_BRIDGE_SOURCE_DIR`, `SILLYTAVERN_LIVE_BRIDGE_DIR`, and
`ReadWritePaths` aligned with your actual bridge and SillyTavern locations.

Install and start the service for your user account:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/sillytavern-telegram.service.example \
  ~/.config/systemd/user/sillytavern-telegram.service

systemctl --user daemon-reload
systemctl --user enable --now sillytavern-telegram.service
systemctl --user status sillytavern-telegram.service
```

Useful service commands:

```bash
systemctl --user restart sillytavern-telegram.service
systemctl --user stop sillytavern-telegram.service
journalctl --user -u sillytavern-telegram.service -f
```

`systemctl --user enable` starts the bridge automatically when your user
systemd manager starts. If you also want it to start at boot without an
interactive login and remain running after logout, enable lingering for the
account once:

```bash
loginctl enable-linger "$USER"
```

Some distributions require an administrator to enable lingering for a user.

---

## ⚙️ Configuration

The bridge is configured primarily through a private environment file. The
recommended location is:

```text
~/.local/share/sillytavern-telegram/.env
```

Create it from the maintained example and keep it private:

```bash
mkdir -p ~/.local/share/sillytavern-telegram
cp .env.example ~/.local/share/sillytavern-telegram/.env
chmod 600 ~/.local/share/sillytavern-telegram/.env
```

On POSIX, startup rejects an environment file that is not owned by the current
user, is group/other-accessible, is a symlink/non-regular file, exceeds 1 MiB, or
contains invalid UTF-8/NUL data. The complete file is parsed before any values are
applied. On Windows, protect the file with a user-only ACL.

Existing process/systemd environment variables take precedence over values from
the file. Configuration is captured when the bridge starts, so restart the
service after changing `.env`.

> **Special case:** `SILLYTAVERN_ENV_FILE` selects the file *before* that file is
> read. Set it in the process or systemd environment when using a non-default
> path; putting it only inside the alternate file cannot select that same file.

### Minimum required configuration

The bridge validates these values before polling Telegram:

```dotenv
SILLYTAVERN_TELEGRAM_BOT_TOKEN=replace-me
SILLYTAVERN_TELEGRAM_ALLOWED_USERS=123456789
SILLYTAVERN_DEFAULT_CHARACTER=example-character.png
SILLYTAVERN_MODEL=provider-one::provider-one/model-a
```

`SILLYTAVERN_TELEGRAM_ALLOWED_USERS` must contain comma-separated **numeric**
Telegram user IDs. `SILLYTAVERN_DEFAULT_CHARACTER` must name an existing card in
the configured character directory. `SILLYTAVERN_MODEL` uses
`provider-id::model-id` from the private provider catalog. A bare model ID is accepted only when it exactly matches one provider; ambiguous or unknown IDs are refused. Qualified IDs must exist in the provider’s configured `models` or its opted-in discovery cache. Startup validates the route, endpoint policy, and credential before polling; seed the default model in YAML for a first install without a discovery cache.

`SILLYTAVERN_DIR` defaults to `~/.local/share/SillyTavern`; set it when your
SillyTavern installation lives elsewhere.

### Environment variable reference

#### Bot, model, and catalog

| Variable | Default | Purpose |
|---|---|---|
| `SILLYTAVERN_TELEGRAM_BOT_TOKEN` | required | Telegram BotFather token. Secret. |
| `SILLYTAVERN_TELEGRAM_ALLOWED_USERS` | required | Comma-separated numeric Telegram user IDs allowed to use the bot. |
| `SILLYTAVERN_DEFAULT_CHARACTER` | required | PNG character filename used for new/default sessions. |
| `SILLYTAVERN_MODEL` | required | Default Story route in `provider-id::model-id` form. |
| `SILLYTAVERN_DEFAULT_USER_NAME` | empty | Fallback display value for `{{user}}`. |
| `LLM_API_KEY` | empty | Generic provider-key fallback. Prefer a provider-specific `api_key_env`. |
| `SILLYTAVERN_PROVIDER_CONFIG` | `$SILLYTAVERN_BRIDGE_HOME/sillytavern_telegram_providers.yaml` | Private YAML provider catalog. |
| `SILLYTAVERN_MODEL_CACHE` | `$SILLYTAVERN_BRIDGE_HOME/model_catalog_cache.json` | Cache for discovered provider model IDs. |
| `SILLYTAVERN_MODEL_REFRESH_SECONDS` | `3600` | Model discovery cache lifetime; range `1..86400`. |
| `OPENCODE_CLIENT_VERSION` | `1.18.31` | Client-version header used by the OpenCode Muse transport. |

Provider-specific credential names are intentionally dynamic: whatever string you
put in a catalog entry's `api_key_env` must exist in the private environment, for
example `PROVIDER_ONE_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY`.

#### Paths and native SillyTavern data

| Variable | Default | Purpose |
|---|---|---|
| `SILLYTAVERN_ENV_FILE` | `~/.local/share/sillytavern-telegram/.env` | Environment-file selector; set outside the file when overriding. |
| `SILLYTAVERN_BRIDGE_HOME` | `~/.local/share/sillytavern-telegram` | Private bridge data root; database/log paths derive from it. |
| `SILLYTAVERN_BRIDGE_SOURCE_DIR` | current repository root | Git checkout used by signed `/update`. |
| `SILLYTAVERN_LIVE_BRIDGE_DIR` | `$SILLYTAVERN_BRIDGE_HOME/live` | Managed code mirror used by the updater. |
| `SILLYTAVERN_DIR` | `~/.local/share/SillyTavern` | SillyTavern installation/data root used to derive native paths. |
| `SILLYTAVERN_CHARACTER_DIR` | `$SILLYTAVERN_DIR/data/default-user/characters` | Native character cards. |
| `SILLYTAVERN_CHARACTER_BACKUP_DIR` | `$SILLYTAVERN_BRIDGE_HOME/backups/sillytavern/characters` | Character backup destination. |
| `SILLYTAVERN_WORLD_DIR` | `$SILLYTAVERN_DIR/data/default-user/worlds` | Native World Info/lorebooks. |
| `SILLYTAVERN_SYSTEM_PROMPTS_DIR` | `$SILLYTAVERN_DIR/data/default-user/sysprompt` | Native System Prompt directory. |
| `SILLYTAVERN_NATIVE_SETTINGS_FILE` | `$SILLYTAVERN_DIR/data/default-user/settings.json` | Persona names/descriptions and native defaults. |
| `SILLYTAVERN_NATIVE_AVATAR_DIR` | `$SILLYTAVERN_DIR/data/default-user/User Avatars` | Persona avatars. |
| `SILLYTAVERN_ENFORCE_PROMPT_PERMISSIONS` | `false` | Enable prompt-file permission enforcement where supported. |

Derived private paths that do **not** have separate environment variables:

```text
$SILLYTAVERN_BRIDGE_HOME/scripts/sillytavern_telegram.sqlite3
$SILLYTAVERN_BRIDGE_HOME/logs/sillytavern_telegram_bridge.log
$SILLYTAVERN_BRIDGE_HOME/backups/sillytavern/personas/
```

#### Provider and network policy

| Variable | Default | Purpose |
|---|---|---|
| `SILLYTAVERN_PROVIDER_ALLOWED_HOSTS` | empty | Exact external provider/image hostnames. Empty intentionally denies external destinations. |
| `SILLYTAVERN_PROVIDER_PRIVATE_HOSTS` | empty | Separate opt-in for approved LAN/tailnet provider hosts. |
| `SILLYTAVERN_RAG_ALLOWED_HOSTS` | empty | Exact external embedding hostnames. |
| `SILLYTAVERN_RAG_PRIVATE_HOSTS` | empty | Separate LAN/tailnet embedding-host opt-in. |
| `SILLYTAVERN_HINDSIGHT_ALLOWED_HOSTS` | empty | Exact external Hindsight hostnames. |
| `SILLYTAVERN_HINDSIGHT_PRIVATE_HOSTS` | empty | Separate LAN/tailnet Hindsight-host opt-in. |

Host entries are plain exact hostnames: no scheme, path, port, or wildcard. Local
loopback HTTP is allowed for local services. External destinations require HTTPS.
Private/LAN/tailnet destinations require both their normal allowlist and matching
`*_PRIVATE_HOSTS` opt-in.

#### Context planning and diagnostics

| Variable | Default | Valid range / behavior |
|---|---:|---|
| `SILLYTAVERN_CONTEXT_WINDOW_TOKENS` | `32768` | `4096..1000000`; total prompt context window. |
| `SILLYTAVERN_CONTEXT_OUTPUT_RESERVE_TOKENS` | `4096` | `512..131072`; tokens reserved for model output. |
| `SILLYTAVERN_CONTEXT_HISTORY_CANDIDATES` | `96` | `8..512`; recent transcript messages considered before compaction. |
| `SILLYTAVERN_PERF_LOG` | `false` | Boolean (`true/yes/on/1` or `false/no/off/0`); logs low-overhead timing spans. |

The prompt input budget is approximately context window minus output reserve.
When over budget, older history, Data Bank context, Hindsight recall and continuity
summary are reduced before fixed character/system instructions or the current
user turn.

#### Hindsight memory

| Variable | Default | Purpose |
|---|---|---|
| `HINDSIGHT_API_URL` | `http://127.0.0.1:8890` | Hindsight service URL. |
| `HINDSIGHT_API_KEY` | empty | Optional Hindsight credential. Secret. |
| `SILLYTAVERN_HINDSIGHT_ALLOWED_HOSTS` | empty | External Hindsight allowlist. |
| `SILLYTAVERN_HINDSIGHT_PRIVATE_HOSTS` | empty | Private/LAN Hindsight opt-in. |

Hindsight is optional. Session generation only recalls memory scoped to the active
session. Reset/session deletion refuses destructive local cleanup when required
Hindsight cleanup cannot be verified.

#### Data Bank semantic embeddings

| Variable | Default | Valid range / purpose |
|---|---|---|
| `SILLYTAVERN_RAG_EMBEDDING_URL` | `http://127.0.0.1:8891/v1/embeddings` | OpenAI-compatible embeddings endpoint. |
| `SILLYTAVERN_RAG_EMBEDDING_API_KEY` | empty | Dedicated key; required for external embedding endpoints. |
| `SILLYTAVERN_RAG_ALLOWED_HOSTS` | empty | External embedding hostname allowlist. |
| `SILLYTAVERN_RAG_PRIVATE_HOSTS` | empty | Private/LAN embedding-host opt-in. |
| `SILLYTAVERN_RAG_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model ID. |
| `SILLYTAVERN_RAG_EMBEDDING_DIMENSIONS` | `1536` | `1..65536`; vector size. |
| `SILLYTAVERN_RAG_EMBEDDING_REVISION` | `1` | User-controlled embedding revision; change when embeddings become incompatible. |
| `SILLYTAVERN_RAG_MAX_EXTRACTED_CHARS` | `1000000` | `1..10000000`; extraction cap per document. |
| `SILLYTAVERN_RAG_MAX_PDF_PAGES` | `200` | `1..10000`; PDF page cap. |
| `SILLYTAVERN_RAG_PDF_PARSE_TIMEOUT_SECONDS` | `45` | `1..300`; isolated PDF parser timeout. |
| `SILLYTAVERN_RAG_SEMANTIC_CANDIDATES` | `384` | `64..2048`; candidate chunks considered by semantic retrieval. |

Full-text Data Bank search works without embeddings. Reindex documents after
changing embedding model, dimensions, or revision.

#### Live Sync

| Variable | Default | Valid range / purpose |
|---|---|---|
| `SILLYTAVERN_SYNC_API_URL` | empty (off) | SillyTavern Live API base URL. |
| `SILLYTAVERN_SYNC_API_HANDLE` | empty | API account/handle when required. |
| `SILLYTAVERN_SYNC_API_PASSWORD` | empty | API password when required. Secret. |
| `SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS` | `10` | `2..30`; request timeout. |
| `SILLYTAVERN_SYNC_API_INTERVAL_SECONDS` | `2.0` | `1..30`; realtime polling interval. |

Live Sync is disabled until `SILLYTAVERN_SYNC_API_URL` is set. It uses the API,
not chat-file polling or JSONL transfer.

#### Voice

| Variable | Default | Purpose |
|---|---|---|
| `SILLYTAVERN_STT_MODEL` | `base` | Speech-to-text model name. |
| `SILLYTAVERN_TTS_BIN` | `$SILLYTAVERN_BRIDGE_HOME/venv/bin/edge-tts` | `edge-tts` executable path. Override when your executable lives elsewhere. |
| `SILLYTAVERN_TTS_VOICE` | empty | Edge TTS voice; required when TTS output is enabled. |

#### Signed self-update

| Variable | Default | Purpose |
|---|---|---|
| `SILLYTAVERN_UPDATE_ALLOWED_SIGNERS` | unset | External OpenSSH allowed-signers file containing trusted **public** release keys. Required for automatic installation. |
| `SILLYTAVERN_UPDATE_SERVICE` | `sillytavern-telegram.service` | User systemd unit restarted after a verified update. |
| `SILLYTAVERN_BRIDGE_SOURCE_DIR` | repository root | Clean `main` checkout that the updater fast-forwards. |
| `SILLYTAVERN_LIVE_BRIDGE_DIR` | `$SILLYTAVERN_BRIDGE_HOME/live` | Managed mirror replaced after verification/staging. |

The current maintainer release-signing key has fingerprint:

```text
SHA256:kFUr31xAkxOVpg9D6G5oKP3l+WY2anmAXWWgLFZ8Ecw
```

Its public allowed-signers record is:

```text
cepeter namespaces="git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAPEP4Ucw+6lvdP0VQD3Z71+8eKj2ePXlLXRW9gA/8d
```

Verify the fingerprint against a GitHub **Verified** release tag or another
independent maintainer channel before installing it as trust material. Do not use
a private key as an allowed-signers file and do not store the trust file inside
the source checkout or managed live mirror.

### Environment syntax and validation

The parser accepts `KEY=VALUE` and optional `export KEY=VALUE`. Matching single or
double quotes are removed. Existing process variables win over file values.
Integer/float/boolean validation reports the variable name without printing the
supplied secret value.

Use the maintained `.env.example` as the copyable configuration template. It
contains the same supported user-facing variables documented above.

---

## 🌐 Provider catalog

The bridge uses its own **private YAML provider catalog**; it does not import
SillyTavern provider credentials/settings. Start from the maintained example:

```bash
cp config/providers.example.yaml ~/.local/share/sillytavern-telegram/sillytavern_telegram_providers.yaml
chmod 600 ~/.local/share/sillytavern-telegram/sillytavern_telegram_providers.yaml
```

Point `SILLYTAVERN_PROVIDER_CONFIG` elsewhere if you prefer another private path.
A minimal OpenAI-compatible provider looks like:

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

Then place the referenced credential in your private environment file:

```dotenv
PROVIDER_ONE_API_KEY=replace-me
SILLYTAVERN_PROVIDER_ALLOWED_HOSTS=provider.example
```

If the catalog contains no available model IDs, the panel shows setup guidance
rather than inventing fallback providers or models. The Health and Refresh
buttons remain available. For a Chat Completions provider, set `api_endpoint`
(or its accepted `api` alias) explicitly; a missing endpoint produces a
configuration error before any network request or credential attachment.

### Provider catalog field reference

| Field | Default / values | Purpose |
|---|---|---|
| `name` | provider ID | Human-readable label shown in Telegram panels. |
| `api_endpoint` | none | Provider base URL. `api` is accepted as an alias. Remote providers must use HTTPS. |
| `api_key_env` | `LLM_API_KEY` | Environment-variable name that contains this provider's credential. |
| `transport` | `chat_completions` | `chat_completions`/`openai`/`openai_compatible`, `anthropic_messages`, or `opencode_muse`. |
| `adapter` | `transport` | Model-menu capability label; normally match the transport. |
| `models` | empty | Explicit model IDs for this provider. Required when discovery is disabled/unavailable. |
| `discover_models` | false | When true, refresh model IDs from `GET /models` and cache them. |
| `streaming` | false | Enable SSE streaming for compatible Chat Completions providers. `stream` is also accepted. |
| `health_check` | `GET /models` | Set `chat_completion` for providers without a useful `/models` endpoint. |
| `extra_headers` | `{}` | Additional HTTP headers merged into provider requests. Do not put secrets here if the YAML might be shared. |
| `anthropic_version` | `2023-06-01` | Anthropic `anthropic-version` header for `anthropic_messages`. |
| `image_enabled` | false | Opt this provider into `/imagine`. |
| `image_endpoint` | `<api_endpoint>/images/generations` | Explicit OpenAI-compatible Images endpoint override. |
| `image_models` | empty | Image model IDs; first item is the provider default for image selection. |

`config/providers.example.yaml` contains normal Chat Completions, Anthropic,
OpenCode Muse and image-provider examples. Only fields consumed by the current
runtime are shown there.

### Model discovery and health checks

`discover_models: true` enables `GET /models` discovery. Results are stored in
`SILLYTAVERN_MODEL_CACHE` and refreshed according to
`SILLYTAVERN_MODEL_REFRESH_SECONDS`. If a provider has no usable `/models`
endpoint, keep explicit `models`, set `discover_models: false`, and optionally set
`health_check: chat_completion`.

The provider panel is the normal user interface:

Open `/providers`, choose **Story** or **Utility**, then use **Provider health**
or **Refresh models** in the provider list. These maintenance actions are
panel-only; typing a provider subcommand returns guidance instead of running it.

### Outbound host policy

The YAML catalog describes **where** to call; it does not grant network trust.
External endpoints must also be present in the corresponding environment
allowlist. An empty external-host list is fail-closed.

```dotenv
SILLYTAVERN_PROVIDER_ALLOWED_HOSTS=provider.example,images.example
SILLYTAVERN_RAG_ALLOWED_HOSTS=embedding.example
SILLYTAVERN_HINDSIGHT_ALLOWED_HOSTS=memory.example
```

Entries are exact hostnames without schemes, paths, ports, or wildcards.
Loopback addresses/`localhost` remain available to local HTTP services. LAN and
tailnet destinations also require the appropriate `*_PRIVATE_HOSTS` entry.
Metadata/link-local, unspecified, multicast, and reserved addresses are refused.

The built-in provider/image/embedding HTTP transport validates DNS addresses,
pins an approved numeric address for the connection, retains the original host
for TLS verification, rejects cross-origin redirects, and does not inherit
OS/environment proxy settings. Hindsight receives the same endpoint-policy
validation before its SDK client is created, but the SDK owns its own transport.

---

## 🤖 Using the bot

The bot is built around panels. Send a command, get a menu, then tap buttons or
send the next message to complete the action. It keeps things predictable and
prevents accidental changes.

### 📋 Everyday commands

| Command | What it does |
|---|---|
| `/start` | Show the greeting or setup guidance |
| `/help` | Open the interactive command guide |
| `/status` | Show formatted read-only session status |
| `/new` | Create and activate a named isolated session |
| `/reset` | Confirm an active-session reset and memory purge |
| `/session` | Switch, create, or delete inactive sessions |
| `/cancel` | Cancel the current scoped text-input step |
| `/character` | Manage native character cards |
| `/persona` | Choose, create, edit, or disable a native Persona |
| `/world` | Choose or disable World Info/lorebooks |
| `/systemprompt` | Choose a native JSON/TXT SillyTavern System Prompt |
| `/note` | Configure the session Author's Note |
| `/providers` | Choose Story or Utility provider/model |
| `/update` | Check for a release update and confirm before applying it |

### 🔄 Replies and generation

| Command | What it does |
|---|---|
| `/settings` | Open reasoning and generation controls |
| `/stream` | Open streaming preview controls |
| `/preset` | Apply, save, or delete generation presets |
| `/prompt` | Open the read-only prompt inspector |
| `/regen` | Generate another response variant |
| `/swipe` | Browse stored response variants |
| `/branch` | Choose the active response branch |
| `/continue` | Continue the latest assistant response |
| `/edit` | Edit the latest user turn and regenerate |
| `/retry` | Retry the latest failed response |
| `/language` | Choose the model reply language |
| `/expression` | Choose native expression behavior |
| `/macro` | Preview supported SillyTavern macros |
| `/stscript` | Open allowlisted STscript actions |

### 🎙️ Voice, files, memory, and groups

| Command | What it does |
|---|---|
| `/voice` | Open automatic quote-driven TTS controls |
| `/voice_input` | Configure transcription, STT model, and language |
| `/imagine` | Generate an image through an enabled image provider |
| `/memory` | Open active-session Hindsight memory controls |
| `/remember` | Store one explicit long-term fact |
| `/summarize` | Confirm active-session summary regeneration |
| `/databank` | Open Data Bank RAG controls |
| `/sync` | Open Live API Sync controls |
| `/group` | Open Forum Topic group controls |
| `/scene` | Open structured scene-state controls |

The README intentionally keeps this list to top-level commands. `/help` is the
canonical command reference for direct typed forms and subcommands. For example,
`/help databank search`, `/help group mode`, `/help group goal`, and
`/help scene refresh` open the matching detailed entry.

> `/tts` is not a command. Automatic voice is controlled from `/voice`.


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
  when automatic voice is enabled from `/voice`. Works for both your messages
  and character replies.
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

Dynamic panel choices use random, chat-scoped handles stored in SQLite. They
expire after 15 minutes, survive service restarts while valid, and are rejected
when their chat or panel ownership does not match. A failed database write does
not issue a memory-only handle. Expired handles are pruned when new handles are
created; token resolution itself never commits or mutates a request transaction.

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

Your original user text is stored as entered. Single-star action formatting
is added only to its prompt representation. Assistant replies are stored after
any selected response-language rendering and optional Humanizer pass.

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

### Optional Humanizer response style

Open `/settings` and choose **Humanizer: On** to request an additional prose
rewrite after response-language rendering. It is **off by default**, scoped to
the selected session, and reset by **Reset all** in the generation settings
panel. It uses the response's selected provider/model, so enabling it can add
latency and token charges. Native transcript sync preserves the setting.

With Humanizer enabled, normal replies do not expose an intermediate raw
streaming preview. The rewrite receives at most 24,000 source characters, has
an output-token request capped at 4,096, and uses a 30-second **per-request**
provider timeout. Existing bounded provider recovery/continuation may involve
additional requests; this is not a 30-second whole-turn deadline. Longer source
texts bypass the rewrite rather than sending a truncated source.

Provider failure, an empty rewrite, excessive shortening, or changes to protected
code, numbers, quoted dialogue, action spans, links, or citations keep the
original rendered reply. These conservative structural checks are not a proof
of semantic equivalence: review important prose as with any model-generated text.
The setting applies to normal replies, regeneration, continuation, edited-message
regeneration, and image replies through their shared rendering paths.

The [reference-refresh document](docs/humanizer-weekly-sync.md) is a future
implementation specification only. No weekly sync, timer, or automatic prompt
promotion is installed. Prompt attribution is retained in
[third-party notices](THIRD_PARTY_NOTICES.md).

### Telegram-safe model output

Completed model replies are normalized for Telegram before they are stored and
delivered. Presentation HTML such as `<div>`, `<span>`, headings, lists and
`<br>` is converted to readable plain text; HTML entities are decoded. Fenced
and inline code are protected so literal HTML examples remain copyable. This is
a final-output compatibility step rather than Telegram `parse_mode=HTML`, whose
limited tag set cannot safely render arbitrary model-generated web markup.

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
guidance, and delete cards through a protected flow. Character Info displays the
selected card PNG directly in Telegram with its metadata summary and panel
controls; if Telegram cannot render the PNG, the bridge falls back to the text-only
info view. Uploaded cards are validated as real SillyTavern PNGs. Backups are made
before any replacement or deletion.

The active card and any cards referenced by sessions or groups are protected —
you can't accidentally delete a card that's in use.

#### Re-uploading and optimizing a card

A first upload installs a validated card with a verified backup. Re-uploading
an existing name opens **Overwrite / New version / Keep existing** instead of
silently replacing it. The pending file stays outside the visible character
catalog. Confirmations belong to the initiating user and session, expire after
10 minutes, and are single-use. A newer proposal supersedes that user's prior
proposal of the same type. Changes to the installed card after preview require
a new preview rather than overwriting the changed file.

The **Optimizer** entry in `/character` first opens **Auto Optimize** and
**Manual Suggestion**. Auto uses the configured utility-model route directly.
Manual Suggestion waits for one user instruction (up to 2,000 characters), such
as “make her more sarcastic, preserve the backstory, and shorten the first
message”, and sends that guidance to the same utility model without letting it
override the optimizer field whitelist or character-identity rules. Suggestions
are bound to the initiating user, session, character file and original file
digest, expire after 10 minutes, and are not written into the card itself. The
preview also offers Manual Suggestion again for another draft; each refinement
starts from the currently installed/original card rather than chaining edits on
top of the previous model draft.

The Optimizer is a model-assisted editing tool, not a character-quality
guarantee. Review all pages of the proposed fields before applying; the preview
includes system prompt and post-history instructions when changed. Application
verifies that the exact approved fields produce the staged card bytes, preserves
name/avatar/other metadata, backs up the original bytes, and atomically replaces
the file. Unsupported dual `chara`/`ccv3` payloads are refused rather than
partially rewritten. A failed or interrupted application may require generating
a new preview; it never replays an already consumed confirmation automatically.

New installations and applied card changes also request an optional S–D quality
tier from the utility-model route. Badges are model-generated assessments, not
objective scores. Unavailable ranking leaves the card usable without a new badge.
Stored badges are invalidated when the card's file revision changes. Ranking
and optimization do not enter the roleplay transcript, but they send card text
to the selected utility provider and can incur token charges. Utility tasks use
bounded inputs and per-request timeouts.

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

Open `/voice` and enable automatic voice; the bridge will speak dialogue wrapped
in straight double quotes — from both your messages and character replies:

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

In manual mode, native Telegram message edits obey the same user-turn rule as
new text. The rule is checked before enqueue and again before regeneration,
including recovered jobs. An edit targets the session containing its original
message, not whichever session is currently active.

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
  reverse imports from the isolated service/port layer. Ruff linting, security
  rules, and formatting cover the complete Python tree. Mypy currently checks
  24 explicitly listed source files, including the network and callback-token policy.
- **🛡️ Use the systemd hardening template** for production deployments.

---

## 🚀 Downloads, updates, and database compatibility

### Release downloads

The latest GitHub release includes a real downloadable archive:

```text
SillyTavern-Telegram-Bridge-vX.Y.Z.zip
SillyTavern-Telegram-Bridge-vX.Y.Z.zip.sha256
```

The ZIP is produced directly from the signed release tag and contains only
tracked repository content. Verify the checksum before manual installation.
GitHub's automatically generated source archives may also appear, but the named
ZIP above is the maintained release asset.

This repository intentionally keeps **only the latest GitHub release and tag**.
Release history remains available in `CHANGELOG.md` and Git history.

### Automatic signed `/update`

Automatic installation is fail-closed. `/update` requires:

1. a clean Git checkout on branch `main`;
2. an SSH-signed annotated release tag;
3. a trusted public key in an allowed-signers file outside the source/live trees;
4. user-owned source/live parent directories that are not group/other-writable;
5. an empty managed live directory or one containing the bridge's
   `.bridge-deployment.json` marker;
6. Git, `ssh-keygen`, `systemctl`, `systemd-run`, and the configured user systemd service.

Create the trust directory/file on Linux:

```bash
mkdir -p ~/.config/sillytavern-telegram
chmod 700 ~/.config/sillytavern-telegram
cat > ~/.config/sillytavern-telegram/trusted-maintainers <<'EOF'
cepeter namespaces="git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAPEP4Ucw+6lvdP0VQD3Z71+8eKj2ePXlLXRW9gA/8d
EOF
chmod 600 ~/.config/sillytavern-telegram/trusted-maintainers
```

Verify that key's fingerprint independently before trusting it:

```text
SHA256:kFUr31xAkxOVpg9D6G5oKP3l+WY2anmAXWWgLFZ8Ecw
```

Then configure:

```dotenv
SILLYTAVERN_UPDATE_ALLOWED_SIGNERS=/home/you/.config/sillytavern-telegram/trusted-maintainers
SILLYTAVERN_UPDATE_SERVICE=sillytavern-telegram.service
```

and restart the bridge once so it loads the setting.

A successful update verifies the exact signed tag, checks ancestry and archive
safety, compiles the staged Python tree, fast-forwards the unchanged source
checkout, replaces the managed live mirror, retains the previous mirror for
recovery, and requests a nonblocking user-service restart.

If `requirements.lock` changed, automatic installation refuses the update; use a
manual reviewed install so dependency changes are explicit.

#### First update from a pre-hardening installation

If your old `live` directory is a nonempty code copy without
`.bridge-deployment.json`, `/update` returns `unmanaged_target`. Stop the service,
archive **only that old live code mirror**, create an empty private live directory,
and leave the `.env` and SQLite database in place:

```bash
systemctl --user stop sillytavern-telegram.service
mv ~/.local/share/sillytavern-telegram/live    ~/.local/share/sillytavern-telegram/prehardening-live-backup
mkdir ~/.local/share/sillytavern-telegram/live
chmod 700 ~/.local/share/sillytavern-telegram/live
chmod go-w ~/sillytavern-telegram-bridge ~/.local/share/sillytavern-telegram
systemctl --user start sillytavern-telegram.service
```

Do not move/delete:

```text
~/.local/share/sillytavern-telegram/.env
~/.local/share/sillytavern-telegram/scripts/sillytavern_telegram.sqlite3
```

### Manual update

For a Git installation:

```bash
systemctl --user stop sillytavern-telegram.service
cd ~/sillytavern-telegram-bridge
git fetch origin --tags --prune
git switch main
git pull --ff-only origin main
./.venv/bin/python -m pip install --require-hashes -r requirements.lock
./.venv/bin/python sillytavern_telegram_bridge.py --check
systemctl --user start sillytavern-telegram.service
```

For a release ZIP installation, download the latest ZIP and `.sha256`, verify the
checksum, extract to a fresh directory, install the locked dependencies, run
`--check`, then point your service at the new directory. Do not overlay a new ZIP
onto an old source tree.

### Pre-production database compatibility

This project is still preproduction. SQLite starts from the current
`initial_schema`; older development databases are not guaranteed upgrade paths.
Before using a revision that explicitly requires a fresh database, stop the
bridge and archive the existing SQLite file if you need it for inspection. The
bridge never silently deletes or converts an unsupported old database.

## 🧰 Troubleshooting

Start with the built-in check:

```bash
cd ~/sillytavern-telegram-bridge
./.venv/bin/python sillytavern_telegram_bridge.py --check
```

For a systemd installation:

```bash
systemctl --user status sillytavern-telegram.service
journalctl --user -u sillytavern-telegram.service -n 100 --no-pager
```

Common configuration/update failures:

| Symptom / updater code | What to check |
|---|---|
| Provider is refused before a request | Add the exact external host to `SILLYTAVERN_PROVIDER_ALLOWED_HOSTS`; private/LAN hosts also need `SILLYTAVERN_PROVIDER_PRIVATE_HOSTS`. |
| RAG/Hindsight external endpoint refused | Configure the matching `*_ALLOWED_HOSTS` and, for private networks, `*_PRIVATE_HOSTS`. |
| `.env` permission error | On POSIX, ensure the file is owned by the bridge user and `chmod 600`. |
| Changed `.env` appears ignored | Restart the service; settings are captured at application startup. |
| `SILLYTAVERN_ENV_FILE` appears ignored | Set it in systemd/process environment, not only inside the alternate file. |
| `/update` → `trust` or `signature` | Check `SILLYTAVERN_UPDATE_ALLOWED_SIGNERS`, file ownership/mode, and that the GitHub tag displays **Verified**. |
| `/update` → `target` | Source/live paths must be real, non-overlapping, user-owned, and not group/other-writable. |
| `/update` → `unmanaged_target` | Archive the old pre-hardening live code mirror and create an empty managed `live` directory. |
| `/update` → `dirty` / `branch` | Restore a clean source checkout and switch to `main`. |
| `/update` → `dependencies` | `requirements.lock` changed; perform a manual locked-dependency update. |
| TTS says voice is missing | Set `SILLYTAVERN_TTS_VOICE` and ensure `SILLYTAVERN_TTS_BIN` points to a working `edge-tts`. |

Never paste real bot/provider passwords or private signing keys into issues,
README files, release assets, or Telegram messages.

---

## 📄 License

GNU General Public License v3.0. See [LICENSE](LICENSE).


## Contributing and security

README scope is installation, configuration and normal user operation. Developer
architecture, branch workflow, test commands and dependency-lock maintenance live
in [CONTRIBUTING.md](CONTRIBUTING.md). Security reporting and deployment trust
boundaries live in [SECURITY.md](SECURITY.md).

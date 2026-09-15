# Changelog

All notable changes to **SillyTavern Telegram Bridge** are documented here.

## [0.1.82] - 2026-09-15

### Security and reliability

- Completed phase-aware replay handling for durable generation, edit, continuation, export, start, TTS, import, and group operations.
- Added atomic group response/variant/turn commits for text and image generation.
- Added durable callback token recovery and global scheduler/backlog bounds.
- Hardened character deletion references, prompt field limits, backup naming, and custom prompt permissions.
- Added embedding revision namespaces and untrusted Hindsight/RAG content boundaries.
- Expanded private crash/recovery regression coverage and documented canonical Python 3.11 lock policy.

## [0.1.81] - 2026-09-15

### Security and reliability

- Fixed PNG character-card Document routing before generic image analysis.
- Fixed Forum Topic panel binding scope and rejected expired/unbound session callbacks.
- Added durable callback tokens with SQLite persistence and expiry cleanup.
- Added operation identity to regeneration, continuation, edit, export, start, TTS, import, and group-turn paths.
- Hardened character deletion against default, session, and group references with verified pre-delete backups.
- Added group-turn recovery after committed assistant responses.
- Made `/continue` persistence and Telegram output consistent.
- Added global scheduler caps, bounded backlog batches, embedding revisions, character prompt limits, panel label limits, and phased JSONL import recovery.
- Moved recalled Hindsight content into an untrusted user-content boundary and documented PDF/process and Python lock policies.

## [0.1.80] - 2026-09-15

### Changed

- Combined model refresh into the provider panel.
- Added Provider health and Refresh models panel actions.
- `/providers health`, `/providers refresh`, and `/model refresh` now open the provider panel instead of running text-only actions.
- Updated README and `/help` provider workflow documentation.

## [0.1.79] - 2026-09-15

### Changed

- Removed legacy character text fallbacks for info, versions, restore, and delete.
- Character management commands are now panel-only; invalid legacy forms return a panel instruction instead of entering generation.

## [0.1.78] - 2026-09-15

### Added

- Expanded `/character` into a panel with character selection, metadata info, upload guidance, and safe two-step deletion of non-active cards.
- Kept legacy text commands for compatibility; panel actions use short callback tokens and preserve verified backups.
- Updated README and `/help` for the character panel workflow.

## [0.1.77] - 2026-09-15

### Security and reliability

- Added operation markers and deterministic replay handling for durable group/session/import/TTS side effects.
- Bound panel callbacks to the originating session and added expiring dynamic callback tokens.
- Made Forum Topic voice/document multipart delivery include the real chat ID and message thread ID.
- Moved PNG character-card detection into the durable document worker.
- Bounded per-chat scheduler memory and added SQLite backlog wake-up dispatch.
- Added JSONL message/transcript limits, TXT prompt permission enforcement, backup retention, export filename limits, and settings-input expiry.
- Added RAG embedding coverage status, untrusted-reference prompt boundaries, `defusedxml`, and a hash-pinned dependency lock.
- Updated documentation for TXT System Prompts, custom systemd paths, RAG allowlists, legacy JSONL prompt import, and best-effort auxiliary jobs.

## [0.1.76] - 2026-09-15

### Changed

- Removed the private runtime `systemprompt/` directory from the public repository.
- Added an ignore rule so private TXT System Prompt files cannot be committed accidentally.

## [0.1.75] - 2026-09-15

### Changed

- Converted public and private System Prompt files from JSON arrays to multiline TXT files.
- System Prompt panel choices now use `balanced.txt`, `concise.txt`, and `natural.txt`.
- Updated README and `/help` to document TXT-only examples.

## [0.1.74] - 2026-09-15

### Added

- Added multiline `.txt` System Prompt files.
- Each `.txt` file is one panel choice, labeled from its filename, and loaded without JSON newline escaping.
- Updated README and `/help` to document JSON and TXT System Prompt files.

## [0.1.73] - 2026-09-15

### Changed

- Removed development-only Bandit baseline, GitHub workflow, Ruff config, development requirements, and test suite from the public repository.
- Kept `requirements.txt` and `requirements.lock` for user installation and reproducibility.

## [0.1.72] - 2026-09-15

### Changed

- Changed each System Prompt JSON file to a JSON array of prompt lines.
- Loader joins array items with real newlines before panel selection/generation.
- Updated README examples and regression coverage for array loading.

## [0.1.71] - 2026-09-15

### Changed

- Changed System Prompt JSON files to contain only a raw JSON string.
- Panel labels now derive from each filename (`natural.json` → `Natural`).
- Added regression coverage for raw-string prompt loading.

## [0.1.70] - 2026-09-15

### Changed

- Removed the legacy `config/system_prompts.example.json` file.
- Kept only `config/system_prompts.example/` with one JSON file per System Prompt choice.

## [0.1.69] - 2026-09-15

### Changed

- Changed System Prompt catalogs to one JSON file per panel choice.
- Added support for single-prompt JSON objects with `name` and `prompt` fields.
- Split the private catalog into `balanced.json`, `concise.json`, and `natural.json` with a backup of the original multi-entry file.
- Added public directory examples under `config/system_prompts.example/`.

## [0.1.68] - 2026-09-15

### Changed

- Published the existing `natural.json` System Prompt catalog unchanged as the public `config/system_prompts.example.json`, per user confirmation that it contains no secrets and is publicly available.

## [0.1.67] - 2026-09-15

### Changed

- Added a sanitized generic `natural` System Prompt entry to `config/system_prompts.example.json`.
- Kept the live 27 KB `natural.json` prompt catalog private and outside Git.

## [0.1.66] - 2026-09-15

### Added

- Added two-step custom input for `/settings` fields, including reasoning budget.
- Added panel prompts, `/cancel`, and Python range/type validation for custom values.
- Added regression coverage proving custom settings input is consumed by settings instead of generation.

## [0.1.65] - 2026-09-15

### Fixed

- Normalized all common Telegram group command mention forms: `/command@bot`, `@bot /command`, and `/command @bot`.
- Prevented mentioned panel commands from falling through to normal generation.
- Added regression coverage for all three formats.

## [0.1.64] - 2026-09-15

### Changed

- Clarified `/settings` panel text with the active reasoning label and exact budget.
- Synchronized reasoning buttons with the canonical Python `REASONING_LEVELS` validator.
- Updated `/help` and README wording for session generation settings.

## [0.1.63] - 2026-09-15

### Fixed

- Normalized Telegram group command suffixes such as `/settings@botname` before routing.
- Ensured suffixed commands open the same panels as their private-chat forms instead of triggering normal generation.
- Added regression coverage for bot-addressed commands.

## [0.1.62] - 2026-09-15

### Fixed

- Added job/session replay hardening for deterministic `/new` operations and already-delivered replies.
- Added strict provider/catalog/RAG redirect validation.
- Added RAG reindex/backfill and source-aware citation truncation.
- Added SQLite busy timeout, job/failed-turn retention, STT model initialization locking, log rotation, response-variant turn IDs, stale-summary edit protection, and character filename caps.
- Fixed the default systemd SillyTavern writable path and clarified enum panel boundaries.

### Added

- Added a generation settings reasoning panel and Data Bank reindex action.

## [0.1.61] - 2026-09-15

### Changed

- Updated `/help`, Telegram command registration, and README for enum panels.
- Documented free-form text boundaries and Python validation for non-enum values.
- Documented pagination for preset and Data Bank option panels.

## [0.1.60] - 2026-09-15

### Added

- Added enum panels for streaming, TTS, STT mode/model, Hindsight memory mode/scope, presets, and Data Bank actions.
- Kept free-form values such as queries, facts, notes, preset names, language codes, and numeric generation settings under Python validation.
- Updated README and `/help` to distinguish panel enums from free-form text commands.

## [0.1.59] - 2026-09-15

### Added

- Added the `/group` control panel with Add/Remove character, Choose speaker, Mode, Enable/Disable, and Next speaker actions.
- Added panel-based character selection for group membership with 8 items per page.
- Updated group help and README documentation.

## [0.1.58] - 2026-09-15

### Changed

- Made System Prompt selection fully panel-only.
- Removed `/systemprompt list`, `/systemprompt use`, and arbitrary text fallback routing.
- Updated README, `/help`, and Telegram command registration accordingly.

## [0.1.57] - 2026-09-15

### Added

- Added Telegram Forum Topic scoping using `message_thread_id`.
- Isolated sessions, history, queues, group state, media, callbacks, and replies per topic.
- Added topic-aware Telegram message delivery and regression coverage.


## [0.1.56] - 2026-09-15

### Fixed

- Enforced exactly one submitted/in-flight job per chat for strict FIFO execution.
- Woke durable chat queues whenever any background capacity is released.
- Removed delayed duplicate callback acknowledgments from queued callback workers.
- Prevented recovery from re-sending assistant replies with committed Telegram delivery IDs.
- Added regression coverage for three-job FIFO interleavings and replay delivery.

## [0.1.55] - 2026-09-15

### Changed

- Clarified provider adapter behavior in README and `/help`.
- Documented that adapter-enabled providers can generate, while catalog-only entries are view-only.
- Documented 8-item provider/model panel pagination.

## [0.1.54] - 2026-09-15

### Added

- Added multi-world World Info panel selection with toggleable active lorebooks.
- Added multi-lorebook prompt merging, session status display, and list-compatible chat export/import.
- Added `Done` and `Clear all World Info` panel actions.

## [0.1.53] - 2026-09-15

### Added

- Added eight-item pagination to dynamic inline panels for characters, personas, sessions, World Info, System Prompts, providers, and models.
- Added Previous/Next navigation callbacks while preserving active-item markers.

## [0.1.52] - 2026-09-15

### Changed

- Made World Info/lorebook selection panel-only through the inline keyboard.
- Removed text-based lorebook selection and text entry-editor fallbacks.
- Updated `/help` and README command references to match the panel-only flow.

## [0.1.51] - 2026-09-15

### Added

- Routed state-mutating commands, callbacks, and native edits through durable per-chat FIFO jobs.
- Added continuous queued-job draining when worker capacity becomes available.
- Added failure-safe native edit regeneration.
- Added embedding namespaces, BM25 rank ordering, and single-retrieval context/citation bundles.
- Made CI install and audit the exact dependency lock; added gating Bandit delta checks.
- Aligned the hardened service template with character, World Info, and STT cache write paths.

### Fixed

- Removed the unconditional startup requirement for the generic `LLM_API_KEY`.

## [0.1.49] - 2026-09-15

### Added

- Added callback-token handling for long System Prompt choice keys.
- Added UTF-16-safe Telegram message splitting.
- Added typed World Info editing for integer and boolean fields.
- Added retention for processed Telegram update records.
- Split generation and utility/media worker pools.

## [0.1.48] - 2026-09-15

### Added

- Added fail-closed provider credential lookup when `api_key_env` is explicit.
- Added runtime permission enforcement for private bridge state.
- Added Hindsight HTTPS/loopback endpoint validation and hostname allowlisting.
- Added compatible systemd hardening with `UMask=0077`, private temp, filesystem protection, and restricted address families.
- Added behavioral tests, a locked dependency set, and CI test execution.

### Fixed

- Updated `pypdf` and locked dependencies after vulnerability scanning.


### Fixed

- Added durable SQLite jobs for normal generation and long-running commands.
- Added durable SQLite handoff and session-safe recovery for voice, image, and document jobs.
- Captured the active session ID before enqueue so queued work cannot move to a later session.
- Added strict per-chat FIFO dispatch while retaining parallel work across different chats.
- Requeued unfinished jobs after restart and made `/retry` redelivery-aware.


### Fixed

- Fixed `/systemprompt` failing with Telegram HTTP 400 after selecting a long JSON prompt.
- System Prompt menus now display the selected JSON entry name instead of embedding the full prompt text.


### Added

- Moved normal text and image generation into bounded background workers.
- Added durable failed-turn records and `/retry` for failed character responses.
- Added duplicate Telegram update protection with the `processed_updates` table.
- Finalized streamed responses through the full Telegram splitter after removing the preview.

## [0.1.44] - 2026-09-15

### Fixed

- Added durable Telegram update deduplication to prevent redelivered updates from generating duplicate replies.
- Removed a failed streaming placeholder before sending a fallback reply.
- Preserved existing response and session history behavior.

## [0.1.43] - 2026-09-15

### Fixed

- Made Help panel Close hide the message before attempting deletion.
- Kept the close action independent from callback acknowledgment failures.
- Restored category-button acknowledgment for the Help panel.

## [0.1.42] - 2026-09-14

### Fixed

- Fixed Help panel Close handling with a hide-message fallback when Telegram rejects `deleteMessage`.
- Updated README to document the guaranteed panel cleanup behavior.

## [0.1.41] - 2026-09-14

### Changed

- Made Help panel Close delete the Help message instead of only removing buttons.
- Removed `/systemprompt list` and `/systemprompt use` from Help documentation.
- Documented System Prompt as a JSON-choice panel with an Off button.

## [0.1.40] - 2026-09-14

### Changed

- Changed `/start` to send only the character card `first_mes`.
- Kept `/help` as the separate command for the help panel.
- Clarified that `balanced` and `concise` are JSON entries loaded from the configured prompt folder.

## [0.1.39] - 2026-09-14

### Changed

- Configured `SILLYTAVERN_SYSTEM_PROMPTS_DIR` to support a dedicated local `systemprompt/` folder.
- Kept JSON prompt files ignored from Git while loading them from the configured directory.

## [0.1.38] - 2026-09-14

### Changed

- Disabled arbitrary text System Prompt fallback.
- Limited the System Prompt panel to JSON choices from `SILLYTAVERN_SYSTEM_PROMPTS_DIR` plus `Off`.

## [0.1.37] - 2026-09-14

### Changed

- Added `SILLYTAVERN_SYSTEM_PROMPTS_DIR` for loading multiple JSON prompt files from a SillyTavern folder.
- Moved the live `natural.json` catalog into the configured SillyTavern system-prompts directory.
- Kept System Prompt panel and text commands compatible.

## [0.1.36] - 2026-09-14

### Changed

- Added an inline Telegram choice panel for JSON System Prompt catalogs.
- Kept `/systemprompt list` and `/systemprompt use <name>` for text automation.

## [0.1.35] - 2026-09-14

### Added

- Added private JSON System Prompt choices with `/systemprompt list` and `/systemprompt use <name>`.
- Documented `SILLYTAVERN_SYSTEM_PROMPTS_FILE` and the generic example catalog.
- Kept private prompt content outside the public repository.

## [0.1.34] - 2026-09-14

### Added

- Added a per-session System Prompt with `/systemprompt <text>` and `/systemprompt off`.
- Included the session System Prompt in prompt assembly with macro expansion.
- Added migration support for existing SQLite sessions.

## [0.1.33] - 2026-09-14

### Changed

- Split the message command handler into `commands.py` and `message_commands.py`.
- Kept every implementation module within the recommended 150–500 line range, except intentionally small launcher/runtime files.
- Preserved the modular runtime loader and live behavior.

## [0.1.32] - 2026-09-14

### Changed

- Clarified the new streaming, preset, macro/STscript, World Info editor, and card-version commands in README and Telegram autocomplete.
- Added the new feature commands to the Telegram command menu.

## [0.1.31] - 2026-09-14

### Added

- Added live SSE response updates controlled by `/stream on|off`.
- Added per-chat generation presets with `/preset save|use|delete`.
- Added World Info entry editor commands for list, add, set, and confirmed removal.
- Added safe `/macro` preview and `/stscript note|reset` commands.
- Added character-card backup version listing and confirmed restore.

## [0.1.30] - 2026-09-14

### Security

- Removed the main LLM key fallback for external embedding endpoints.
- Required HTTPS for external provider endpoints, with optional host allowlisting.
- Added DOCX decompression-ratio and uncompressed-size limits.
- Added PDF page-count and extracted-text limits.

## [0.1.29] - 2026-09-14

### Added

- Added reasoning level settings: `none`, `low`, `medium`, `high`, and `max`.
- Persisted reasoning levels per chat session and documented `/settings reasoning medium`.

## [0.1.28] - 2026-09-14

### Changed

- Documented the `streaming: true` provider setting required by SSE-based Chat Completions endpoints.
- Kept the public provider example generic while matching the live streaming configuration model.

## [0.1.27] - 2026-09-14

### Fixed

- Resolved legacy catalog model IDs such as `provider/model` through the configured provider catalog before generating text.
- Prevented valid existing sessions from falling back to the example endpoint and producing DNS failures.

## [0.1.26] - 2026-09-14

### Fixed

- Kept shared `Path` available in the modular runtime namespace so World Info and prompt assembly work during live message processing.

## [0.1.25] - 2026-09-14

### Added

- Added automated distribution ZIP boundary validation to GitHub Actions.
- The CI ZIP check confirms `CHANGELOG.md` is included and runtime/tooling files are excluded.

## [0.1.24] - 2026-09-14

### Changed

- Removed vendor-specific provider IDs, endpoint fallbacks, and runtime labels from the public bridge source.
- Made provider selection, streaming, and bridge data paths configuration-driven.
- Added `SILLYTAVERN_BRIDGE_HOME` for portable private runtime data.
- Kept the live deployment on its existing private data directory through systemd environment settings.

## [0.1.23] - 2026-09-14

### Fixed

- Corrected the GitHub Actions privacy scan to inspect tracked file names instead of matching `.gitignore` and workflow text.

## [0.1.22] - 2026-09-14

### Added

- Added GitHub Actions quality checks for compilation, YAML examples, and public privacy boundaries.
- Added `/providers health` endpoint probes without running inference.
- Added checksum-verified private backups for imported character cards.
- Added RAG `Sources:` footers when indexed documents contribute to a reply.
- Improved `/regen`, `/continue`, `/edit`, and branch variant outgoing-message tracking and cleanup.

## [0.1.21] - 2026-09-14

### Changed

- Clarified in README that release tooling is private maintainer tooling and excluded from public ZIPs.
- Updated `/providers refresh` help text to describe the standalone bridge catalog.
- Removed stale Hermes labels from user-facing provider catalog messages and request metadata.

## [0.1.20] - 2026-09-14

### Changed

- Moved the release publisher to the private maintainer path outside the public repository.
- Removed `scripts/publish_release.sh` and its README instructions from the public source.
- Removed the public release-tooling directory from the next distribution archive.

## [0.1.19] - 2026-09-14

### Changed

- Corrected the public product name typo to `SillyTavern Telegram Bridge`.
- Corrected the service example, release helper default title, README release command, and release branding.

## [0.1.18] - 2026-09-14

### Changed

- Explained why `scripts/publish_release.sh` is included in the public repository.
- Documented that the helper is maintainer-only tooling and is not required by the runtime service.
- Clarified that runtime deployments may omit the `scripts/` directory.

## [0.1.17] - 2026-09-14

### Changed

- Split the monolithic bridge implementation into domain modules under `bridge/`.
- Kept `sillytavern_telegram_bridge.py` as a nine-line compatibility launcher.
- Added README architecture documentation and kept every domain module below 500 lines.
- Preserved the existing command, provider, memory, RAG, media, group, and branch behavior.

## [0.1.16] - 2026-09-14

### Changed

- Updated README examples to use generic provider, model, character, and credential names.
- Documented the standalone provider catalog and optional Anthropic Messages adapter accurately.
- Updated `/help` and Telegram command-menu descriptions to identify the standalone bridge catalog.
- Removed the old provider-runtime label from the user-facing command menu.

## [0.1.15] - 2026-09-14

### Changed

- Added a commented optional Anthropic Messages provider block to `config/providers.example.yaml`.
- Kept the basic example limited to two active generic providers.
- Documented `ANTHROPIC_API_KEY`, `api_endpoint`, `transport`, and `anthropic_version` in the example.

## [0.1.14] - 2026-09-14

### Added

- Synced the Anthropic Messages adapter into the public source.
- Added generic Anthropic content-block and SSE handling to the distributable bridge.

### Verification

- Real Anthropic Messages inference returned `ANTHROPIC_OK`.
- Public bridge compiled successfully.

## [0.1.12] - 2026-09-14

### Changed

- Restored the missing `v0.1.11` entry in the cumulative changelog.
- Corrected release ordering and attribution.
- Kept release notes scoped to their own version changes.

## [0.1.11] - 2026-09-14

### Changed

- Removed local live-environment and credential-copy details from the public cumulative changelog.
- Kept release bodies scoped to their own release.
- Refreshed public documentation without exposing local runtime details.

## [0.1.10] - 2026-09-14

### Changed

- Replaced vendor-specific names in the public provider example with `provider-one` and `provider-two`.
- Added generic `adapter: chat_completions` support for arbitrary OpenAI-compatible provider IDs.
- Updated public environment examples to use generic provider credential names.
- Kept vendor-specific provider catalogs confined to local runtime configuration.

## [0.1.8] - 2026-09-14

### Changed

- Removed the remaining live credential-name reference from public source.
- Added the generic `SILLYTAVERN_RAG_EMBEDDING_API_KEY` setting.
- Refreshed the public ZIP after the privacy cleanup.

## [0.1.7] - 2026-09-14

### Changed

- Removed local live-environment and credential-copy details from the public cumulative changelog.
- Kept release bodies scoped to their own version changes.
- Refreshed the public ZIP with cleaned documentation.

## [0.1.6] - 2026-09-14

### Added

- Anthropic Messages adapter with `/messages` request translation.
- `x-api-key` and `anthropic-version` headers.
- System prompt, alternating message, image-block, and SSE text parsing.
- Optional generic Anthropic provider configuration.

## [0.1.5] - 2026-09-14

### Changed

- Added `SILLYTAVERN_ENV_FILE` for a dedicated bridge environment path.
- Updated the systemd example for an external environment file.
- Documented keeping credentials and runtime state outside the public repository.

## [0.1.4] - 2026-09-14

### Changed

- Renamed provider catalog endpoint field from `api` to `api_endpoint`.
- Added a compatibility fallback for older catalogs using `api`.
- Updated the standalone provider catalog and public example.

## [0.1.3] - 2026-09-14

### Changed

- Removed unsupported Anthropic Messages transport from the basic provider example.
- Added a runtime guard for supported Chat Completions transports.
- Simplified the public provider example to two Chat Completions providers.
- Updated README documentation and provider catalog setup.

## [0.1.2] - 2026-09-14

### Added

- Automatic model discovery through OpenAI-compatible `/models` endpoints.
- TTL-based model catalog cache.
- `/providers refresh` force-refresh command.
- Static YAML fallback when discovery fails.
- `SILLYTAVERN_MODEL_REFRESH_SECONDS` and `SILLYTAVERN_MODEL_CACHE` settings.

## [0.1.1] - 2026-09-14

### Changed

- Provider catalog became independent from the host application's provider configuration.
- Added `SILLYTAVERN_PROVIDER_CONFIG` and `/providers`.
- Updated the example catalog to generic credential names.
- Documented SillyTavern prerequisites and portable setup.

## [0.1.0] - 2026-09-14

### Added

- Telegram character chat backed by SillyTavern character-card metadata.
- Per-chat and per-session SQLite history.
- Character, persona, World Info, and Author's Note selection.
- Two-level provider/model catalog navigation.
- Session-scoped generation settings.
- Response regeneration, swipe variants, branch selection, and continuation.
- Native Telegram edited-message handling with branch replacement.
- Image input, text-to-speech, and voice transcription.
- Hindsight memory, session summaries, and bounded context compression.
- Data Bank RAG with FTS5 and optional embeddings.
- Multi-character group chat with bounded autonomous mode.
- Character-card upload and metadata validation.
- Inline help menu with command categories.
- Bounded background workers for media, documents, and memory retention.

### Security and portability

- Runtime credentials remain in `.env` and are excluded from Git.
- Live state, logs, cards, and user personas are excluded from the public repository.
- Paths and Telegram allowlists are configurable through environment variables.
- Unsupported provider entries are shown as catalog-only rather than exposed as inference options.

### Known limitations

- The bridge reimplements selected SillyTavern concepts and does not execute every native extension or STscript hook.
- Autonomous group mode produces a bounded labeled exchange in one generation request.
- Historical assistant bubbles created before message-ID tracking cannot be deleted during branch replacement.
- Embedding search requires an OpenAI-compatible embedding endpoint; lexical FTS5 remains the fallback.

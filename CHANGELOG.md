# Changelog

All notable changes to **SillyTavern Telegram Bridge** are documented here.

## [Unreleased]

### Changed

- Reduced SQLite contention by keeping RAG embedding work outside write transactions, reusing the realtime sync worker connection, and using a smaller page-cache budget for lightweight worker connections.

## [0.2.017] - 2026-09-19

### Added

- Added an explicit extension registry and Director policy boundary for Group Director customization, with validation and runtime integration.
- Added regression coverage for Director policy behavior, extension registration, group execution invariants, and SQLite contention.

### Fixed

- Serialized SQLite write transactions to reduce contention between concurrent workers.
- Prevented successful native message edits from being reported as rollback failures.
- Preserved Director instruction non-disclosure and behavior-priority semantics across customization and execution.

### Changed

- Made model target selection provider-first and separated story-model selection from utility-model selection.
- Removed the obsolete standalone release-notes file.

## [0.2.016] - 2026-09-19

### Added

- Added explicit duplicate and new-version notifications for uploaded character cards; changed cards are retained with content-hash filenames.

### Changed

- Made `/status` text-only with a formatted Telegram report and removed the obsolete status drilldown panel.
- Externalized privacy-sensitive character, model, user-name, provider, and TTS defaults from source code into environment configuration.
- Removed character and model identity details from startup logs.

## [0.2.015] - 2026-09-19

### Added

- Added World-style per-item panels for Persona, Preset, Data Bank document, and Group character management.
- Added confirmation before deleting Presets and removing Data Bank documents.
- Added confirmation before removing a character from a Group without deleting the native character card.
- Added regression coverage for the managed item-panel layouts.

### Fixed

- Fixed Telegram World panel delivery by preserving the bot token while generating per-item callback tokens.
- Retried one transient Telegram `sendMessage` 404 before reporting a command failure.

### Changed

- Session and Character panels now show select and delete actions on each item row, with in-place refresh after deletion.

## [0.2.014] - 2026-09-19

### Added

- Added covering indexes for cleanup deletes: `callback_tokens_expires_idx` on `callback_tokens(expires_at)`, `panel_sessions_expires_idx` on `panel_sessions(expires_at)`, `operations_state_idx` on `operations(state, updated_at)` to prevent full table scans on every schema init.
- Added `/world` panel actions for uploading validated native World Info JSON and deleting inactive lorebooks with confirmation and reference protection.

### Fixed

- Translated leftover Indonesian-language user-facing error messages (voice/image/character-card/Data Bank size and format limits) to English for consistency with the rest of the bot's UI copy.
- Fixed `/update` version display so an `Unreleased` changelog is compared against its latest released base without overwriting local development changes.

## [0.2.013] - 2026-09-18

### Added

- Added per-session utility-task model routing, invisible Group Director mode, Data Bank document versioning, and budget-aware context compaction.
- Added read-only status/prompt/scene panels, Director-goal and curated-memory panels, summary confirmation, and a synchronous Help fast path.
- Added bounded RAG retrieval improvements, optional SQLite vector support, and safer runtime override loading.

### Changed

- Improved SQLite maintenance, worker connection reuse, shutdown cleanup, and query-planner maintenance.
- Consolidated panel, recovery, memory, card, catalog, and session-column helpers without changing their public behavior.
- Improved default character-name fallback and user-facing Help/README guidance.

### Fixed

- Preserved Data Bank embedding metadata during reindexing and kept legacy embedding rows readable.
- Added lifecycle cleanup coverage for background work and Hindsight session data.

## [0.2.012] - 2026-09-18

### Changed

- Added SQLite connection performance pragmas (`synchronous = NORMAL`, `temp_store = MEMORY`, `cache_size = -64000`, `foreign_keys = ON`); worker connections stay connection-local while WAL and optional extension setup happen once on the schema-init connection.
- Added query planner maintenance (`optimize_database`, `PRAGMA optimize`) after bulk deletions, and dedicated end-of-shutdown disk reclamation (`run_database_maintenance`) with incremental auto-vacuum initialization and a bounded-timeout `VACUUM` on a separate connection.
- Added optional `sqlite-vec` vector extension detection and initialization support, with extension loading always disabled after the attempt.
- Removed the unused native-cache reset hook.
- Removed the redundant Live API Sync JSONL serialization/parsing round trip; API chat records are now validated and normalized directly.
- Kept bounded message/transcript validation, metadata handling, and swipe variants while removing retired JSONL-transfer limits from shared configuration.
- Updated README, Help, environment examples, and provider configuration guidance to describe Live API Sync as the only conversation synchronization path.

## [0.2.011] - 2026-09-17

### Fixed

- Removed retired provider entries and stale model-cache data from the live bridge configuration.
- Added a Hive streaming chat health check for providers that do not expose a `/models` endpoint; JSON requests now include the required content type.
- Recovered streaming replies that end with `finish_reason=length` before producing visible content by retrying with bounded larger output budgets.
- Documented provider health behavior for endpoints without model discovery.

## [0.2.010] - 2026-09-17

### Fixed

- Improved incomplete response recovery for streaming chat providers and accepted both standard SSE `data:` framing variants plus text content blocks.
- Made `/update` a true no-op when the installed release already matches the latest release; it no longer syncs or restarts in that case.
- Renamed the Persona panel action to `Delete inactive` to match its protected-target picker behavior.

## [0.2.009] - 2026-09-17

### Fixed

- Persona deletion now mirrors Character deletion safety: the active Persona is excluded from the delete picker, and Personas referenced by another session are protected.
- Updated README and Help with the inactive/unreferenced Persona deletion rules.

## [0.2.008] - 2026-09-17

### Changed

- Added concise comments to provider and environment configuration examples so each field's purpose is clear.
- Expanded README and Help descriptions for Expressions, image generation, semantic splitting, User dialogue/User action formatting, and quote-driven voice replies.

## [0.2.007] - 2026-09-17

### Added

- Added native SillyTavern expression sprite discovery with session-scoped manual or automatic selection, change-only Telegram delivery, and neutral/fixed-avatar fallbacks.
- Added guarded `/imagine` prompt input and OpenAI-compatible Images API adapter; image generation remains opt-in through provider catalog fields.
- Added semantic Telegram message splitting that prefers paragraphs, newlines, sentence boundaries, and whitespace while preserving the UTF-16 limit.
- Added User dialogue/User action prompt formatting for single-star action spans without changing stored transcript text or user role semantics.

### Changed

- Automatic voice replies now synthesize only model dialogue enclosed in straight double quotes; narration and unquoted text are not spoken.
- Disabled the manual `/tts` command; `/voice` controls automatic quote-driven voice replies.
- Expanded README, Help, provider examples, and environment examples with concise configuration guidance.
- Removed live deployment endpoint and port details from the changelog.

## [0.2.006] - 2026-09-17

### Added

- Added a dedicated keyless OpenCode Muse Free transport using the `/responses` endpoint, canonical OpenCode session/request headers, low reasoning effort, and a bounded output-token floor.
- Added panel-first input flows for `/edit`, `/remember`, `/macro`, and `/tts`, with session binding, expiry cleanup, validation, and `/cancel` support.
- Added Memory search, Data Bank search, and a safe `/stscript` Reset action panel.
- Added native Persona deletion with confirmation, reference cleanup, and avatar preservation.

### Changed

- `/note` is now the sole Author's Note interface; the duplicate STscript Note action was removed.
- `/status` now shows the custom session title followed by the technical session ID.

### Fixed

- OpenCode Muse Contributor Free generation now succeeds through the bridge adapter instead of returning HTTP 403.

## [0.2.005] - 2026-09-17

### Changed

- Made Persona storage fully native to SillyTavern: settings and avatars are the source of truth, SQLite stores only native avatar references, and the bridge catalog is archived.
- Removed Persona import/export controls and obsolete bridge catalog scope; native Persona create/edit now writes directly to SillyTavern storage with verified backups and readback.

## [0.2.004] - 2026-09-17

### Added

- Added validated naming before creating normal, Character-driven, or topic-local group sessions; cancellation leaves no empty session behind.
- Added deterministic session-prefixed Hindsight document IDs, local document mapping, and fail-closed targeted memory cleanup when deleting an inactive session.
- Added native persona interoperability through SillyTavern's authenticated settings API and native User Avatars directory, with verified backups, atomic writes, and readback checks.
- Added safe recovery of session character references after native card renames using a unique PNG image fingerprint or unique embedded card name.
- Added a topic-only New group session wizard that chains Character and World Info selection without colliding with ordinary session flows.

### Changed

- Character cards and World Info now use SillyTavern's native local paths directly; bridge JSON remains cache/fallback state where needed.
- Increased Character, Persona, System Prompt, and World Info catalog limits to 40 entries.
- Removed automatic chat-file synchronization, manual JSONL Export/Import, document-import routing, recovery state machines, fallback-only database helpers, and their panel controls. Live API Sync is now the only conversation synchronization path.
- Simplified `/sync`, README, and Help around Live API Sync only.

### Fixed

- Rebound the Character panel to a uniquely renamed native card and refreshed its embedded display name.
- Treated an unchanged Character panel refresh as a successful idempotent action instead of reporting `Callback processing failed`.
- Normalized isolated invalid Persona entries without blocking the catalog, surfaced malformed-catalog warnings in the panel, and preserved newer concurrent edits when native export rollback was needed.
- Enforced fixed response-language selections with a final bounded render pass across normal, image, edit, regenerate, and continue paths; Auto mode remains single-pass and fixed-language streaming previews are suppressed.
- Explicitly disabled hidden reasoning on OpenRouter when the configured reasoning budget is zero, and automatically requested one bounded continuation when a non-stream response stopped at its output-token limit.
- Changed `/reset` to clear only the active session's SQLite conversation and session-scoped Hindsight documents; other sessions and the shared per-chat Hindsight bank are preserved.
- Enforced Hindsight recall as active-session-only, ignored legacy broader scope metadata, and removed user/character scope choices from the memory panel.
- Removed the obsolete Hindsight scope panel; `/memory search <query>` and `/remember <fact>` remain text-input commands, while bridge recall is verified against the live Hindsight API.
- Moved Persona metadata to native SillyTavern settings and native User Avatars; bridge SQLite now stores only the native avatar reference, and the bridge JSON catalog is archived.
- Kept session and group setup callback state scoped to the correct chat, topic, and session.

## [0.2.003] - 2026-09-16

### Added

- Added Telegram persona editor for creating and editing persona name/description with scoped input, backup verification, and atomic JSON writes.
- Added opt-in Phase 3 near-real-time bidirectional sync through SillyTavern's supported loopback HTTP chat API with cookie/CSRF authentication, conflict detection, and Phase 2 fallback.

### Changed

- Added a persona information review step before editing, with separate name, description, or combined edit actions.
- Added opt-in Phase 2 file synchronization with checkpoints, conflict detection, edit/delete transfer, and compatible swipe transfer.

### Fixed

- Show visible chat feedback when a queued callback reaches an expired panel, while preserving callback-toast behavior for non-queued callbacks.
- Removed expired panel bindings before rejecting stale actions and classified swipe callbacks explicitly as session-scoped.
- Serialized persona catalog read-modify-write operations globally and used unique temporary files for atomic replacements.
- Treated an unchanged model/provider/world panel as an idempotent refresh instead of reporting `Callback processing failed` after a successful model refresh.

## [0.2.002] - 2026-09-16

### Added

- Added Phase 1 manual bidirectional sync panel at `/sync`.
- Added stable per-session sync IDs and transfer checkpoints in SQLite.
- Added `bridge_sync` metadata to SillyTavern-compatible JSONL exports.
- Added safe JSONL imports as separate sessions, preserving the active session.

### Changed

- Removed standalone `/export` and `/import` commands; use the `/sync` panel for SillyTavern transfers.
- Reworded Help navigation and detail pages with task-based category names and explicit action guidance.

### Documentation

- Documented the Phase 1 sync scope: transcript, title, character reference, persona, World Info references, Author's Note, and compatible generation settings.

## [0.2.001] - 2026-09-16

### Added

- Added manual group user-turn gating with Claim turn and Pass user turn actions.
- Added topic-only New group session wizard chaining Character and World Info selection.
- Added Help category → command → detailed-information drill-down panels.

### Fixed

- Made repeated group Disable actions idempotent when Telegram reports that the panel is unchanged.

## [0.2.000] - 2026-09-16

### Fixed

- Displayed current generation field values in the Settings panel after valid input.
- Tracked and removed invalid-input feedback together with its pending prompt on `/cancel` or Close.
- Added Character → Session chaining so a selected character is applied only to the chosen target session.
- Extracted pending-input, command-routing, and reply-persistence flows from the main message handler.
- Added pinned pytest development tooling and CI execution.

## [0.1.107] - 2026-09-16

### Fixed

- Routed the shared inline-keyboard removal helper through panel close/delete.
- Made every panel Cancel/Close path remove its message and binding instead of leaving stale text.
- Preserved valid `Panel closed.` fallback behavior when Telegram rejects deletion.

## [0.1.106] - 2026-09-16

### Fixed

- Fixed panel close/delete callbacks to read nested Telegram callback message IDs correctly.
- Prevented `message_id=None` HTTP 400 errors and generic callback-processing failures.
- Added exact message-ID regression coverage for Author's Note and shared panel close paths.

## [0.1.105] - 2026-09-16

### Fixed

- Changed character-card Upload guidance to reuse the existing panel message with Back and Close buttons.
- Changed Character panel Cancel/Close to delete the panel message and clear its binding.
- Added regression tests for the upload guidance and close lifecycle.

## [0.1.104] - 2026-09-16

### Fixed

- Changed the shared panel Close action to delete the panel message and clear its session binding.
- Removed the `/settings <name> <value>` text mutation path and fallback instructions from the Settings panel.
- Added lifecycle coverage for Settings, Preset Save, and STT User input panels.

## [0.1.103] - 2026-09-16

### Fixed

- Fixed panel close/delete to read callback message IDs correctly.
- Made delete the primary panel-close operation, with a valid `Panel closed.` fallback when Telegram rejects deletion.
- Applied the same old-panel cleanup to Settings, Preset Save, and STT User input callbacks.
- Removed closed panel bindings to prevent stale callback processing failures.

## [0.1.102] - 2026-09-16

### Fixed

- Fixed Author's Note User input to close the original panel before waiting for text.
- Fixed Author's Note Cancel to remove the old panel binding and prevent stuck duplicate panels.
- Added regression coverage for panel close/delete ordering and removed alias behavior.

## [0.1.101] - 2026-09-16

### Fixed

- Removed pre-production compatibility guards and legacy message migration branches for `/model`, `/authornote`, and `/system`.
- Added one generic unknown-slash-command guard before model generation.
- Fixed Author's Note Cancel to close and delete the previous panel message.

## [0.1.99] - 2026-09-16

### Removed

- Removed the user-facing `/authornote` legacy alias.
- Kept `/note` as the only Author's Note command and added a safe migration response for old `/authornote` messages.

## [0.1.98] - 2026-09-16

### Changed

- Changed `/note` and `/authornote` to open an Author's Note panel instead of accepting direct text or `off` mutations.
- Added Off and session-scoped User input actions with expiry and `/cancel` support.
- Added regression coverage for panel routing and pending note input.

## [0.1.97] - 2026-09-16

### Added

- Added safe inactive-session deletion from the `/session` panel with a separate confirmation step.
- Protected the active session and sessions with queued or running jobs.
- Removed all local SQLite data associated with a deleted session while retaining shared chat Hindsight memory.

## [0.1.96] - 2026-09-16

### Changed

- Clarified `/reset` confirmation wording: SQLite reset is active-session-only, while Hindsight purge covers the entire Telegram chat bank.

## [0.1.95] - 2026-09-16

### Refactored

- Split the oversized `common.py` into `common.py` and `cards.py` while preserving the shared runtime loader namespace.
- Split the PDF regression test into its own test module.
- Enforced a maximum of 500 lines for every Python file; the largest current file is 485 lines.

## [0.1.94] - 2026-09-16

### Documentation

- Synchronized README and `/help` with the canonical `/providers` panel and removed `/model` command.
- Documented provider-action validation and the voice input Auto/User input language panel.

## [0.1.93] - 2026-09-16

### Fixed

- Routed `/stscript reset` through the reset confirmation and Hindsight purge flow.
- Rejected unknown `/providers` actions before normal generation.
- Added an STT language panel with Auto, fixed language choices, pagination, and session-scoped User input.

## [0.1.92] - 2026-09-16

### Removed

- Removed the user-facing `/model` command and its Telegram command-menu entry.
- Made `/providers` the single provider/model panel entry point.
- Kept a safe migration response for old `/model` messages so they cannot fall through to model generation.

## [0.1.91] - 2026-09-16

### Changed

- Removed direct text fallback handling for model, persona, preset, and branch selection commands.
- Preset use/delete now resolve only through panel callbacks; preset save uses the panel's two-step name input.
- Removed stale direct-selection documentation and added a regression assertion for the deleted preset text handler.

## [0.1.90] - 2026-09-16

### Changed

- Routed `/model <id>`, `/persona <id>`, `/preset use|delete <name>`, and `/branch <number>` to their panels instead of direct text mutations.
- Added a panel Save preset action with session-scoped, expiring two-step name input.
- Added panel regression coverage for selection redirects and preset creation.

## [0.1.89] - 2026-09-16

### Changed

- Changed `/reset` to require an explicit Telegram confirmation panel.
- Reset confirmation now purges and recreates the entire per-chat Hindsight bank before deleting session data.
- Failed Hindsight purge preserves the session and reports the failure instead of partially resetting.

## [0.1.88] - 2026-09-16

### Fixed

- Changed `/reset` to clear the active conversation and restart from the character card's opening greeting.
- Persisted the reset greeting as the first assistant message without invoking model generation.
- Added durable reset replay handling and regression coverage.

## [0.1.87] - 2026-09-16

### Security

- Isolated PDF Data Bank extraction in a resource-limited subprocess.
- Added bounded PDF parser input, page count, text output, CPU time, memory, and parent timeout controls.
- Added regression coverage for valid and invalid PDF parsing.

## [0.1.86] - 2026-09-16

### Added

- Added a session-scoped model response language panel and `/language` command.
- Added validated language persistence in session storage, including JSONL export/import.
- Added response-language instructions to model prompt assembly and status output.

## [0.1.85] - 2026-09-16

### Security and reliability

- Added crash-safe durable operation recovery for generation, continuation, edit, export, start, TTS, and JSONL import flows.
- Added durable scheduler handoff protection, bounded recovery batches, and stale swipe/session callback protection.
- Restored public Python 3.11 CI and audit regression tests.

## [0.1.84] - 2026-09-16

### Changed

- Removed the legacy Hindsight endpoint compatibility alias from the bridge script.
- Set the only supported Hindsight endpoint variable to `HINDSIGHT_API_URL`.
- Corrected the default local Hindsight endpoint configuration.
- Audited live bridge environment/configuration without exposing credential values.

## [0.1.83] - 2026-09-16

### Fixed

- Configured the bridge to use the supported Hindsight API endpoint.
- Verified Hindsight retain, recall, and delete behavior without exposing deployment details.
- Updated README and `.env.example` to use the actual Hindsight API URL setting.

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

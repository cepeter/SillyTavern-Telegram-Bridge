# PR #64 Startup Purity and Environment Ownership — Design

Date: 2026-09-22  
Status: Written-spec review pending  
Repository: `cepeter/SillyTavern-Telegram-Bridge`  
Baseline: merged `main` at `6501b8f36f1b8457fb8e48e407677fc4d86e1a86`  
Predecessor: PR #63 — media composition import made type-only  
Branch: `refactor/pr64-startup-purity-environment`

## 1. Purpose

PR #64 establishes an explicit process-startup boundary.

The bridge already has explicit application-service composition, but process bootstrap still has hidden import-time effects:

- the executable implements its own environment-file parser;
- `bridge.common` derives several path constants from `os.environ`;
- importing `bridge.common` creates the log directory;
- importing `bridge.common` installs a rotating file logger;
- importing `bridge.common` constructs both thread-pool executors;
- `bridge.main` calls `load_env_file()` even though the executable already loads the environment before importing application modules;
- the Telegram allowlist is parsed into `BridgeConfig` but is not currently required to be non-empty or numeric;
- the legacy single-file system-prompt configuration remains alongside the canonical system-prompt directory.

The target invariant is:

> Importing bridge modules defines code and immutable configuration values but does not create files, install process handlers, start worker resources, or perform environment-file loading. Process bootstrap occurs explicitly before application imports, and startup validation rejects incomplete or malformed runtime configuration.

This repository is preproduction. Backward compatibility with the old bootstrap parser, empty allowlists, or the retired single-file system-prompt configuration is not required.

## 2. Current state

### 2.1 Executable owns an inline environment parser

`sillytavern_telegram_bridge.py` currently defines `bootstrap_env()`, reads `SILLYTAVERN_ENV_FILE`, mutates `os.environ`, then imports `bridge.main`.

This startup order is directionally correct because environment-derived module constants must see the loaded values. The problem is ownership and semantics: bootstrap parsing is embedded in the launcher and silently ignores malformed lines.

### 2.2 Environment loading is duplicated

`bridge.main.main()` also calls `load_env_file()` from `bridge.common`.

That call occurs after `bridge.main`, `bridge.config`, and their dependency graph have already been imported, so environment-derived module constants may already be fixed before this second load.

The duplicate loader therefore provides misleading late mutation rather than a reliable configuration boundary.

### 2.3 bridge.common creates process resources at import time

Current import-time behavior includes:

```python
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(...)
_GENERATION_EXECUTOR = ThreadPoolExecutor(...)
_UTILITY_EXECUTOR = ThreadPoolExecutor(...)
```

This means importing an application-support module mutates the filesystem, logging configuration, and thread resources.

### 2.4 Runtime path ownership is split

`bridge.config` owns major application paths such as:

- `BRIDGE_HOME`;
- `DB_FILE`;
- `SILLYTAVERN_DIR`;
- `CHARACTER_DIR`;
- `WORLD_DIR`;
- `SYSTEM_PROMPTS_DIR`.

`bridge.common` separately owns:

- `ENV_FILE`;
- `PROVIDER_CONFIG_FILE`;
- `MODEL_CACHE_FILE`;
- `CHARACTER_BACKUP_DIR`;
- `LOG_FILE`.

PR #64 makes `bridge.config` the canonical owner for all environment-derived runtime paths.

### 2.5 Startup validation is incomplete

`load_bridge_config()` already normalizes the Telegram allowlist to a frozen set, but `validate_bridge_config()` currently validates only:

- bot token;
- default model;
- default character file;
- character card existence.

It does not reject:

- an empty allowlist;
- non-numeric Telegram IDs.

### 2.6 Legacy single-file system prompt configuration remains

`bridge.config` still exposes `SYSTEM_PROMPTS_FILE`.

The current architecture already has `SYSTEM_PROMPTS_DIR` as the canonical prompt collection. PR #64 removes the single-file compatibility path.

## 3. Goals

PR #64 must:

1. add a canonical `bridge.environment` module for pre-import environment bootstrap;
2. keep environment loading before importing `bridge.main`;
3. remove launcher-local environment parsing;
4. remove `bridge.common.load_env_file()`;
5. make environment parsing strict and testable;
6. preserve process-environment precedence over values from the environment file;
7. move runtime path constants into `bridge.config`;
8. ensure importing `bridge.common` creates no log directory/file and no thread pool;
9. configure file logging explicitly during startup;
10. ensure the log file receives private permissions when created;
11. create background executors lazily on first use;
12. make executor shutdown safe when no executor was ever created;
13. require a non-empty numeric Telegram allowlist at startup;
14. remove the legacy single-file system-prompt configuration path;
15. add permanent regression tests for import purity and bootstrap order;
16. preserve application-service composition and runtime behavior outside process bootstrap.

## 4. Non-goals

PR #64 does not:

- change SQLite schema or migration history;
- reset preproduction databases;
- modify RAG storage or retrieval;
- rename `phase3_*` Live Sync symbols;
- remove `bridge.runtime_context`;
- introduce explicit request/job context;
- split `bridge.main`;
- redesign application services;
- change durable-job semantics;
- change Telegram routing behavior;
- introduce a generic configuration framework;
- introduce dotenv or another external parser dependency;
- dynamically reload environment-derived configuration after application imports.

Those belong to PR #65–#68.

## 5. Architectural approaches considered

### 5.1 Selected: explicit pre-import environment boundary plus lazy process resources

Create a small `bridge.environment` module that is safe to import before the rest of the application.

The executable becomes:

```text
sillytavern_telegram_bridge.py
        |
        v
bridge.environment.bootstrap_environment()
        |
        v
import bridge.main
        |
        v
bridge.main.main()
```

Application imports may then safely derive immutable defaults from the already-loaded process environment.

Logging and thread pools are created only through explicit runtime functions after import.

Advantages:

- deterministic startup order;
- one environment-file parser;
- testable bootstrap semantics;
- no file/thread creation on ordinary imports;
- compatible with later `bridge.main` decomposition;
- no service locator or mutable configuration registry.

This is the selected approach.

### 5.2 Rejected: load the environment inside bridge.main

This would be too late because importing `bridge.main` imports modules that derive constants from `os.environ`.

It would either preserve stale values or require a broad mutable-config redesign that is out of scope.

### 5.3 Rejected: replace module constants with a dynamic configuration registry

A process-global mutable configuration registry would remove import-order concerns but create a new hidden dependency surface.

The repository already has explicit immutable `BridgeConfig` for startup credentials and service composition. PR #64 should not add another runtime configuration mechanism.

## 6. Environment bootstrap contract

### 6.1 New bridge.environment owner

Add `bridge/environment.py`.

It owns:

```python
DEFAULT_BRIDGE_HOME
environment_file(...)
load_environment_file(...)
bootstrap_environment(...)
```

The module may import only lightweight standard-library dependencies required for parsing paths/environment values.

It must not import `bridge.config`, `bridge.common`, `bridge.main`, or application modules whose configuration depends on the environment.

### 6.2 Environment-file location

`environment_file(environ)` resolves:

1. `SILLYTAVERN_ENV_FILE` when present;
2. otherwise `~/.local/share/sillytavern-telegram/.env`.

The result is expanded with `Path.expanduser()`.

`SILLYTAVERN_BRIDGE_HOME` does not change the default environment-file location in PR #64. The executable historically uses the fixed application data location for locating the bootstrap file before the rest of configuration is known. A custom environment-file path remains available through `SILLYTAVERN_ENV_FILE`.

### 6.3 Parsing semantics

`load_environment_file(path, environ)`:

- returns silently if the path does not exist;
- raises `RuntimeError` if the path exists but is not a regular file;
- reads UTF-8 with BOM support;
- ignores blank lines;
- ignores lines whose first non-space character is `#`;
- accepts optional `export ` prefix before an assignment;
- requires every non-comment line to contain `=`;
- requires the key to match `[A-Za-z_][A-Za-z0-9_]*`;
- trims surrounding whitespace around key and value;
- strips matching single or double quotes that surround the entire value;
- rejects an unterminated quoted value;
- keeps unquoted value contents after surrounding whitespace trimming;
- uses `setdefault` so an existing process-environment value always wins;
- does not perform shell interpolation, escape decoding, command substitution, or variable expansion.

A malformed file fails startup instead of being partially ignored.

### 6.4 Bootstrap function

`bootstrap_environment(environ=None)`:

1. resolves the environment-file path from the target mapping;
2. loads it using the strict parser;
3. returns the resolved path.

This function is deterministic and directly unit-testable with a temporary mapping and path.

## 7. Executable contract

`sillytavern_telegram_bridge.py` becomes a minimal launcher:

```python
#!/usr/bin/env python3
"""Telegram bridge executable entry point."""
from __future__ import annotations

from bridge.environment import bootstrap_environment


bootstrap_environment()
from bridge.main import main


if __name__ == "__main__":
    main()
```

The import after `bootstrap_environment()` is intentional and must be preserved.

An architecture test should guard that bootstrap occurs before importing `bridge.main`.

The launcher no longer owns parsing logic.

## 8. Canonical runtime path ownership

Move these constants to `bridge.config`:

- `ENV_FILE`;
- `LOG_FILE`;
- `PROVIDER_CONFIG_FILE`;
- `MODEL_CACHE_FILE`;
- `CHARACTER_BACKUP_DIR`.

Existing modules import them from `bridge.config`.

No compatibility re-export is added solely to preserve the old `bridge.common` ownership.

`ENV_FILE` represents the configured runtime environment-file path for permissions/documentation after application import. The pre-import bootstrap itself remains owned by `bridge.environment`.

## 9. Logging lifecycle

### 9.1 No import-time logging mutation

Delete import-time:

```python
LOG_FILE.parent.mkdir(...)
logging.basicConfig(...)
```

from `bridge.common`.

### 9.2 Explicit configuration

Add:

```python
configure_logging(log_file: Path = LOG_FILE) -> None
```

Behavior:

- create the log parent directory when called;
- install one rotating file handler for the resolved target path;
- be idempotent for repeated calls with the same target;
- preserve the existing log format, size, rotation count, and INFO root level;
- avoid adding duplicate handlers;
- immediately enforce private permissions on the created log file when the platform supports chmod;
- log a warning rather than crash if a post-creation chmod fails.

`bridge.main.main()` calls `configure_logging()` during startup before normal operational logging.

### 9.3 Permissions ordering

`enforce_runtime_permissions()` remains responsible for the broader private runtime tree.

The logging component additionally protects the log it creates so security does not depend on whether the permissions sweep ran before or after the file was opened.

## 10. Background executor lifecycle

### 10.1 Lazy ownership

Replace import-time executor instances with:

```python
_GENERATION_EXECUTOR: ThreadPoolExecutor | None = None
_UTILITY_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()
```

`_executor_for(label)` creates the selected pool under `_EXECUTOR_LOCK` on first demand.

The existing worker counts and thread names remain unchanged:

- generation: 3 workers, `st-generation`;
- utility: 2 workers, `st-utility`.

### 10.2 Shutdown

`shutdown_background_executors()`:

1. stops accepting new background work;
2. drains tracked futures;
3. atomically detaches any instantiated executors under the executor lock;
4. shuts down only non-`None` executors;
5. leaves both module references as `None`.

This supports:

- shutdown before any job has been submitted;
- normal shutdown after one or both pools have been created;
- repeated test setup without retaining previously shut-down pool objects.

PR #64 does not change queue admission, per-chat serialization, durable backlog dispatch, or job semantics.

## 11. Startup validation

Extend `validate_bridge_config(config)`.

It must reject an empty `allowed_users` set:

```text
required SILLYTAVERN_TELEGRAM_ALLOWED_USERS is missing from .env
```

It must reject any value that is not decimal digits.

The error should identify invalid values without exposing credentials.

The allowlist remains immutable in `BridgeConfig`.

No implicit default user is introduced.

## 12. Retire legacy single-file system-prompt configuration

Remove:

```python
SYSTEM_PROMPTS_FILE
```

and all logic whose only purpose is to load one specifically configured prompt file.

`SYSTEM_PROMPTS_DIR` remains the canonical owner for system-prompt discovery.

If source scanning finds a non-compatibility use that does not have an equivalent directory-based flow, implementation must migrate that behavior to `SYSTEM_PROMPTS_DIR`; it must not retain a compatibility alias.

Environment examples/documentation referencing `SILLYTAVERN_SYSTEM_PROMPTS_FILE` are removed.

## 13. main startup order

The target startup ordering is:

```text
launcher:
    bootstrap environment
    import main

main():
    initialize extensions
    parse CLI arguments
    refresh environment-backed Live Sync config
    load + validate BridgeConfig
    validate provider credential
    enforce runtime permissions
    configure logging
    build BridgeServices
    continue startup
```

The exact relative order of `enforce_runtime_permissions()` and `configure_logging()` is not relied on for log confidentiality because `configure_logging()` protects its own created log.

The old `load_env_file()` call disappears.

## 14. Import-purity boundary

PR #64's import-purity guarantee is deliberately scoped.

Importing `bridge.common` must not:

- create the log directory;
- create/open the log file;
- add the rotating file handler;
- instantiate either thread-pool executor.

PR #64 does not promise that every bridge module is completely side-effect free in all respects. Broader context/global-state cleanup belongs to later phases.

## 15. Failure semantics

Configuration/bootstrap failures are startup failures.

Examples:

- malformed environment assignment;
- invalid environment variable name;
- unterminated quote;
- environment path is not a regular file;
- missing Telegram allowlist;
- non-numeric Telegram ID;
- missing existing required credentials/card.

These failures must occur before polling/worker startup.

Missing environment files remain valid because deployments may provide the environment entirely through the process environment.

Operational failures after startup retain their current semantics.

## 16. Test strategy

Implementation uses TDD.

### 16.1 Environment parser RED tests

Add `tests/test_environment.py` covering:

- missing file is a no-op;
- ordinary `KEY=VALUE`;
- whitespace;
- comments;
- optional `export `;
- single-quoted value;
- double-quoted value;
- UTF-8 BOM;
- existing target value wins;
- malformed line without `=` raises;
- invalid variable name raises;
- unterminated quote raises;
- directory supplied as environment file raises;
- custom `SILLYTAVERN_ENV_FILE` is honored.

### 16.2 Launcher-order guard

Add a source-level architecture test proving:

- launcher imports `bootstrap_environment`;
- invokes it before `from bridge.main import main`;
- no launcher-local `def bootstrap_env` remains.

### 16.3 Import-purity RED tests

Use a subprocess or isolated import test so prior module state cannot hide side effects.

Prove that importing `bridge.common` with a temporary `SILLYTAVERN_BRIDGE_HOME`:

- does not create the log directory;
- does not create a log file;
- leaves generation executor `None`;
- leaves utility executor `None`.

### 16.4 Executor lifecycle tests

Prove:

- first generation submission creates only the generation executor;
- first utility submission creates only the utility executor;
- repeated submissions reuse the same pool;
- shutdown before any executor exists succeeds;
- shutdown after use clears both references;
- existing queue/backlog behavior tests remain green.

### 16.5 Logging tests

Prove:

- no handler is installed by importing `bridge.common`;
- `configure_logging(temp_path)` creates one rotating handler;
- repeated configuration for the same target is idempotent;
- the created file is private on POSIX-capable test environments;
- logging configuration does not create thread pools.

### 16.6 Configuration validation tests

Extend composition tests for:

- empty Telegram allowlist;
- non-numeric allowlist member;
- numeric allowlist remains accepted.

### 16.7 Compatibility residue guards

Architecture tests should reject:

- `def load_env_file(`;
- launcher-local `def bootstrap_env(`;
- `SYSTEM_PROMPTS_FILE`;
- import-time `ThreadPoolExecutor(...)` assignments in `bridge.common`;
- import-time `logging.basicConfig(...)`;
- import-time `LOG_FILE.parent.mkdir(...)`.

## 17. Expected file scope

Likely production files:

- `sillytavern_telegram_bridge.py`;
- `bridge/environment.py` — new;
- `bridge/config.py`;
- `bridge/common.py`;
- `bridge/composition.py`;
- `bridge/main.py`;
- modules importing runtime path constants from `bridge.common`;
- modules using `SYSTEM_PROMPTS_FILE`.

Likely tests:

- `tests/test_environment.py` — new;
- `tests/test_background_lifecycle.py`;
- `tests/test_composition.py`;
- architecture/import-boundary tests;
- system-prompt/catalog tests affected by single-file retirement.

Documentation:

- `.env.example` if it contains retired variables;
- `README.md` only where startup/environment behavior is currently documented.

Exact file count is not binding. Scope is defined by startup purity and environment/path ownership.

## 18. Acceptance criteria

PR #64 is complete when:

1. `bridge.environment` is the sole environment-file parser.
2. Environment bootstrap runs before `bridge.main` import.
3. Malformed environment files fail explicitly.
4. Existing process environment wins over file values.
5. `bridge.main` no longer loads the environment file.
6. Runtime path constants have one canonical owner in `bridge.config`.
7. Importing `bridge.common` creates no log directory/file.
8. Importing `bridge.common` creates no thread-pool executor.
9. Logging is configured explicitly during startup.
10. Log-file creation applies private file permissions.
11. Executors are created lazily and shutdown handles unused pools.
12. Empty Telegram allowlists fail startup validation.
13. Non-numeric Telegram allowlist entries fail startup validation.
14. `SYSTEM_PROMPTS_FILE` and its compatibility behavior are deleted.
15. No new configuration registry or service locator is introduced.
16. Service composition, durable jobs, Telegram routing, and application behavior remain unchanged.
17. Full CI-equivalent validation passes at the exact PR head.
18. Final diff review contains no schema, RAG, Live Sync naming, runtime-context, or `main.py` decomposition work.

## 19. Follow-up boundary

After PR #64 merges, proceed in this order:

1. PR #65 — preproduction database baseline reset plus RAG legacy-state retirement;
2. PR #66 — Live Sync and architecture-test naming cleanup with no compatibility aliases;
3. PR #67 — explicit request/job context replacing `bridge.runtime_context`;
4. PR #68 — decompose `bridge.main` after process and request state are explicit.

PR #64 must not preempt those phases.

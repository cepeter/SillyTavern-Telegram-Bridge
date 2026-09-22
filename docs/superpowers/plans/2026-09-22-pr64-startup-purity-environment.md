# PR #64 Startup Purity and Environment Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make process bootstrap explicit and strict, remove import-time logging/thread-pool side effects, centralize runtime path ownership, require a valid Telegram allowlist, and delete the legacy single-file system-prompt fallback.

**Architecture:** `bridge.environment` becomes the only pre-import environment-file bootstrap owner. `bridge.config` owns the remaining environment-derived runtime paths, while `bridge.common` creates logging and thread-pool resources only through explicit/lazy runtime functions. The executable loads the environment before importing `bridge.main`; application-service composition, persistence, Live Sync naming, runtime context, and `bridge.main` decomposition remain unchanged.

**Tech Stack:** Python 3.11, standard-library `pathlib`, `logging`, `RotatingFileHandler`, `concurrent.futures.ThreadPoolExecutor`, `unittest`, `pytest`, existing GitHub Actions CI.

**Spec:** `docs/superpowers/specs/2026-09-22-pr64-startup-purity-environment-design.md`

## Global Constraints

- The repository is preproduction; backward compatibility with the old bootstrap parser, empty allowlists, or `SILLYTAVERN_SYSTEM_PROMPTS_FILE` is not required.
- Environment-file loading must happen before importing `bridge.main`.
- `bridge.environment` must not import `bridge.config`, `bridge.common`, `bridge.main`, or environment-dependent application modules.
- Existing process-environment values always win over environment-file values.
- Missing environment files are valid; malformed existing files fail startup explicitly.
- No shell interpolation, command substitution, variable expansion, or escape decoding is added.
- Importing `bridge.common` must not create a log directory/file, install a rotating file handler, or instantiate a thread-pool executor.
- `bridge.environment` owns environment-file path resolution; `bridge.config` owns the other migrated runtime paths.
- `configure_logging()` must be idempotent for the same resolved target and must protect a newly created log file with private permissions where chmod is supported.
- Background executors remain 3 generation workers and 2 utility workers with the existing thread-name prefixes.
- Background queueing, per-chat serialization, durable-job behavior, and application-service composition must not change.
- `validate_bridge_config()` must reject an empty allowlist and any non-decimal user ID.
- `SYSTEM_PROMPTS_DIR` is the only system-prompt source; no alias or fallback for `SYSTEM_PROMPTS_FILE` is retained.
- Do not change schema/RAG behavior, `phase3_*` names, `bridge.runtime_context`, or split `bridge.main` in this PR.
- Use TDD for each slice and validate the exact PR head before completion.
- Do not merge automatically.

## Review Focus

- **A value contains additional `=` characters:** `TOKEN=a=b=c` must parse as key `TOKEN` with value `a=b=c`, not fail or truncate. Task 1 pins this.
- **`SILLYTAVERN_BRIDGE_HOME` is customized but `SILLYTAVERN_ENV_FILE` is not:** bootstrap must still use the historical fixed default `~/.local/share/sillytavern-telegram/.env`. Task 1 pins this.
- **Logging is configured twice for the same path:** exactly one matching rotating file handler must remain. Task 3 pins this.
- **Shutdown happens before any background job ever creates an executor, or is invoked again after shutdown:** shutdown must not crash and executor references must remain/reset to `None`. Task 4 pins both cases.
- **A malformed allowlist contains whitespace, duplicates, or one invalid token:** normalization may deduplicate valid IDs, but any non-decimal retained member must fail validation rather than silently widening or narrowing access. Task 5 pins this.

---

## File Structure

### New production file

- `bridge/environment.py` — strict pre-import environment-file discovery and parsing only.

### Production files modified

- `sillytavern_telegram_bridge.py` — minimal bootstrap → import-main launcher.
- `bridge/config.py` — canonical owner of log/provider-cache/backup paths; delete `SYSTEM_PROMPTS_FILE`.
- `bridge/common.py` — import-pure logging/executor lifecycle and runtime permission ownership.
- `bridge/card_content.py` — directory-only system-prompt discovery.
- `bridge/catalog.py` — import provider/cache paths from `bridge.config`.
- `bridge/generation.py` — import provider path from `bridge.config`.
- `bridge/telegram.py` — import character-backup path from `bridge.config` rather than through `bridge.common`.
- `bridge/composition.py` — stricter Telegram allowlist validation.
- `bridge/main.py` — remove late environment load; explicitly configure logging during startup.
- Other modules are changed only if the final residue scan finds an import of one of the moved path constants.

### Test files

- Create: `tests/test_environment.py` — strict parser/bootstrap contract.
- Create: `tests/test_startup_purity.py` — isolated import/logging purity contract.
- Modify: `tests/test_background_lifecycle.py` — lazy executor creation/shutdown.
- Modify: `tests/test_composition.py` — allowlist validation and startup wiring expectations.
- Modify: `tests/test_catalog_limits.py` — remove single-file prompt fixture and prove directory-only prompt behavior.
- Modify: `tests/test_phase7c_final_runtime_cutover.py` — launcher bootstrap-order guard.
- Modify: `tests/test_native_runtime_retirement.py` — permanent startup-compatibility residue guards.

### Documentation/config examples

- `.env.example` — remove `SILLYTAVERN_SYSTEM_PROMPTS_FILE`.
- `README.md` — document strict pre-import environment bootstrap, required numeric Telegram allowlist, and directory-only system prompts only where current setup/architecture sections require it.

---

### Task 1: Introduce the Strict Pre-Import Environment Boundary

**Files:**
- Create: `bridge/environment.py`
- Create: `tests/test_environment.py`
- Modify: `sillytavern_telegram_bridge.py`
- Modify: `tests/test_phase7c_final_runtime_cutover.py`

**Interfaces:**
- Produces:
  - `DEFAULT_BRIDGE_HOME: Path`
  - `environment_file(environ: MutableMapping[str, str] | None = None) -> Path`
  - `load_environment_file(path: Path, environ: MutableMapping[str, str] | None = None) -> None`
  - `bootstrap_environment(environ: MutableMapping[str, str] | None = None) -> Path`
- Consumes: standard-library `os`, `Path`, `re`, and `MutableMapping` only.

- [ ] **Step 1: Write failing environment parser tests**

Create `tests/test_environment.py` with focused tests:

```python
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bridge.environment import (
    DEFAULT_BRIDGE_HOME,
    bootstrap_environment,
    environment_file,
    load_environment_file,
)


class EnvironmentBootstrapTests(unittest.TestCase):
    def test_missing_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            target = {}
            path = Path(directory) / "missing.env"
            load_environment_file(path, target)
            self.assertEqual(target, {})

    def test_parses_supported_assignments_without_overriding_process_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "\ufeff"
                "# comment\n"
                "PLAIN=value\n"
                "export SPACED =  around value  \n"
                "SINGLE='one two'\n"
                'DOUBLE="three four"\n'
                "EQUALS=a=b=c\n"
                "KEEP=file-value\n",
                encoding="utf-8",
            )
            target = {"KEEP": "process-value"}
            load_environment_file(path, target)

        self.assertEqual(target["PLAIN"], "value")
        self.assertEqual(target["SPACED"], "around value")
        self.assertEqual(target["SINGLE"], "one two")
        self.assertEqual(target["DOUBLE"], "three four")
        self.assertEqual(target["EQUALS"], "a=b=c")
        self.assertEqual(target["KEEP"], "process-value")

    def test_rejects_malformed_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("NOT_AN_ASSIGNMENT\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid environment assignment"):
                load_environment_file(path, {})

    def test_rejects_invalid_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("BAD-NAME=value\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid environment name"):
                load_environment_file(path, {})

    def test_rejects_unterminated_quote(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('BROKEN="value\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unterminated quoted value"):
                load_environment_file(path, {})

    def test_rejects_directory_as_environment_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "not a regular file"):
                load_environment_file(Path(directory), {})

    def test_custom_environment_file_wins_over_default_location(self):
        target = {"SILLYTAVERN_ENV_FILE": "~/custom.env"}
        self.assertEqual(
            environment_file(target),
            Path("~/custom.env").expanduser(),
        )

    def test_bridge_home_does_not_change_bootstrap_default(self):
        target = {"SILLYTAVERN_BRIDGE_HOME": "/tmp/other-home"}
        self.assertEqual(
            environment_file(target),
            DEFAULT_BRIDGE_HOME / ".env",
        )
```

Do not import `bridge.config` in this test module.

- [ ] **Step 2: Write the failing launcher-order guard**

Extend `tests/test_phase7c_final_runtime_cutover.py`:

```python
def test_entrypoint_bootstraps_environment_before_importing_main(self):
    source = (REPO_ROOT / "sillytavern_telegram_bridge.py").read_text(
        encoding="utf-8"
    )
    bootstrap_import = source.index(
        "from bridge.environment import bootstrap_environment"
    )
    bootstrap_call = source.index("bootstrap_environment()")
    main_import = source.index("from bridge.main import main")

    self.assertLess(bootstrap_import, bootstrap_call)
    self.assertLess(bootstrap_call, main_import)
    self.assertNotIn("def bootstrap_env(", source)
```

Keep the existing direct-`bridge.main` import assertion.

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
python3 -m unittest   tests.test_environment   tests.test_phase7c_final_runtime_cutover.Phase7CFinalRuntimeCutoverTests.test_entrypoint_bootstraps_environment_before_importing_main -v
```

Expected: FAIL because `bridge.environment` does not exist and the launcher still owns `bootstrap_env()`.

- [ ] **Step 4: Implement bridge.environment**

Create `bridge/environment.py`:

```python
"""Process environment bootstrap for the executable entry point."""
from __future__ import annotations

from collections.abc import MutableMapping
import os
from pathlib import Path
import re


DEFAULT_BRIDGE_HOME = (
    Path.home() / ".local/share/sillytavern-telegram"
)
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def environment_file(
    environ: MutableMapping[str, str] | None = None,
) -> Path:
    source = os.environ if environ is None else environ
    return Path(
        source.get(
            "SILLYTAVERN_ENV_FILE",
            str(DEFAULT_BRIDGE_HOME / ".env"),
        )
    ).expanduser()


def load_environment_file(
    path: Path,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    target = os.environ if environ is None else environ
    path = Path(path)

    if not path.exists():
        return
    if not path.is_file():
        raise RuntimeError(
            f"environment path is not a regular file: {path}"
        )

    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise RuntimeError(
                f"invalid environment assignment at "
                f"{path}:{line_number}"
            )

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not _ENVIRONMENT_NAME.fullmatch(key):
            raise RuntimeError(
                f"invalid environment name at "
                f"{path}:{line_number}: {key!r}"
            )

        if value[:1] in {"\"", "'"}:
            if len(value) < 2 or value[-1] != value[0]:
                raise RuntimeError(
                    f"unterminated quoted value at "
                    f"{path}:{line_number}"
                )
            value = value[1:-1]

        target.setdefault(key, value)


def bootstrap_environment(
    environ: MutableMapping[str, str] | None = None,
) -> Path:
    target = os.environ if environ is None else environ
    path = environment_file(target)
    load_environment_file(path, target)
    return path
```

Do not add bridge-module imports.

- [ ] **Step 5: Replace launcher-local parsing**

Replace `sillytavern_telegram_bridge.py` with:

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

Keep the intentional import-after-call ordering.

- [ ] **Step 6: Run focused tests**

Run:

```bash
python3 -m unittest   tests.test_environment   tests.test_phase7c_final_runtime_cutover -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add   bridge/environment.py   sillytavern_telegram_bridge.py   tests/test_environment.py   tests/test_phase7c_final_runtime_cutover.py
git commit -m "refactor: establish strict environment bootstrap"
```

---

### Task 2: Centralize Runtime Paths and Retire the Single-File Prompt Fallback

**Files:**
- Modify: `bridge/config.py`
- Modify: `bridge/common.py`
- Modify: `bridge/catalog.py`
- Modify: `bridge/generation.py`
- Modify: `bridge/telegram.py`
- Modify: `bridge/card_content.py`
- Modify: `tests/test_catalog_limits.py`
- Modify: `tests/test_native_runtime_retirement.py`

**Interfaces:**
- Consumes: `bridge.environment.environment_file()`.
- Produces from `bridge.config`:
  - `LOG_FILE: Path`
  - `PROVIDER_CONFIG_FILE: Path`
  - `MODEL_CACHE_FILE: Path`
  - `CHARACTER_BACKUP_DIR: Path`
  - existing `SYSTEM_PROMPTS_DIR: Path`
- Deletes: `SYSTEM_PROMPTS_FILE`, `ENV_FILE` ownership in `bridge.common`.

- [ ] **Step 1: Add failing path/prompt-retirement guards**

Extend `tests/test_native_runtime_retirement.py`:

```python
def test_startup_path_ownership_has_no_legacy_common_definitions(self):
    common_source = (BRIDGE_DIR / "common.py").read_text(
        encoding="utf-8"
    )
    config_source = (BRIDGE_DIR / "config.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "ENV_FILE =",
        "PROVIDER_CONFIG_FILE =",
        "MODEL_CACHE_FILE =",
        "CHARACTER_BACKUP_DIR =",
        "LOG_FILE =",
    ):
        self.assertNotIn(forbidden, common_source)

    self.assertNotIn("ENV_FILE =", config_source)
    for required in (
        "PROVIDER_CONFIG_FILE =",
        "MODEL_CACHE_FILE =",
        "CHARACTER_BACKUP_DIR =",
        "LOG_FILE =",
    ):
        self.assertIn(required, config_source)

    import bridge.common as common

    for retired_export in (
        "ENV_FILE",
        "PROVIDER_CONFIG_FILE",
        "MODEL_CACHE_FILE",
        "CHARACTER_BACKUP_DIR",
        "LOG_FILE",
    ):
        self.assertFalse(
            hasattr(common, retired_export),
            retired_export,
        )


def test_single_file_system_prompt_fallback_is_deleted(self):
    offenders = {}
    for path in sorted(BRIDGE_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "SYSTEM_PROMPTS_FILE" in source:
            offenders[path.name] = "SYSTEM_PROMPTS_FILE"
    self.assertEqual(offenders, {})
```

- [ ] **Step 2: Update catalog tests to describe directory-only prompts**

In `tests/test_catalog_limits.py`:

- delete setup/teardown storage for `_m_common.SYSTEM_PROMPTS_FILE` and `config.SYSTEM_PROMPTS_FILE`;
- keep `SYSTEM_PROMPTS_DIR` setup;
- add:

```python
def test_system_prompt_catalog_uses_directory_only(self):
    prompt = _m_common.SYSTEM_PROMPTS_DIR / "Only.txt"
    prompt.write_text("directory prompt", encoding="utf-8")

    prompts = _m_cards.load_system_prompts()

    self.assertEqual(
        prompts,
        {
            "Only": {
                "name": "Only",
                "prompt": "directory prompt",
            }
        },
    )
```

This proves the canonical path remains functional after deleting the fallback.

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```bash
python3 -m unittest   tests.test_native_runtime_retirement.NativeRuntimeRetirementTests.test_startup_path_ownership_has_no_legacy_common_definitions   tests.test_native_runtime_retirement.NativeRuntimeRetirementTests.test_single_file_system_prompt_fallback_is_deleted   tests.test_catalog_limits.CatalogLimitTests.test_system_prompt_catalog_uses_directory_only -v
```

Expected: retirement guards FAIL on current path definitions and `SYSTEM_PROMPTS_FILE`.

- [ ] **Step 4: Move runtime path definitions to bridge.config**

Add near `BRIDGE_HOME` in `bridge/config.py`:

```python
LOG_FILE = (
    BRIDGE_HOME
    / "logs"
    / "sillytavern_telegram_bridge.log"
)
PROVIDER_CONFIG_FILE = Path(
    os.environ.get(
        "SILLYTAVERN_PROVIDER_CONFIG",
        str(
            BRIDGE_HOME
            / "sillytavern_telegram_providers.yaml"
        ),
    )
)
MODEL_CACHE_FILE = Path(
    os.environ.get(
        "SILLYTAVERN_MODEL_CACHE",
        str(BRIDGE_HOME / "model_catalog_cache.json"),
    )
)
CHARACTER_BACKUP_DIR = Path(
    os.environ.get(
        "SILLYTAVERN_CHARACTER_BACKUP_DIR",
        str(
            BRIDGE_HOME
            / "backups"
            / "sillytavern"
            / "characters"
        ),
    )
)
```

Delete `SYSTEM_PROMPTS_FILE` from `bridge.config`.

- [ ] **Step 5: Make common consume canonical owners**

In `bridge/common.py`:

- import `environment_file` from `bridge.environment`;
- import moved config paths under private aliases only:

```python
from bridge.config import (
    CHARACTER_BACKUP_DIR as _CHARACTER_BACKUP_DIR,
    LOG_FILE as _LOG_FILE,
    MODEL_CACHE_FILE as _MODEL_CACHE_FILE,
    PROVIDER_CONFIG_FILE as _PROVIDER_CONFIG_FILE,
)
```

- delete local definitions for those paths and `ENV_FILE`;
- remove `SYSTEM_PROMPTS_FILE` import;
- do not expose the moved paths as public attributes of `bridge.common`.

Change the permissions file set from:

```python
private_files = {
    ENV_FILE,
    DB_FILE,
    LOG_FILE,
    PROVIDER_CONFIG_FILE,
    MODEL_CACHE_FILE,
}
```

to:

```python
private_files = {
    environment_file(),
    DB_FILE,
    _LOG_FILE,
    _PROVIDER_CONFIG_FILE,
    _MODEL_CACHE_FILE,
}
```

Do not introduce an `ENV_FILE` alias.

- [ ] **Step 6: Move direct provider/cache consumers to bridge.config**

In `bridge/catalog.py`, replace:

```python
from bridge.common import (
    MODEL_CACHE_FILE,
    MODEL_CHOICES,
    MODEL_REFRESH_SECONDS,
    PROVIDER_CONFIG_FILE,
)
from bridge.config import WORLD_DIR
```

with:

```python
from bridge.common import (
    MODEL_CHOICES,
    MODEL_REFRESH_SECONDS,
)
from bridge.config import (
    MODEL_CACHE_FILE,
    PROVIDER_CONFIG_FILE,
    WORLD_DIR,
)
```

In `bridge/generation.py`, remove `PROVIDER_CONFIG_FILE` from the `bridge.common` import and import it from `bridge.config`.

In `bridge/telegram.py`, remove `CHARACTER_BACKUP_DIR` from the `bridge.common` import and import it from `bridge.config`.

Do not re-export moved paths from `bridge.common`.

- [ ] **Step 7: Delete single-file prompt fallback**

In `bridge/card_content.py`, replace:

```python
def load_system_prompts() -> dict[str, dict[str, str]]:
    result = {}
    if _config.SYSTEM_PROMPTS_FILE:
        _merge_system_prompt_file(
            result,
            Path(_config.SYSTEM_PROMPTS_FILE),
        )
    if _config.SYSTEM_PROMPTS_DIR.exists():
        ...
```

with:

```python
def load_system_prompts() -> dict[str, dict[str, str]]:
    result = {}
    if _config.SYSTEM_PROMPTS_DIR.exists():
        for path in sorted(
            list(_config.SYSTEM_PROMPTS_DIR.glob("*.json"))
            + list(_config.SYSTEM_PROMPTS_DIR.glob("*.txt"))
        ):
            _merge_system_prompt_file(result, path)
    return dict(
        list(result.items())[:_config.CATALOG_MAX_ITEMS]
    )
```

Retain JSON/text parsing behavior and catalog limit unchanged.

- [ ] **Step 8: Run focused path/catalog tests**

Run:

```bash
python3 -m unittest   tests.test_native_runtime_retirement   tests.test_catalog_limits   tests.test_catalog_panel -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add   bridge/config.py   bridge/common.py   bridge/catalog.py   bridge/generation.py   bridge/card_content.py   tests/test_catalog_limits.py   tests/test_native_runtime_retirement.py
git commit -m "refactor: centralize startup paths and prompts"
```

---

### Task 3: Make Logging Explicit and Keep bridge.common Import-Pure

**Files:**
- Create: `tests/test_startup_purity.py`
- Modify: `bridge/common.py`
- Modify: `bridge/main.py`
- Modify: `tests/test_composition.py`

**Interfaces:**
- Consumes: `bridge.config.LOG_FILE`.
- Produces:
  - `configure_logging(log_file: Path = LOG_FILE) -> None`.
- Import invariant: loading `bridge.common` does not create filesystem logging resources or install a rotating handler.

- [ ] **Step 1: Add isolated failing import-purity test**

Create `tests/test_startup_purity.py`:

```python
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).parents[1]


class StartupPurityTests(unittest.TestCase):
    def _run(self, source: str):
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_importing_common_does_not_create_log_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            source = f"""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path

root = Path({directory!r}) / "bridge-home"
os.environ["SILLYTAVERN_BRIDGE_HOME"] = str(root)

import bridge.common as common

assert not (root / "logs").exists()
assert not any(
    isinstance(handler, RotatingFileHandler)
    for handler in logging.getLogger().handlers
)
assert common._GENERATION_EXECUTOR is None
assert common._UTILITY_EXECUTOR is None
"""
            completed = self._run(source)

        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
```

This test intentionally combines log and executor purity; Task 4 makes the executor assertions GREEN.

- [ ] **Step 2: Add failing logging lifecycle tests**

Add to `tests/test_startup_purity.py`:

```python
def test_configure_logging_is_idempotent_for_same_target(self):
    with tempfile.TemporaryDirectory() as directory:
        log_file = Path(directory) / "logs" / "bridge.log"
        source = f"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import stat

from bridge.common import configure_logging

target = Path({str(log_file)!r})
configure_logging(target)
configure_logging(target)

matching = [
    handler
    for handler in logging.getLogger().handlers
    if isinstance(handler, RotatingFileHandler)
    and Path(handler.baseFilename).resolve()
       == target.resolve()
]
assert len(matching) == 1
assert target.exists()

import os

if os.name == "posix":
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode & 0o077 == 0
"""
        completed = self._run(source)

    self.assertEqual(
        completed.returncode,
        0,
        completed.stdout + completed.stderr,
    )
```

Also add:

```python
def test_configure_logging_does_not_create_executors(self):
    with tempfile.TemporaryDirectory() as directory:
        log_file = Path(directory) / "bridge.log"
        source = f"""
from pathlib import Path
import bridge.common as common

common.configure_logging(Path({str(log_file)!r}))
assert common._GENERATION_EXECUTOR is None
assert common._UTILITY_EXECUTOR is None
"""
        completed = self._run(source)

    self.assertEqual(
        completed.returncode,
        0,
        completed.stdout + completed.stderr,
    )
```

- [ ] **Step 3: Run logging/purity tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_startup_purity -v
```

Expected: FAIL because importing `bridge.common` currently creates the log directory/file and `configure_logging()` does not exist.

- [ ] **Step 4: Implement explicit configure_logging**

In `bridge/common.py`, delete import-time:

```python
LOG_FILE.parent.mkdir(...)
logging.basicConfig(...)
```

Add:

```python
def configure_logging(
    log_file: Path = _LOG_FILE,
) -> None:
    """Install the bridge rotating file handler explicitly."""
    target = Path(log_file).expanduser().resolve()
    root = logging.getLogger()

    if any(
        isinstance(handler, RotatingFileHandler)
        and Path(handler.baseFilename).resolve() == target
        for handler in root.handlers
    ):
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(target),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s"
        )
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    try:
        target.chmod(0o600)
    except OSError:
        logging.warning(
            "Could not protect runtime log file %s",
            target,
            exc_info=True,
        )
```

Do not use `logging.basicConfig()`.

- [ ] **Step 5: Wire logging into startup**

In `bridge/main.py`, import `configure_logging` from `bridge.common`.

In `main()`, call it during startup after `enforce_runtime_permissions()`:

```python
refresh_phase3_config()
enforce_runtime_permissions()
configure_logging()

config = _load_startup_config(os.environ)
```

Task 5 will remove `load_env_file()`; do not move unrelated startup code.

- [ ] **Step 6: Update startup tests to assert explicit logging**

In the existing `tests/test_composition.py` main-startup tests that patch `enforce_runtime_permissions`, also patch:

```python
patch.object(_m_main, "configure_logging")
```

and assert `configure_logging.assert_called_once_with()` in one representative startup test.

Do not make the tests depend on a real file handler.

- [ ] **Step 7: Run focused tests**

Run:

```bash
python3 -m unittest   tests.test_startup_purity   tests.test_composition -v
```

At this point the import-purity test may still fail only because Task 4 has not yet made executors lazy. Logging assertions must be GREEN; record that remaining executor failure as expected and proceed immediately to Task 4.

- [ ] **Step 8: Commit**

```bash
git add   bridge/common.py   bridge/main.py   tests/test_startup_purity.py   tests/test_composition.py
git commit -m "refactor: configure logging explicitly at startup"
```

---

### Task 4: Create Background Executors Lazily

**Files:**
- Modify: `bridge/common.py`
- Modify: `tests/test_background_lifecycle.py`
- Test: `tests/test_startup_purity.py`

**Interfaces:**
- Produces:
  - `_GENERATION_EXECUTOR: ThreadPoolExecutor | None`
  - `_UTILITY_EXECUTOR: ThreadPoolExecutor | None`
  - `_EXECUTOR_LOCK: threading.Lock`
  - `_executor_for(label: str) -> ThreadPoolExecutor`
- Preserves existing `submit_background`, `submit_chat_background`, drain, queue, and shutdown APIs.

- [ ] **Step 1: Add failing lazy-creation tests**

Extend `tests/test_background_lifecycle.py`:

```python
from unittest.mock import Mock, patch


def test_executor_for_creates_only_requested_pool_and_reuses_it(self):
    old_generation = _m_common._GENERATION_EXECUTOR
    old_utility = _m_common._UTILITY_EXECUTOR
    _m_common._GENERATION_EXECUTOR = None
    _m_common._UTILITY_EXECUTOR = None
    try:
        generation = Mock()
        utility = Mock()
        with patch.object(
            _m_common.concurrent.futures,
            "ThreadPoolExecutor",
            side_effect=[generation, utility],
        ) as constructor:
            first = _m_common._executor_for("generation")
            second = _m_common._executor_for("retry")
            third = _m_common._executor_for("tts")

        self.assertIs(first, generation)
        self.assertIs(second, generation)
        self.assertIs(third, utility)
        self.assertEqual(constructor.call_count, 2)
    finally:
        _m_common._GENERATION_EXECUTOR = old_generation
        _m_common._UTILITY_EXECUTOR = old_utility


def test_shutdown_is_safe_before_executor_creation_and_after_reset(self):
    old_generation = _m_common._GENERATION_EXECUTOR
    old_utility = _m_common._UTILITY_EXECUTOR
    old_accepting = _m_common._BACKGROUND_ACCEPTING
    try:
        _m_common._GENERATION_EXECUTOR = None
        _m_common._UTILITY_EXECUTOR = None
        _m_common._BACKGROUND_ACCEPTING = True

        self.assertTrue(
            _m_common.shutdown_background_executors(
                timeout=0.0
            )
        )
        self.assertIsNone(_m_common._GENERATION_EXECUTOR)
        self.assertIsNone(_m_common._UTILITY_EXECUTOR)

        self.assertTrue(
            _m_common.shutdown_background_executors(
                timeout=0.0
            )
        )
    finally:
        _m_common._GENERATION_EXECUTOR = old_generation
        _m_common._UTILITY_EXECUTOR = old_utility
        _m_common._BACKGROUND_ACCEPTING = old_accepting
```

- [ ] **Step 2: Run focused executor tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_background_lifecycle -v
```

Expected: FAIL because executors are currently constructed at import and shutdown assumes both objects exist.

- [ ] **Step 3: Make executor globals lazy**

In `bridge/common.py`, replace:

```python
_GENERATION_EXECUTOR = ThreadPoolExecutor(...)
_UTILITY_EXECUTOR = ThreadPoolExecutor(...)
```

with:

```python
_GENERATION_EXECUTOR: (
    concurrent.futures.ThreadPoolExecutor | None
) = None
_UTILITY_EXECUTOR: (
    concurrent.futures.ThreadPoolExecutor | None
) = None
_EXECUTOR_LOCK = threading.Lock()
```

- [ ] **Step 4: Implement locked first-use creation**

Replace `_executor_for()` with:

```python
def _executor_for(
    label: str,
) -> concurrent.futures.ThreadPoolExecutor:
    global _GENERATION_EXECUTOR, _UTILITY_EXECUTOR

    with _EXECUTOR_LOCK:
        if label in _GENERATION_LABELS:
            if _GENERATION_EXECUTOR is None:
                _GENERATION_EXECUTOR = (
                    concurrent.futures.ThreadPoolExecutor(
                        max_workers=3,
                        thread_name_prefix="st-generation",
                    )
                )
            return _GENERATION_EXECUTOR

        if _UTILITY_EXECUTOR is None:
            _UTILITY_EXECUTOR = (
                concurrent.futures.ThreadPoolExecutor(
                    max_workers=2,
                    thread_name_prefix="st-utility",
                )
            )
        return _UTILITY_EXECUTOR
```

Do not alter admission slots or label classification.

- [ ] **Step 5: Make shutdown tolerate absent pools and clear references**

Implement:

```python
def shutdown_background_executors(
    timeout: float = 20.0,
) -> bool:
    global _GENERATION_EXECUTOR, _UTILITY_EXECUTOR

    begin_background_shutdown()
    drained = drain_background_jobs(timeout)

    with _EXECUTOR_LOCK:
        generation = _GENERATION_EXECUTOR
        utility = _UTILITY_EXECUTOR
        _GENERATION_EXECUTOR = None
        _UTILITY_EXECUTOR = None

    if generation is not None:
        generation.shutdown(
            wait=drained,
            cancel_futures=not drained,
        )
    if utility is not None:
        utility.shutdown(
            wait=drained,
            cancel_futures=not drained,
        )
    return drained
```

- [ ] **Step 6: Run executor and import-purity tests**

Run:

```bash
python3 -m unittest   tests.test_background_lifecycle   tests.test_startup_purity -v
```

Expected: PASS.

- [ ] **Step 7: Run background/job regressions**

Run:

```bash
python3 -m unittest   tests.test_background_lifecycle   tests.test_job_service   tests.test_job_service_workers -v
```

Expected: PASS; no durable-job behavior changes.

- [ ] **Step 8: Commit**

```bash
git add   bridge/common.py   tests/test_background_lifecycle.py   tests/test_startup_purity.py
git commit -m "refactor: create background executors lazily"
```

---

### Task 5: Enforce Native Startup Validation and Remove Late Environment Loading

**Files:**
- Modify: `bridge/composition.py`
- Modify: `bridge/common.py`
- Modify: `bridge/main.py`
- Modify: `tests/test_composition.py`
- Modify: `tests/test_native_runtime_retirement.py`

**Interfaces:**
- Consumes: pre-import environment bootstrap from Task 1.
- Produces: stricter `validate_bridge_config(config: BridgeConfig) -> None`.
- Deletes: `bridge.common.load_env_file()` and all `bridge.main` use of it.

- [ ] **Step 1: Add failing allowlist validation tests**

Extend `CompositionConfigTests` in `tests/test_composition.py`:

```python
def test_validate_bridge_config_rejects_empty_allowlist(self):
    environ = self._environ()
    environ["SILLYTAVERN_TELEGRAM_ALLOWED_USERS"] = ""
    config = load_bridge_config(
        environ,
        character_dir=self.character_dir,
        db_file=self.db_file,
    )

    with self.assertRaisesRegex(
        ValueError,
        "SILLYTAVERN_TELEGRAM_ALLOWED_USERS",
    ):
        validate_bridge_config(config)


def test_validate_bridge_config_rejects_non_numeric_allowlist_member(self):
    environ = self._environ()
    environ[
        "SILLYTAVERN_TELEGRAM_ALLOWED_USERS"
    ] = "100, invalid-user, 200,100"
    config = load_bridge_config(
        environ,
        character_dir=self.character_dir,
        db_file=self.db_file,
    )

    with self.assertRaisesRegex(
        ValueError,
        "invalid-user",
    ):
        validate_bridge_config(config)


def test_validate_bridge_config_accepts_trimmed_duplicate_numeric_ids(self):
    environ = self._environ()
    environ[
        "SILLYTAVERN_TELEGRAM_ALLOWED_USERS"
    ] = " 100,200,100 ,, "
    config = load_bridge_config(
        environ,
        character_dir=self.character_dir,
        db_file=self.db_file,
    )

    self.assertEqual(
        config.allowed_users,
        frozenset({"100", "200"}),
    )
    self.assertIsNone(validate_bridge_config(config))
```

- [ ] **Step 2: Add failing late-loader retirement guard**

Extend `tests/test_native_runtime_retirement.py`:

```python
def test_late_environment_loader_is_deleted(self):
    common_source = (BRIDGE_DIR / "common.py").read_text(
        encoding="utf-8"
    )
    main_source = (BRIDGE_DIR / "main.py").read_text(
        encoding="utf-8"
    )
    self.assertNotIn("def load_env_file(", common_source)
    self.assertNotIn("load_env_file", main_source)
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
python3 -m unittest   tests.test_composition.CompositionConfigTests   tests.test_native_runtime_retirement.NativeRuntimeRetirementTests.test_late_environment_loader_is_deleted -v
```

Expected: FAIL because empty/non-numeric allowlists are accepted and `load_env_file()` remains.

- [ ] **Step 4: Tighten validate_bridge_config**

In `bridge/composition.py`, after the existing card checks:

```python
if not config.allowed_users:
    raise ValueError(
        "required SILLYTAVERN_TELEGRAM_ALLOWED_USERS "
        "is missing from .env"
    )

invalid_users = sorted(
    user_id
    for user_id in config.allowed_users
    if not user_id.isdecimal()
)
if invalid_users:
    raise ValueError(
        "SILLYTAVERN_TELEGRAM_ALLOWED_USERS must contain "
        "only numeric Telegram user IDs: "
        + ", ".join(invalid_users)
    )
```

Do not normalize invalid tokens away.

- [ ] **Step 5: Delete load_env_file**

Delete `load_env_file()` from `bridge/common.py`.

Remove it from the `bridge.main` import list and delete:

```python
load_env_file()
```

from `main()`.

The startup prefix becomes:

```python
_initialize_extensions()

parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
args = parser.parse_args()

refresh_phase3_config()
enforce_runtime_permissions()
configure_logging()

config = _load_startup_config(os.environ)
```

Do not move environment loading into `main()`.

- [ ] **Step 6: Update main-startup tests**

In `tests/test_composition.py`:

- remove every `patch.object(_m_main, "load_env_file", ...)`;
- update `test_run_check_uses_prebuilt_services_without_reloading_environment` so it verifies `run_check()` directly from the supplied services without referencing a deleted symbol;
- retain/extend the `configure_logging`, `refresh_phase3_config`, and `enforce_runtime_permissions` patches from Task 3.

Where a startup-order test records calls, assert the relative order:

```python
self.assertLess(
    calls.index("enforce_permissions"),
    calls.index("configure_logging"),
)
self.assertLess(
    calls.index("configure_logging"),
    calls.index("build_services"),
)
```

if those events are already instrumented by the test. Do not add a production ordering API solely for testing.

- [ ] **Step 7: Run focused startup/config tests**

Run:

```bash
python3 -m unittest   tests.test_composition   tests.test_native_runtime_retirement   tests.test_environment   tests.test_startup_purity -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add   bridge/composition.py   bridge/common.py   bridge/main.py   tests/test_composition.py   tests/test_native_runtime_retirement.py
git commit -m "refactor: enforce explicit startup configuration"
```

---

### Task 6: Close Startup-Purity Residue, Documentation, and Exact-Head Validation

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `tests/test_native_runtime_retirement.py`
- Modify: `tests/test_startup_purity.py` only if final invariant consolidation is needed.
- No new production architecture is introduced in this task.

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: permanent architecture guards and exact-head validation evidence.

- [ ] **Step 1: Add final source-level startup purity guards**

Extend `tests/test_native_runtime_retirement.py` with:

```python
def test_common_has_no_import_time_process_resource_construction(self):
    source = (BRIDGE_DIR / "common.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "logging.basicConfig(",
        "LOG_FILE.parent.mkdir(",
        "_GENERATION_EXECUTOR = "
        "concurrent.futures.ThreadPoolExecutor(",
        "_UTILITY_EXECUTOR = "
        "concurrent.futures.ThreadPoolExecutor(",
    ):
        with self.subTest(forbidden=forbidden):
            self.assertNotIn(forbidden, source)


def test_launcher_has_no_local_environment_parser(self):
    source = (
        REPO_ROOT / "sillytavern_telegram_bridge.py"
    ).read_text(encoding="utf-8")
    self.assertNotIn("def bootstrap_env(", source)
    self.assertIn(
        "from bridge.environment "
        "import bootstrap_environment",
        source,
    )
```

Keep behavioral subprocess tests as the primary evidence; these source guards prevent known retired patterns from returning.

- [ ] **Step 2: Remove the legacy prompt variable from .env.example**

Delete:

```dotenv
# Optional legacy single system-prompt file fallback.
# SILLYTAVERN_SYSTEM_PROMPTS_FILE=/path/to/system-prompt.txt
```

Keep `SILLYTAVERN_SYSTEM_PROMPTS_DIR`.

Update the allowlist comment to make the requirement explicit:

```dotenv
# Required comma-separated numeric Telegram user IDs allowed to use the bot.
SILLYTAVERN_TELEGRAM_ALLOWED_USERS=
```

- [ ] **Step 3: Update README only for changed startup contracts**

Update setup/architecture text to state:

- the executable loads the environment file before importing application modules;
- the environment file is strict `KEY=VALUE`, existing process values win, and missing file is allowed;
- `SILLYTAVERN_TELEGRAM_ALLOWED_USERS` is required and must contain numeric IDs;
- system prompts are discovered from `SILLYTAVERN_SYSTEM_PROMPTS_DIR`;
- logging and background executors are created during runtime startup/use rather than module import.

Do not document PR #65–#68 behavior yet.

- [ ] **Step 4: Run final compatibility-residue scans**

Run:

```bash
grep -R   -e "def load_env_file("   -e "SYSTEM_PROMPTS_FILE"   -e "def bootstrap_env("   bridge tests sillytavern_telegram_bridge.py .env.example README.md
```

Expected: no production/config/documentation hits. Test guard string literals are allowed.

Run:

```bash
grep -R   -e "logging.basicConfig("   -e "LOG_FILE.parent.mkdir("   bridge
```

Expected: no hits.

Run:

```bash
grep -R   -e "PROVIDER_CONFIG_FILE ="   -e "MODEL_CACHE_FILE ="   -e "CHARACTER_BACKUP_DIR ="   -e "LOG_FILE ="   bridge
```

Expected: definitions occur only in `bridge/config.py`.

- [ ] **Step 5: Run startup-focused suites**

Run:

```bash
python3 -m unittest   tests.test_environment   tests.test_startup_purity   tests.test_background_lifecycle   tests.test_composition   tests.test_catalog_limits   tests.test_catalog_panel   tests.test_native_runtime_retirement   tests.test_phase7c_final_runtime_cutover -v
```

Expected: PASS.

- [ ] **Step 6: Run durable/background regression suites**

Run:

```bash
python3 -m unittest   tests.test_job_service   tests.test_job_service_workers   tests.test_operation_recovery -v
```

Expected: PASS.

- [ ] **Step 7: Run the full unittest suite**

Run:

```bash
python3 -m unittest discover -s tests -q
```

Expected: exit 0. Record exact test/subtest counts for the PR body.

- [ ] **Step 8: Run the full pytest suite**

Run:

```bash
python3 -m pytest -q
```

Expected: exit 0. Record exact pass/subtest counts.

- [ ] **Step 9: Compile production and tests**

Run:

```bash
python3 -m compileall -q bridge tests
```

Expected: exit 0.

- [ ] **Step 10: Validate installed dependency consistency**

Run:

```bash
python3 -m pip check
```

Expected:

```text
No broken requirements found.
```

- [ ] **Step 11: Run the repository dependency audit**

Run:

```bash
python3 -m pip_audit
```

If `.github/workflows/ci.yml` uses a different exact audit command at execution time, use the workflow command as authoritative.

Expected: no known vulnerabilities under the repository's accepted audit policy.

- [ ] **Step 12: Review the diff against the approved spec**

Run:

```bash
git diff --stat main...HEAD
git diff main...HEAD --   sillytavern_telegram_bridge.py   bridge   tests   .env.example   README.md
```

Verify:

- no schema or migration changes;
- no RAG behavior changes;
- no `phase3_*` rename;
- no `runtime_context` removal;
- no `bridge.main` decomposition;
- no service-composition changes;
- no compatibility alias for retired environment/system-prompt paths;
- no import-time log/executor construction remains.

- [ ] **Step 13: Commit final guards/docs if changed**

If Task 6 changed tracked files:

```bash
git add   .env.example   README.md   tests/test_native_runtime_retirement.py   tests/test_startup_purity.py
git commit -m "test: guard startup purity invariants"
```

Do not create an empty commit.

- [ ] **Step 14: Prepare PR #64**

Before PR creation:

```bash
git status --short
git log --oneline main..HEAD
```

Expected:

- clean working tree;
- only PR #64 commits.

Create a PR from:

```text
punzer4-code:refactor/pr64-startup-purity-environment
```

to:

```text
cepeter/SillyTavern-Telegram-Bridge:main
```

The PR body must report:

- strict `bridge.environment` bootstrap;
- canonical runtime-path ownership;
- deleted late environment loader;
- directory-only system prompts;
- explicit logging lifecycle;
- lazy executor lifecycle;
- required numeric Telegram allowlist;
- exact head SHA;
- exact validation counts;
- exact-head CI run;
- changed-file/addition/deletion counts;
- confirmation that PR #65–#68 work is out of scope;
- `Ready to merge. Do not merge automatically.`

Do not merge automatically.

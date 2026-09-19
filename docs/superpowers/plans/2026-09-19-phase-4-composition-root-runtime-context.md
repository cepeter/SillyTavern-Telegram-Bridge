# Phase 4 Composition Root and Runtime Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construct startup configuration and root infrastructure dependencies once, then pass one explicit immutable runtime context through top-level workers, durable recovery, backlog dispatch, check mode, and polling without removing the compatibility runtime.

**Architecture:** Add an ordinary-import `bridge/composition.py` with immutable `BridgeConfig`, `TelegramRuntime`, `BackgroundRuntime`, and `BridgeServices`. Keep `main.py` as the transitional composition root, make the final scheduler-safe `db_connect` path-aware, and migrate only root worker/lifecycle boundaries; Phase 5 application-service extraction remains out of scope.

**Tech Stack:** Python 3.11, stdlib `dataclasses`/`pathlib`/`typing`/`sqlite3`, existing compatibility runtime loader, unittest/pytest, SQLite WAL + serialized write lock.

**Spec:** `docs/superpowers/specs/2026-09-19-composition-root-runtime-context-design.md`

## Global Constraints

- `bridge/composition.py` is an ordinary import and must not be added to `DEFAULT_RUNTIME_STAGES`.
- Do not remove or redesign `runtime_loader.py` or its production `exec()` flow in Phase 4.
- Do not introduce a global current-services registry, getter, setter, IoC container, or DI framework.
- Do not introduce `GroupDirectorService`, `MemoryService`, `PersonaService`, `SyncService`, or `JobService`.
- Do not thread `BridgeServices` through every feature function; only startup, worker, recovery, backlog, check, and root lifecycle boundaries migrate.
- `BridgeConfig`, `TelegramRuntime`, `BackgroundRuntime`, and `BridgeServices` are frozen dataclasses.
- `bot_token` and `api_key` use `repr=False`.
- `load_bridge_config()` reads only the supplied mapping and never mutates it or `os.environ`.
- Migrated workers obtain DB connections through `services.db_factory`.
- Migrated workers obtain token/API key/default model through `services.config`; a recovered job may supply an explicit per-job `model_override`.
- Live and recovered durable jobs receive the exact same `BridgeServices` instance.
- `submit_durable_chat_job()` must use injected `BackgroundRuntime.submit_chat`; it must not discover `submit_chat_background` globally.
- No new public-callable runtime override or `_ORIGINAL_*` chain is introduced.
- Preserve Phase 3 transaction ownership and all durable-job/recovery semantics.
- Preserve existing SQLite serialized connection, WAL, busy-timeout, schema migrations, and scheduler contention behavior.
- Do not mass-remove static-analyzer "unused" compatibility parameters or perform broad docstring/dead-code/duplicate-string cleanup.

## Review Focus

- **Explicit DB path while the default DB has already been initialized:** the injected factory must initialize the new path independently rather than reusing default-path readiness state.
- **Recovered job with a stored model different from current default:** the worker must use the stored per-job model override while still using the same `BridgeServices` object.
- **Worker failure with injected Telegram transport:** failure delivery must use `services.telegram.send_text`, not a global `send_text`.
- **Background shutdown during durable submission:** injected `submit_chat` returning false must leave the durable job recoverable and must not mark it scheduled.
- **Signal handling before/after service construction:** installed handlers must capture the composed `begin_shutdown` callable and must not depend on a global services registry.

---

## File Structure

### Create: `bridge/composition.py`

Own only:

- `BridgeConfig`
- `TelegramRuntime`
- `BackgroundRuntime`
- `BridgeServices`
- `load_bridge_config(...)`
- `validate_bridge_config(...)`
- `build_bridge_services(...)`

No compatibility-runtime discovery and no domain workflow functions.

### Modify: `bridge/database.py`

Extend the baseline database connector to:

```python
db_connect(database_path: Path | None = None) -> sqlite3.Connection
```

using the explicit path when supplied.

### Modify: `bridge/scheduler_safety.py`

Preserve the existing late-loaded scheduler-safety override while making schema readiness path-aware and preserving `_DB_SCHEMA_READY` compatibility for the current default path.

### Modify: `bridge/main.py`

Own the transitional composition root, root lifecycle adapters, message/image/callback/edit worker migration, explicit durable submission/recovery, backlog closure, `run_check(services)`, and the poll loop's root infrastructure access.

### Modify: `bridge/media.py`

Migrate `process_voice_job` to `BridgeServices`.

### Modify: `bridge/help.py`

Migrate `process_document_job` to `BridgeServices`.

### Create: `tests/test_composition.py`

Own pure composition/config tests plus injected runtime-context behavior.

### Modify: `tests/test_sqlite_contention.py`

Add explicit multi-path database initialization coverage without removing existing contention tests.

### Modify: `tests/test_background_lifecycle.py`

Add injected background-runtime and shutdown-handler coverage.

### Modify: `tests/test_runtime_loader.py`

Assert `composition.py` remains outside runtime stages and current override expectations remain unchanged.

---

### Task 1: Add immutable composition/configuration types

**Files:**
- Create: `bridge/composition.py`
- Create: `tests/test_composition.py`

**Interfaces:**
- Produces:
  - `BridgeConfig`
  - `TelegramRuntime`
  - `BackgroundRuntime`
  - `BridgeServices`
  - `load_bridge_config(environ, *, character_dir, db_file) -> BridgeConfig`
  - `validate_bridge_config(config) -> None`
  - `build_bridge_services(config, *, db_factory, telegram, background) -> BridgeServices`

- [ ] **Step 1: Write RED tests for config parsing, immutability, secret repr, validation, and builder identity**

Create `tests/test_composition.py`:

```python
from dataclasses import FrozenInstanceError
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bridge.composition import (
    BackgroundRuntime,
    BridgeConfig,
    BridgeServices,
    TelegramRuntime,
    build_bridge_services,
    load_bridge_config,
    validate_bridge_config,
)


class CompositionConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.character_dir = self.root / "characters"
        self.character_dir.mkdir()
        self.card = self.character_dir / "mira.png"
        self.card.write_bytes(b"card")
        self.db_file = self.root / "bridge.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def _environ(self):
        return {
            "SILLYTAVERN_TELEGRAM_BOT_TOKEN": "secret-token",
            "LLM_API_KEY": "secret-api-key",
            "SILLYTAVERN_MODEL": "provider::model",
            "SILLYTAVERN_DEFAULT_CHARACTER": "mira.png",
            "SILLYTAVERN_TELEGRAM_ALLOWED_USERS": " 100,200, 100 ,,",
        }

    def test_load_bridge_config_is_pure_and_parses_once(self):
        environ = self._environ()
        before = dict(environ)

        config = load_bridge_config(
            environ,
            character_dir=self.character_dir,
            db_file=self.db_file,
        )

        self.assertEqual(environ, before)
        self.assertEqual(config.bot_token, "secret-token")
        self.assertEqual(config.api_key, "secret-api-key")
        self.assertEqual(config.default_model, "provider::model")
        self.assertEqual(config.default_character_file, "mira.png")
        self.assertEqual(config.card_file, self.card)
        self.assertEqual(config.db_file, self.db_file)
        self.assertEqual(config.allowed_users, frozenset({"100", "200"}))

    def test_config_and_services_are_immutable_and_hide_credentials(self):
        config = load_bridge_config(
            self._environ(),
            character_dir=self.character_dir,
            db_file=self.db_file,
        )
        rendered = repr(config)
        self.assertNotIn("secret-token", rendered)
        self.assertNotIn("secret-api-key", rendered)

        with self.assertRaises(FrozenInstanceError):
            config.default_model = "other"

        telegram = TelegramRuntime(
            request=lambda *_args, **_kwargs: {},
            send_text=lambda *_args, **_kwargs: None,
        )
        background = BackgroundRuntime(
            submit_chat=lambda *_args, **_kwargs: True,
            register_backlog_dispatcher=lambda _callback: None,
            begin_shutdown=lambda: None,
        )
        services = build_bridge_services(
            config,
            db_factory=lambda: sqlite3.connect(":memory:"),
            telegram=telegram,
            background=background,
        )

        self.assertIs(services.config, config)
        self.assertIs(services.telegram, telegram)
        self.assertIs(services.background, background)
        with self.assertRaises(FrozenInstanceError):
            services.telegram = telegram

    def test_validate_bridge_config_rejects_missing_required_values(self):
        base = self._environ()
        cases = (
            ("SILLYTAVERN_TELEGRAM_BOT_TOKEN", "Telegram bot token"),
            ("SILLYTAVERN_MODEL", "SILLYTAVERN_MODEL"),
            ("SILLYTAVERN_DEFAULT_CHARACTER", "SILLYTAVERN_DEFAULT_CHARACTER"),
        )
        for key, message in cases:
            with self.subTest(key=key):
                environ = dict(base)
                environ[key] = ""
                config = load_bridge_config(
                    environ,
                    character_dir=self.character_dir,
                    db_file=self.db_file,
                )
                with self.assertRaisesRegex(ValueError, message):
                    validate_bridge_config(config)

    def test_validate_bridge_config_rejects_missing_card(self):
        environ = self._environ()
        environ["SILLYTAVERN_DEFAULT_CHARACTER"] = "missing.png"
        config = load_bridge_config(
            environ,
            character_dir=self.character_dir,
            db_file=self.db_file,
        )
        with self.assertRaisesRegex(ValueError, "does not exist"):
            validate_bridge_config(config)

    def test_validate_bridge_config_accepts_valid_config(self):
        config = load_bridge_config(
            self._environ(),
            character_dir=self.character_dir,
            db_file=self.db_file,
        )
        self.assertIsNone(validate_bridge_config(config))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python -m unittest tests.test_composition.CompositionConfigTests -v
```

Expected: import error because `bridge.composition` does not exist.

- [ ] **Step 3: Implement `bridge/composition.py` minimally**

Create:

```python
"""Explicit startup configuration and root infrastructure composition."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class BridgeConfig:
    bot_token: str = field(repr=False)
    api_key: str = field(repr=False)
    default_model: str
    default_character_file: str
    card_file: Path
    db_file: Path
    allowed_users: frozenset[str]


@dataclass(frozen=True)
class TelegramRuntime:
    request: Callable[..., object]
    send_text: Callable[..., object]


@dataclass(frozen=True)
class BackgroundRuntime:
    submit_chat: Callable[..., bool]
    register_backlog_dispatcher: Callable[[Callable[[], None]], None]
    begin_shutdown: Callable[[], None]


@dataclass(frozen=True)
class BridgeServices:
    config: BridgeConfig
    db_factory: Callable[[], sqlite3.Connection]
    telegram: TelegramRuntime
    background: BackgroundRuntime


def load_bridge_config(
    environ: Mapping[str, str],
    *,
    character_dir: Path,
    db_file: Path,
) -> BridgeConfig:
    default_character_file = str(
        environ.get("SILLYTAVERN_DEFAULT_CHARACTER", "")
    ).strip()
    allowed = frozenset(
        value.strip()
        for value in str(
            environ.get("SILLYTAVERN_TELEGRAM_ALLOWED_USERS", "")
        ).split(",")
        if value.strip()
    )
    character_dir = Path(character_dir)
    return BridgeConfig(
        bot_token=str(
            environ.get("SILLYTAVERN_TELEGRAM_BOT_TOKEN", "")
        ).strip(),
        api_key=str(environ.get("LLM_API_KEY", "")).strip(),
        default_model=str(environ.get("SILLYTAVERN_MODEL", "")).strip(),
        default_character_file=default_character_file,
        card_file=character_dir / default_character_file,
        db_file=Path(db_file),
        allowed_users=allowed,
    )


def validate_bridge_config(config: BridgeConfig) -> None:
    if not config.bot_token:
        raise ValueError("required Telegram bot token is missing from .env")
    if not config.default_model:
        raise ValueError("required SILLYTAVERN_MODEL is missing from .env")
    if not config.default_character_file:
        raise ValueError(
            "required SILLYTAVERN_DEFAULT_CHARACTER is missing from .env"
        )
    if not config.card_file.is_file():
        raise ValueError(
            "configured default character card does not exist: "
            f"{config.card_file}"
        )


def build_bridge_services(
    config: BridgeConfig,
    *,
    db_factory: Callable[[], sqlite3.Connection],
    telegram: TelegramRuntime,
    background: BackgroundRuntime,
) -> BridgeServices:
    return BridgeServices(
        config=config,
        db_factory=db_factory,
        telegram=telegram,
        background=background,
    )
```

Do not import `bridge.runtime`, `bridge.main`, or any compatibility-runtime stage.

- [ ] **Step 4: Run Task 1 tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_composition.CompositionConfigTests -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add bridge/composition.py tests/test_composition.py
git commit -m "feat: add explicit bridge composition types"
```

---

### Task 2: Make the database factory path-aware without weakening scheduler safety

**Files:**
- Modify: `bridge/database.py:175-188`
- Modify: `bridge/scheduler_safety.py:5-36`
- Modify: `tests/test_sqlite_contention.py`
- Modify: `tests/test_composition.py`

**Interfaces:**
- Consumes: Task 1 `BridgeServices.db_factory` callable shape.
- Produces:
  - compatibility `db_connect(database_path: Path | None = None) -> sqlite3.Connection`
  - path-aware scheduler-safety readiness
  - default no-argument behavior unchanged

- [ ] **Step 1: Add RED explicit-path isolation tests**

Append to `tests/test_composition.py`:

```python
import bridge.runtime as rt


class DatabaseFactoryPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_db_file = rt.DB_FILE
        self.old_ready = rt._DB_SCHEMA_READY

    def tearDown(self):
        rt.DB_FILE = self.old_db_file
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = self.old_ready
            if hasattr(rt, "_DB_SCHEMA_READY_PATHS"):
                rt._DB_SCHEMA_READY_PATHS.clear()
        self.tmp.cleanup()

    def test_explicit_database_paths_initialize_independently(self):
        default_path = self.root / "default.sqlite3"
        explicit_a = self.root / "a.sqlite3"
        explicit_b = self.root / "b.sqlite3"
        rt.DB_FILE = default_path
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
            if hasattr(rt, "_DB_SCHEMA_READY_PATHS"):
                rt._DB_SCHEMA_READY_PATHS.clear()

        default_db = rt.db_connect()
        default_db.close()

        a = rt.db_connect(explicit_a)
        a.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('which','a')"
        )
        a.commit()
        a.close()

        b = rt.db_connect(explicit_b)
        self.assertIsNone(
            b.execute(
                "SELECT value FROM meta WHERE key='which'"
            ).fetchone()
        )
        b.close()

        self.assertTrue(explicit_a.is_file())
        self.assertTrue(explicit_b.is_file())

    def test_explicit_factory_does_not_mutate_global_db_file(self):
        original = rt.DB_FILE
        explicit = self.root / "factory.sqlite3"
        db = rt.db_connect(explicit)
        db.close()
        self.assertEqual(rt.DB_FILE, original)
```

Add to `tests/test_sqlite_contention.py`:

```python
    def test_explicit_path_workers_still_use_serialized_connection(self):
        path = Path(self.tmp.name) / "explicit-worker.sqlite3"
        db = rt.db_connect(path)
        try:
            self.assertIsInstance(db, rt._SerializedSQLiteConnection)
            self.assertEqual(
                db.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                "wal",
            )
        finally:
            db.close()
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
python -m unittest   tests.test_composition.DatabaseFactoryPathTests   tests.test_sqlite_contention.SqliteContentionTests.test_explicit_path_workers_still_use_serialized_connection -v
```

Expected: `TypeError` because current final runtime `db_connect()` accepts no path.

- [ ] **Step 3: Extend the baseline connector in `bridge/database.py`**

Replace the current function with:

```python
def db_connect(database_path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite database and initialize its schema."""
    path = Path(database_path) if database_path is not None else DB_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(
        path,
        timeout=30,
        factory=_SerializedSQLiteConnection,
    )
    _apply_connection_pragmas(db, timeout=30.0)
    db.execute("PRAGMA auto_vacuum=INCREMENTAL")
    db.execute("PRAGMA journal_mode=WAL")
    _load_optional_vector_extension(db)
    initialize_database_schema(db)
    return db
```

Do not mutate `DB_FILE`.

- [ ] **Step 4: Make `scheduler_safety.py` path-aware**

Replace its readiness section with this contract:

```python
_ORIGINAL_DB_CONNECT = db_connect
_DB_SCHEMA_READY = False
_DB_SCHEMA_READY_PATHS: set[Path] = set()
_DB_SCHEMA_LOCK = threading.Lock()


def _database_path(database_path: Path | None = None) -> Path:
    path = Path(database_path) if database_path is not None else DB_FILE
    return path.expanduser().resolve()


def _lightweight_db_connect(
    database_path: Path | None = None,
    timeout: float = 30.0,
):
    path = _database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        path,
        timeout=timeout,
        factory=_SerializedSQLiteConnection,
    )
    _apply_connection_pragmas(connection, timeout=timeout)
    return connection


def db_connect(database_path: Path | None = None):
    """Initialize each database path once, then use lightweight handles."""
    global _DB_SCHEMA_READY
    path = _database_path(database_path)
    default_path = _database_path(None)

    def ready() -> bool:
        if path == default_path:
            return bool(_DB_SCHEMA_READY)
        return path in _DB_SCHEMA_READY_PATHS

    if not ready():
        with _DB_SCHEMA_LOCK:
            if not ready():
                connection = _ORIGINAL_DB_CONNECT(path)
                if path == default_path:
                    _DB_SCHEMA_READY = True
                else:
                    _DB_SCHEMA_READY_PATHS.add(path)
                return connection
    return _lightweight_db_connect(path)
```

Use the already-available runtime `Path` symbol; do not add another public import name.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
python -m unittest   tests.test_composition.DatabaseFactoryPathTests   tests.test_sqlite_contention -v
```

Expected: PASS.

- [ ] **Step 6: Run migration/database regression tests**

Run:

```bash
python -m unittest   tests.test_migrations   tests.test_database_optimization   tests.test_repository_transactions -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add bridge/database.py bridge/scheduler_safety.py tests/test_composition.py tests/test_sqlite_contention.py
git commit -m "refactor: support injected database paths"
```

---

### Task 3: Migrate all six top-level workers to BridgeServices

**Files:**
- Modify: `bridge/main.py:47-187`
- Modify: `bridge/media.py:213-245`
- Modify: `bridge/help.py:470-487`
- Modify: `tests/test_composition.py`

**Interfaces:**
- Consumes:
  - `BridgeServices.config`
  - `BridgeServices.db_factory()`
  - `BridgeServices.telegram.send_text`
- Produces exact worker signatures:
  - `process_message_job(services, fields, chat_id, text, message_id, queued_session_id=None, model_override=None, job_id=None)`
  - `process_image_job(services, chat_id, file_id, caption, file_size, message_id, queued_session_id=None, model_override=None, job_id=None)`
  - `process_callback_job(services, chat_id, callback, job_id=None)`
  - `process_edit_job(services, chat_id, message_id, text, model_override=None, job_id=None)`
  - `process_voice_job(services, fields, chat_id, voice, message_id, queued_session_id=None, model_override=None, job_id=None)`
  - `process_document_job(services, chat_id, document, message_id=None, model_override=None, job_id=None)`

- [ ] **Step 1: Add private composition imports to runtime-stage files in the RED test expectation only**

The production imports are implemented in Step 4. First append worker tests to `tests/test_composition.py`:

```python
import inspect
from unittest.mock import patch


class WorkerInjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workers.sqlite3"
        self.db = rt.db_connect(self.db_path)
        self.db.close()
        self.opened = 0
        self.sent = []

        config = BridgeConfig(
            bot_token="injected-token",
            api_key="injected-key",
            default_model="injected::model",
            default_character_file="mira.png",
            card_file=Path(self.tmp.name) / "mira.png",
            db_file=self.db_path,
            allowed_users=frozenset({"100"}),
        )
        self.services = BridgeServices(
            config=config,
            db_factory=self._db_factory,
            telegram=TelegramRuntime(
                request=lambda *_args, **_kwargs: {},
                send_text=lambda *args, **_kwargs: self.sent.append(args),
            ),
            background=BackgroundRuntime(
                submit_chat=lambda *_args, **_kwargs: True,
                register_backlog_dispatcher=lambda _callback: None,
                begin_shutdown=lambda: None,
            ),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _db_factory(self):
        self.opened += 1
        return rt.db_connect(self.db_path)

    def test_all_worker_signatures_receive_services_not_startup_bundle(self):
        expectations = {
            rt.process_message_job: (
                "services", "fields", "chat_id", "text", "message_id",
                "queued_session_id", "model_override", "job_id",
            ),
            rt.process_image_job: (
                "services", "chat_id", "file_id", "caption", "file_size",
                "message_id", "queued_session_id", "model_override", "job_id",
            ),
            rt.process_callback_job: (
                "services", "chat_id", "callback", "job_id",
            ),
            rt.process_edit_job: (
                "services", "chat_id", "message_id", "text",
                "model_override", "job_id",
            ),
            rt.process_voice_job: (
                "services", "fields", "chat_id", "voice", "message_id",
                "queued_session_id", "model_override", "job_id",
            ),
            rt.process_document_job: (
                "services", "chat_id", "document", "message_id",
                "model_override", "job_id",
            ),
        }
        for function, expected in expectations.items():
            with self.subTest(function=function.__name__):
                self.assertEqual(
                    tuple(inspect.signature(function).parameters),
                    expected,
                )

    def test_message_worker_uses_injected_db_and_config(self):
        captured = {}

        def fake_process_message(
            db, token, api_key, model, fields, chat_id, text, message_id,
            **kwargs,
        ):
            captured.update(
                db=db,
                token=token,
                api_key=api_key,
                model=model,
                fields=fields,
                chat_id=chat_id,
                text=text,
                message_id=message_id,
                kwargs=kwargs,
            )

        with patch.object(
            rt, "committed_assistant_for_message", return_value=None
        ), patch.object(
            rt, "process_message", side_effect=fake_process_message
        ):
            rt.process_message_job(
                self.services,
                {"name": "Mira"},
                "chat",
                "hello",
                10,
            )

        self.assertEqual(self.opened, 1)
        self.assertEqual(captured["token"], "injected-token")
        self.assertEqual(captured["api_key"], "injected-key")
        self.assertEqual(captured["model"], "injected::model")

    def test_recovered_model_override_wins_over_config_default(self):
        captured = {}

        with patch.object(
            rt, "committed_assistant_for_message", return_value=None
        ), patch.object(
            rt,
            "process_message",
            side_effect=lambda _db, _token, _key, model, *_args, **_kwargs:
                captured.setdefault("model", model),
        ):
            rt.process_message_job(
                self.services,
                {"name": "Mira"},
                "chat",
                "hello",
                11,
                None,
                "stored::model",
            )

        self.assertEqual(captured["model"], "stored::model")

    def test_callback_failure_uses_injected_send_text(self):
        with patch.object(
            rt,
            "process_callback",
            side_effect=RuntimeError("boom"),
        ):
            rt.process_callback_job(
                self.services,
                "chat",
                {"id": "callback"},
            )

        self.assertEqual(
            self.sent,
            [(
                "injected-token",
                "chat",
                "Callback processing failed; try the command again.",
            )],
        )
```

- [ ] **Step 2: Run worker tests and verify RED**

Run:

```bash
python -m unittest tests.test_composition.WorkerInjectionTests -v
```

Expected: signature assertions fail because workers still expose token/API/model bundles.

- [ ] **Step 3: Add private composition imports to each runtime-stage file**

In `bridge/main.py`, `bridge/media.py`, and `bridge/help.py`, use private aliases only:

```python
from bridge.composition import BridgeServices as _BridgeServices
```

Do not import `BridgeServices` publicly into the shared namespace.

- [ ] **Step 4: Migrate the four workers in `main.py`**

Use these exact dependency headers:

```python
def process_message_job(
    services: _BridgeServices,
    fields: dict,
    chat_id: str,
    text: str,
    message_id: int,
    queued_session_id: str | None = None,
    model_override: str | None = None,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    api_key = services.config.api_key
    model = model_override or services.config.default_model
    with chat_job_lock(chat_id):
        db = services.db_factory()
        ...
```

```python
def process_image_job(
    services: _BridgeServices,
    chat_id: str,
    file_id: str,
    caption: str,
    file_size: int,
    message_id: int,
    queued_session_id: str | None = None,
    model_override: str | None = None,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    model = model_override or services.config.default_model
    with chat_job_lock(chat_id):
        db = services.db_factory()
        ...
```

```python
def process_callback_job(
    services: _BridgeServices,
    chat_id: str,
    callback: dict,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    with chat_job_lock(chat_id):
        db = services.db_factory()
        ...
```

Replace callback failure delivery:

```python
services.telegram.send_text(
    token,
    chat_id,
    "Callback processing failed; try the command again.",
)
```

```python
def process_edit_job(
    services: _BridgeServices,
    chat_id: str,
    message_id: int,
    text: str,
    model_override: str | None = None,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    api_key = services.config.api_key
    model = model_override or services.config.default_model
    with chat_job_lock(chat_id):
        db = services.db_factory()
        ...
```

Replace edit failure delivery with `services.telegram.send_text(...)`.

For message/image failure delivery, replace only worker-level direct `send_text` calls. Do not migrate downstream `send_reply`, `process_message`, or `process_telegram_image`.

- [ ] **Step 5: Migrate `process_voice_job` in `bridge/media.py`**

Use:

```python
def process_voice_job(
    services: _BridgeServices,
    fields: dict,
    chat_id: str,
    voice: dict,
    message_id: int,
    queued_session_id: str | None = None,
    model_override: str | None = None,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    api_key = services.config.api_key
    model = model_override or services.config.default_model
    with chat_job_lock(chat_id):
        db = services.db_factory()
        ...
```

Replace the worker-level failure `send_text` with:

```python
services.telegram.send_text(
    token,
    chat_id,
    "Voice processing failed. Use /voice_input status to check transcription settings.",
)
```

Keep `process_voice_message(...)` unchanged.

- [ ] **Step 6: Migrate `process_document_job` in `bridge/help.py`**

Use:

```python
def process_document_job(
    services: _BridgeServices,
    chat_id: str,
    document: dict,
    message_id: int | None = None,
    model_override: str | None = None,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    model = model_override or services.config.default_model
    with chat_job_lock(chat_id):
        db = services.db_factory()
        ...
```

Replace the worker-level failure `send_text` with `services.telegram.send_text(...)`.

Keep `import_telegram_document(...)` unchanged.

- [ ] **Step 7: Run worker tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_composition.WorkerInjectionTests -v
```

Expected: PASS.

- [ ] **Step 8: Run media/document and Phase 3 transaction regressions**

Run:

```bash
python -m unittest   tests.test_pdf_worker   tests.test_quoted_voice   tests.test_repository_transactions   tests.test_group_turn_gating -v
```

Expected: PASS.

- [ ] **Step 9: Commit Task 3**

```bash
git add bridge/main.py bridge/media.py bridge/help.py tests/test_composition.py
git commit -m "refactor: inject runtime context into workers"
```

---

### Task 4: Propagate BridgeServices through durable submission, recovery, and backlog dispatch

**Files:**
- Modify: `bridge/main.py:204-249`
- Modify: `tests/test_composition.py`
- Modify: `tests/test_background_lifecycle.py`

**Interfaces:**
- Consumes:
  - Task 3 worker signatures
  - `BackgroundRuntime.submit_chat`
- Produces:
  - `submit_durable_chat_job(db, background, label, chat_id, job_id, function, *args) -> bool`
  - `dispatch_recovered_jobs(db, services, fields, *, recover_running=True) -> None`
  - `make_durable_backlog_dispatcher(services, fields) -> Callable[[], None]`

- [ ] **Step 1: Add RED durable-submission and recovery identity tests**

Append to `tests/test_composition.py`:

```python
class RecoveryCompositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "recovery.sqlite3"
        self.db = rt.db_connect(self.path)
        self.submitted = []
        config = BridgeConfig(
            bot_token="token",
            api_key="key",
            default_model="current::model",
            default_character_file="mira.png",
            card_file=Path(self.tmp.name) / "mira.png",
            db_file=self.path,
            allowed_users=frozenset(),
        )
        self.background = BackgroundRuntime(
            submit_chat=self._submit,
            register_backlog_dispatcher=lambda _callback: None,
            begin_shutdown=lambda: None,
        )
        self.services = BridgeServices(
            config=config,
            db_factory=lambda: rt.db_connect(self.path),
            telegram=TelegramRuntime(
                request=lambda *_args, **_kwargs: {},
                send_text=lambda *_args, **_kwargs: None,
            ),
            background=self.background,
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _submit(self, label, chat_id, function, *args):
        self.submitted.append((label, chat_id, function, args))
        return True

    def test_submit_durable_chat_job_uses_injected_background_and_explicit_job_id(self):
        with patch.object(rt, "mark_job_scheduled") as scheduled:
            queued = rt.submit_durable_chat_job(
                self.db,
                self.background,
                "generation",
                "chat",
                41,
                rt.process_message_job,
                self.services,
                {"name": "Mira"},
                "chat",
                "hello",
                10,
                None,
                None,
            )

        self.assertTrue(queued)
        self.assertIs(self.submitted[0][3][0], self.services)
        self.assertEqual(self.submitted[0][3][-1], 41)
        scheduled.assert_called_once_with(self.db, 41)

    def test_recovered_job_propagates_same_services_and_stored_model(self):
        row = (
            51,
            "chat",
            "session",
            "10",
            "generation",
            '{"text":"hello","model":"stored::model"}',
        )
        with patch.object(
            rt,
            "recover_jobs",
            return_value=[row],
        ), patch.object(
            rt,
            "mark_job_scheduled",
        ):
            rt.dispatch_recovered_jobs(
                self.db,
                self.services,
                {"name": "Mira"},
            )

        label, chat_id, function, args = self.submitted[0]
        self.assertEqual(label, "generation")
        self.assertEqual(chat_id, "chat")
        self.assertIs(function, rt.process_message_job)
        self.assertIs(args[0], self.services)
        self.assertEqual(args[-2], "stored::model")
        self.assertEqual(args[-1], 51)

    def test_failed_background_submission_does_not_mark_job_scheduled(self):
        background = BackgroundRuntime(
            submit_chat=lambda *_args, **_kwargs: False,
            register_backlog_dispatcher=lambda _callback: None,
            begin_shutdown=lambda: None,
        )
        with patch.object(rt, "mark_job_scheduled") as scheduled:
            queued = rt.submit_durable_chat_job(
                self.db,
                background,
                "generation",
                "chat",
                52,
                rt.process_message_job,
                self.services,
                {"name": "Mira"},
                "chat",
                "hello",
                10,
                None,
                None,
            )
        self.assertFalse(queued)
        scheduled.assert_not_called()

    def test_backlog_dispatcher_reuses_same_services_instance(self):
        opened = []
        seen = []

        def factory():
            db = rt.db_connect(self.path)
            opened.append(db)
            return db

        services = BridgeServices(
            config=self.services.config,
            db_factory=factory,
            telegram=self.services.telegram,
            background=self.services.background,
        )

        with patch.object(
            rt,
            "dispatch_recovered_jobs",
            side_effect=lambda db, actual_services, fields, **kwargs:
                seen.append((db, actual_services, fields, kwargs)),
        ):
            dispatcher = rt.make_durable_backlog_dispatcher(
                services,
                {"name": "Mira"},
            )
            dispatcher()

        self.assertEqual(len(opened), 1)
        self.assertIs(seen[0][1], services)
        self.assertEqual(seen[0][3], {"recover_running": False})
```

- [ ] **Step 2: Run recovery tests and verify RED**

Run:

```bash
python -m unittest tests.test_composition.RecoveryCompositionTests -v
```

Expected: signature/type errors because durable submission/recovery still require token/API/model globals.

- [ ] **Step 3: Replace durable submission with explicit background + job ID**

In `bridge/main.py`:

```python
def submit_durable_chat_job(
    db: sqlite3.Connection,
    background,
    label: str,
    chat_id: str,
    job_id: int,
    function,
    *args,
) -> bool:
    queued = background.submit_chat(
        label,
        chat_id,
        function,
        *args,
        int(job_id),
    )
    if queued:
        mark_job_scheduled(db, int(job_id))
    return queued
```

This intentionally removes the old hidden `args[-1]` job-ID inference.

- [ ] **Step 4: Rewrite recovered dispatch to one services object**

Use:

```python
def dispatch_recovered_jobs(
    db: sqlite3.Connection,
    services: _BridgeServices,
    fields: dict,
    *,
    recover_running: bool = True,
) -> None:
    for (
        job_id,
        chat_id,
        session_id,
        message_id,
        kind,
        payload_json,
    ) in recover_jobs(db, recover_running=recover_running):
        try:
            payload = json.loads(payload_json)
            model_override = str(
                payload.get("model") or services.config.default_model
            )
            session_for_job = (
                None
                if payload.get("resolve_active")
                else str(session_id)
            )

            if kind in {"generation", "command"}:
                worker = process_message_job
                worker_args = (
                    services,
                    fields,
                    str(chat_id),
                    str(payload["text"]),
                    int(message_id),
                    session_for_job,
                    model_override,
                )
            elif kind == "callback":
                worker = process_callback_job
                worker_args = (
                    services,
                    str(chat_id),
                    payload["callback"],
                )
            elif kind == "edit":
                worker = process_edit_job
                worker_args = (
                    services,
                    str(chat_id),
                    int(message_id),
                    str(payload["text"]),
                    model_override,
                )
            elif kind == "voice":
                worker = process_voice_job
                worker_args = (
                    services,
                    fields,
                    str(chat_id),
                    payload["voice"],
                    int(message_id),
                    None if payload.get("resolve_active") else session_for_job,
                    model_override,
                )
            elif kind == "image":
                worker = process_image_job
                worker_args = (
                    services,
                    str(chat_id),
                    str(payload["file_id"]),
                    str(payload.get("caption") or ""),
                    int(payload.get("file_size") or 0),
                    int(message_id),
                    None if payload.get("resolve_active") else session_for_job,
                    model_override,
                )
            elif kind == "document":
                worker = process_document_job
                worker_args = (
                    services,
                    str(chat_id),
                    payload["document"],
                    int(message_id),
                    model_override,
                )
            else:
                finish_job(
                    db,
                    int(job_id),
                    "failed",
                    "unsupported recovered job kind",
                )
                continue

            queued = submit_durable_chat_job(
                db,
                services.background,
                kind,
                str(chat_id),
                int(job_id),
                worker,
                *worker_args,
            )
            if not queued:
                logging.warning(
                    "Could not dispatch recovered %s job %s",
                    kind,
                    job_id,
                )
        except Exception as exc:
            finish_job(db, int(job_id), "failed", str(exc))
            logging.error(
                "Could not recover job %s",
                job_id,
                exc_info=True,
            )
```

Preserve the current callback/edit/voice/image/document behavior exactly; only dependency transport changes.

- [ ] **Step 5: Rewrite backlog dispatcher**

Use:

```python
def make_durable_backlog_dispatcher(
    services: _BridgeServices,
    fields: dict,
):
    def dispatch() -> None:
        db = services.db_factory()
        try:
            dispatch_recovered_jobs(
                db,
                services,
                fields,
                recover_running=False,
            )
        finally:
            db.close()

    return dispatch
```

- [ ] **Step 6: Update every live `submit_durable_chat_job` call in `main.py`**

Every call must now pass, in order:

```python
db,
services.background,
label,
chat_id,
job_id,
worker,
worker arguments excluding job_id
```

Examples:

```python
queued = submit_durable_chat_job(
    db,
    services.background,
    "generation",
    chat_id,
    job_id,
    process_message_job,
    services,
    fields,
    chat_id,
    str(text),
    message_id,
    None,
    None,
)
```

```python
queued = submit_durable_chat_job(
    db,
    services.background,
    "image",
    chat_id,
    job_id,
    process_image_job,
    services,
    chat_id,
    file_id,
    caption,
    file_size,
    message_id,
    None,
    None,
)
```

```python
queued = submit_durable_chat_job(
    db,
    services.background,
    "voice",
    chat_id,
    job_id,
    process_voice_job,
    services,
    fields,
    chat_id,
    voice,
    message_id,
    None,
    None,
)
```

```python
queued = submit_durable_chat_job(
    db,
    services.background,
    "document",
    chat_id,
    job_id,
    process_document_job,
    services,
    chat_id,
    document,
    message_id,
    None,
)
```

Callback and edit calls follow their exact Task 3 signatures:

```python
submit_durable_chat_job(
    db,
    services.background,
    "callback",
    chat_id,
    job_id,
    process_callback_job,
    services,
    chat_id,
    callback,
)
```

```python
submit_durable_chat_job(
    db,
    services.background,
    "edit",
    chat_id,
    job_id,
    process_edit_job,
    services,
    chat_id,
    message_id,
    text,
    None,
)
```

- [ ] **Step 7: Run Task 4 tests and verify GREEN**

Run:

```bash
python -m unittest   tests.test_composition.RecoveryCompositionTests   tests.test_background_lifecycle -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 4**

```bash
git add bridge/main.py tests/test_composition.py tests/test_background_lifecycle.py
git commit -m "refactor: inject services into durable recovery"
```

---

### Task 5: Make main/check/signal handling the explicit composition root

**Files:**
- Modify: `bridge/main.py:1-16, 252-474`
- Modify: `tests/test_composition.py`
- Modify: `tests/test_background_lifecycle.py`
- Modify: `tests/test_runtime_loader.py`

**Interfaces:**
- Consumes all prior Phase 4 types and worker/recovery signatures.
- Produces:
  - `run_check(services: BridgeServices) -> int`
  - `install_bridge_signal_handlers(on_shutdown: Callable[[], None]) -> None`
  - private `_load_startup_config(environ) -> BridgeConfig`
  - private `_build_startup_services(config) -> BridgeServices`
  - one `BridgeServices` instance reused across normal startup.

- [ ] **Step 1: Add RED check-mode composition tests**

Append:

```python
class StartupCompositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.card = root / "mira.png"
        self.card.write_bytes(b"card")
        self.config = BridgeConfig(
            bot_token="token",
            api_key="key",
            default_model="provider::model",
            default_character_file="mira.png",
            card_file=self.card,
            db_file=root / "bridge.sqlite3",
            allowed_users=frozenset({"100"}),
        )
        self.requests = []
        self.services = BridgeServices(
            config=self.config,
            db_factory=lambda: rt.db_connect(self.config.db_file),
            telegram=TelegramRuntime(
                request=self._request,
                send_text=lambda *_args, **_kwargs: None,
            ),
            background=BackgroundRuntime(
                submit_chat=lambda *_args, **_kwargs: True,
                register_backlog_dispatcher=lambda _callback: None,
                begin_shutdown=lambda: None,
            ),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _request(self, token, method, payload=None):
        self.requests.append((token, method, payload))
        return {"username": "bridge_bot"}

    def test_run_check_uses_prebuilt_services_without_reloading_environment(self):
        with patch.object(
            rt,
            "load_env_file",
            side_effect=AssertionError("run_check must not reload env"),
        ), patch.object(
            rt,
            "card_fields",
            return_value={"name": "Mira"},
        ), patch.object(
            rt,
            "read_png_chara",
            return_value={},
        ), patch.object(
            rt,
            "phase3_api_configured",
            return_value=False,
        ):
            self.assertEqual(rt.run_check(self.services), 0)

        self.assertEqual(
            self.requests,
            [("token", "getMe", None)],
        )

    def test_signal_handler_captures_injected_shutdown_callable(self):
        installed = {}
        calls = []

        def fake_signal(signum, handler):
            installed[signum] = handler

        with patch.object(rt.signal, "signal", side_effect=fake_signal):
            rt.install_bridge_signal_handlers(
                lambda: calls.append("shutdown")
            )

        handler = installed[rt.signal.SIGTERM]
        handler(rt.signal.SIGTERM, None)
        self.assertTrue(rt._SHUTDOWN_EVENT.is_set())
        self.assertEqual(calls, ["shutdown"])
        rt._SHUTDOWN_EVENT.clear()

    def test_main_check_builds_services_once_and_passes_same_object(self):
        with patch.object(
            rt.sys,
            "argv",
            ["bridge", "--check"],
        ), patch.object(
            rt,
            "load_env_file",
        ), patch.object(
            rt,
            "refresh_phase3_config",
        ), patch.object(
            rt,
            "enforce_runtime_permissions",
        ), patch.object(
            rt,
            "_load_startup_config",
            return_value=self.config,
        ) as load_config, patch.object(
            rt,
            "_build_startup_services",
            return_value=self.services,
        ) as build_services, patch.object(
            rt,
            "validate_startup_credential",
        ), patch.object(
            rt,
            "set_bot_commands",
        ), patch.object(
            rt,
            "run_check",
            return_value=0,
        ) as run_check:
            self.assertEqual(rt.main(), 0)

        load_config.assert_called_once()
        build_services.assert_called_once_with(self.config)
        run_check.assert_called_once_with(self.services)
```

- [ ] **Step 2: Add runtime-stage regression**

In `tests/test_runtime_loader.py`:

```python
    def test_composition_module_is_not_a_runtime_stage(self):
        loaded_modules = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn("composition.py", loaded_modules)
```

- [ ] **Step 3: Run startup tests and verify RED**

Run:

```bash
python -m unittest   tests.test_composition.StartupCompositionTests   tests.test_runtime_loader.RuntimeLoaderTests.test_composition_module_is_not_a_runtime_stage -v
```

Expected: current `run_check()` takes no services, signal installation takes no shutdown callable, and private startup builders do not exist.

- [ ] **Step 4: Add private composition imports to `main.py`**

Use private aliases:

```python
from functools import partial as _partial

from bridge.composition import (
    BackgroundRuntime as _BackgroundRuntime,
    BridgeConfig as _BridgeConfig,
    BridgeServices as _BridgeServices,
    TelegramRuntime as _TelegramRuntime,
    build_bridge_services as _build_bridge_services_value,
    load_bridge_config as _load_bridge_config_value,
    validate_bridge_config as _validate_bridge_config_value,
)
```

Private names prevent accidental public runtime API growth.

- [ ] **Step 5: Make shutdown handler dependency explicit**

Replace root lifecycle helpers with:

```python
_SHUTDOWN_EVENT = threading.Event()


def request_bridge_shutdown(
    on_shutdown,
    signum=None,
    _frame=None,
) -> None:
    if signum is not None:
        logging.info(
            "Bridge shutdown requested by signal %s",
            signum,
        )
    _SHUTDOWN_EVENT.set()
    on_shutdown()


def install_bridge_signal_handlers(on_shutdown) -> None:
    def handle(signum, frame):
        request_bridge_shutdown(
            on_shutdown,
            signum,
            frame,
        )

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signum, handle)
        except (ValueError, OSError):
            logging.debug(
                "Could not install signal handler %s",
                signum,
                exc_info=True,
            )
```

Update direct shutdown calls in `main()` to:

```python
request_bridge_shutdown(services.background.begin_shutdown)
```

Do not create a global services variable.

- [ ] **Step 6: Add focused startup construction helpers**

Add:

```python
def _load_startup_config(environ) -> _BridgeConfig:
    config = _load_bridge_config_value(
        environ,
        character_dir=CHARACTER_DIR,
        db_file=DB_FILE,
    )
    try:
        _validate_bridge_config_value(config)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return config


def _build_startup_services(
    config: _BridgeConfig,
) -> _BridgeServices:
    return _build_bridge_services_value(
        config,
        db_factory=_partial(db_connect, config.db_file),
        telegram=_TelegramRuntime(
            request=telegram_request,
            send_text=send_text,
        ),
        background=_BackgroundRuntime(
            submit_chat=submit_chat_background,
            register_backlog_dispatcher=register_durable_backlog_dispatcher,
            begin_shutdown=begin_background_shutdown,
        ),
    )
```

This is the only Phase 4 location that adapts compatibility globals into `BridgeServices`.

- [ ] **Step 7: Rewrite `run_check` to consume services**

Use:

```python
def run_check(services: _BridgeServices) -> int:
    config = services.config
    fields = card_fields(read_png_chara(config.card_file))
    if phase3_api_configured():
        try:
            phase3_client().authenticate()
        except (SillyTavernApiError, ValueError) as exc:
            raise SystemExit(
                f"Live Sync check failed: {exc}"
            ) from exc

    me = services.telegram.request(
        config.bot_token,
        "getMe",
    )
    print(
        f"card={fields['name']}; "
        f"telegram=@{me.get('username')}; "
        f"model={config.default_model}; "
        f"db={config.db_file}"
    )
    print("check=ok")
    return 0
```

Delete environment loading/config discovery from `run_check()`.

- [ ] **Step 8: Rewrite startup construction in `main()`**

The startup prefix becomes:

```python
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    load_env_file()
    refresh_phase3_config()
    enforce_runtime_permissions()

    config = _load_startup_config(os.environ)
    try:
        validate_startup_credential(config.default_model)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    services = _build_startup_services(config)
    set_bot_commands(config.bot_token)

    if args.check:
        return run_check(services)

    fields = card_fields(read_png_chara(config.card_file))
    _SHUTDOWN_EVENT.clear()
    install_bridge_signal_handlers(
        services.background.begin_shutdown
    )
    db = services.db_factory()
    start_phase3_sync_worker()
    services.background.register_backlog_dispatcher(
        make_durable_backlog_dispatcher(
            services,
            fields,
        )
    )
    dispatch_recovered_jobs(
        db,
        services,
        fields,
    )
    offset = int(get_meta(db, "telegram_offset", "0"))
    permitted = services.config.allowed_users
```

Delete the old token/API/model environment reads and `allowed_users()` call from this migrated path.

- [ ] **Step 9: Switch root polling and root send-text calls to services**

The polling call becomes:

```python
updates = services.telegram.request(
    config.bot_token,
    "getUpdates",
    {
        "offset": offset,
        "timeout": 50,
        "allowed_updates": [
            "message",
            "edited_message",
            "callback_query",
        ],
    },
)
```

Within the `main()` update loop, every direct root-level:

```python
send_text(token, chat_id, message)
```

becomes:

```python
services.telegram.send_text(
    config.bot_token,
    chat_id,
    message,
)
```

Pass `config.default_model` wherever the current main-loop `model` value is used for session creation or durable-job payloads.

Do not alter feature-level Telegram calls inside downstream command/panel/workflow functions.

- [ ] **Step 10: Add no-environment-rediscovery source guard**

Append to `tests/test_composition.py`:

```python
    def test_migrated_worker_and_check_sources_do_not_rediscover_environment(self):
        root = Path(__file__).parents[1] / "bridge"
        files = {
            "main.py": (root / "main.py").read_text(encoding="utf-8"),
            "media.py": (root / "media.py").read_text(encoding="utf-8"),
            "help.py": (root / "help.py").read_text(encoding="utf-8"),
        }

        worker_markers = (
            "def process_message_job",
            "def process_image_job",
            "def process_callback_job",
            "def process_edit_job",
            "def dispatch_recovered_jobs",
            "def make_durable_backlog_dispatcher",
            "def run_check",
        )
        for marker in worker_markers:
            start = files["main.py"].index(marker)
            next_def = files["main.py"].find("\ndef ", start + 4)
            chunk = files["main.py"][
                start: next_def if next_def >= 0 else None
            ]
            self.assertNotIn("os.environ", chunk, marker)

        for filename, marker in (
            ("media.py", "def process_voice_job"),
            ("help.py", "def process_document_job"),
        ):
            start = files[filename].index(marker)
            next_def = files[filename].find("\ndef ", start + 4)
            chunk = files[filename][
                start: next_def if next_def >= 0 else None
            ]
            self.assertNotIn("os.environ", chunk, marker)
```

- [ ] **Step 11: Run Task 5 tests**

Run:

```bash
python -m unittest   tests.test_composition   tests.test_background_lifecycle   tests.test_runtime_loader -v
```

Expected: PASS.

- [ ] **Step 12: Run startup/routing regression set**

Run:

```bash
python -m unittest   tests.test_start_onboarding   tests.test_session_command_routing   tests.test_group_turn_gating   tests.test_generation_continuation   tests.test_pdf_worker   tests.test_quoted_voice -v
```

Expected: PASS.

- [ ] **Step 13: Commit Task 5**

```bash
git add   bridge/main.py   tests/test_composition.py   tests/test_background_lifecycle.py   tests/test_runtime_loader.py
git commit -m "refactor: compose bridge runtime dependencies at startup"
```

---

### Task 6: Final Phase 4 integration, compatibility audit, and whole-branch review

**Files:**
- Verify all Phase 4 production/test files.
- Modify tests only for missing acceptance coverage or review findings.

**Interfaces:**
- Consumes Tasks 1–5.
- Produces a PR-ready Phase 4 branch with final evidence.

- [ ] **Step 1: Add final source-boundary assertions**

In `tests/test_composition.py`, add:

```python
class CompositionBoundaryTests(unittest.TestCase):
    def test_composition_module_does_not_import_compatibility_runtime(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "composition.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("bridge.runtime", source)
        self.assertNotIn("CURRENT_SERVICES", source)
        self.assertNotIn("get_services(", source)
        self.assertNotIn("set_services(", source)

    def test_phase5_service_classes_are_not_introduced(self):
        root = Path(__file__).parents[1] / "bridge"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in root.glob("*.py")
        )
        for forbidden in (
            "class GroupDirectorService",
            "class MemoryService",
            "class PersonaService",
            "class SyncService",
            "class JobService",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
```

- [ ] **Step 2: Run focused Phase 4 tests**

Run:

```bash
python -m unittest   tests.test_composition   tests.test_background_lifecycle   tests.test_runtime_loader   tests.test_sqlite_contention -v
```

Expected: PASS.

- [ ] **Step 3: Run Phase 3 persistence/transaction regressions**

Run:

```bash
python -m unittest   tests.test_repository_transactions   tests.test_migrations   tests.test_database_optimization   tests.test_director_goals   tests.test_scene_state   tests.test_memory_curator   tests.test_group_director   tests.test_group_turn_gating -v
```

Expected: PASS.

- [ ] **Step 4: Run broader recovery/sync/state regressions**

Run:

```bash
python -m unittest   tests.test_state_integrity   tests.test_sync_audit   tests.test_sync_phase3   tests.test_reset_behavior   tests.test_session_delete   tests.test_session_command_routing   tests.test_generation_continuation   tests.test_hindsight_session_cleanup -v
```

Expected: PASS.

- [ ] **Step 5: Compile Python sources**

Run:

```bash
python -m compileall bridge tests
```

Expected: exit 0.

- [ ] **Step 6: Run complete unittest suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 7: Run pytest exactly as CI does**

Run:

```bash
pytest
```

Expected: all tests PASS.

- [ ] **Step 8: Run dependency checks**

Run:

```bash
python -m pip check
pip-audit
```

Expected: exit 0 in the locked CI environment.

- [ ] **Step 9: Verify runtime compatibility invariants**

Run:

```bash
python -m unittest tests.test_runtime_loader -v
grep -R "_ORIGINAL_.*=" -n bridge
grep -n "composition.py" bridge/runtime_loader.py
```

Expected:

- runtime-loader tests PASS
- no new `_ORIGINAL_*` assignment attributable to Phase 4
- `composition.py` does not appear in runtime stages

Existing pre-Phase-4 `_ORIGINAL_*` compatibility code may still exist; compare against base rather than demanding a repository-wide zero count.

- [ ] **Step 10: Verify worker signatures and global-discovery removal**

Run:

```bash
python - <<'PY'
import inspect
import bridge.runtime as rt

for name in (
    "process_message_job",
    "process_image_job",
    "process_callback_job",
    "process_edit_job",
    "process_voice_job",
    "process_document_job",
):
    print(name, inspect.signature(getattr(rt, name)))
PY
```

Expected: every worker begins with `services`; none has startup parameters named `token`, `api_key`, `default_model`, or plain `model`.

Then:

```bash
grep -nE "os\.environ\.get\(.*(TELEGRAM_BOT_TOKEN|LLM_API_KEY|SILLYTAVERN_MODEL|ALLOWED_USERS)"   bridge/main.py bridge/media.py bridge/help.py
```

Expected: environment reads may remain in non-migrated compatibility functions, but not inside migrated workers, recovered dispatch, backlog dispatcher, or `run_check`. The behavior tests in `test_composition.py` are authoritative for function scope.

- [ ] **Step 11: Compare all 35 spec acceptance criteria against code/tests**

Record explicit evidence for:

- composition module ordinary import
- frozen config/services/runtime dataclasses
- secret repr exclusion
- pure explicit-mapping config loader
- one startup service graph
- path-aware DB factory
- six migrated workers
- stored recovered model override
- same services identity for live/recovered flow
- injected background submission
- injected root Telegram request/failure transport
- allowed-users single parse
- no service locator
- no Phase 5 services
- runtime stage order unchanged
- no new overrides/capture chains
- Phase 3 tests green
- durable/recovery behavior green
- full CI green

- [ ] **Step 12: Request whole-branch review**

Use Superpowers `requesting-code-review`.

Review focus:

- path-aware scheduler readiness when default and explicit DB paths interleave
- accidental second database/runtime lock instance caused by ordinary imports
- recovered model override ordering/argument placement
- explicit durable job ID marking after removal of `args[-1]` inference
- worker DB closure on early return/exception
- credentials accidentally exposed through repr/log/error
- services object accidentally reconstructed inside recovery/backlog paths
- any root-level Telegram/background call still bypassing the injected adapters
- accidental Phase 5 service extraction or runtime-loader changes

For every Critical/Important finding:

1. add a regression test
2. verify RED
3. apply the smallest fix
4. verify GREEN

- [ ] **Step 13: Run fresh final verification after review**

Run:

```bash
python -m compileall bridge tests
python -m unittest discover -s tests -v
pytest
python -m pip check
pip-audit
```

Expected: every command exits 0 on the exact final head.

- [ ] **Step 14: Prepare the Phase 4 PR only after exact-head CI is green**

PR must link:

- `docs/superpowers/specs/2026-09-19-runtime-architecture-migration-design.md`
- `docs/superpowers/specs/2026-09-19-composition-root-runtime-context-design.md`

PR summary must state:

- explicit immutable startup config
- explicit root runtime context
- all six top-level workers migrated
- path-aware injected DB factory
- live/recovered job context reuse
- injected background/root Telegram boundaries
- compatibility runtime intentionally retained
- no Phase 5 application services introduced

The PR remains draft until whole-branch review findings are resolved and final exact-head CI is green.

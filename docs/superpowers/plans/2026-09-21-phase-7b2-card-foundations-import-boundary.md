# Phase 7B2 — Card Foundations Ordinary-Import Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract card/content, callback-token, panel-primitive, and ambient runtime-context ownership into ordinary modules while preserving the residual `cards.py` Telegram/Persona UI shell for later Phase 7B4 retirement.

**Architecture:** Extend the existing canonical `bridge.config`, then create three focused ordinary foundation modules plus one state-owning context module: `runtime_context.py`, `panel_utils.py`, `card_content.py`, and `callback_tokens.py`. `common.py`, `cards.py`, `character_identity.py`, and `runtime.py` become compatibility consumers/re-exporters of those canonical objects; `cards.py` deliberately remains exec-loaded and keeps only late-bound Persona/Telegram menu behavior.

**Tech Stack:** Python 3.11, stdlib pathlib/threading/hashlib/json/base64/struct/re/time/random/logging, SQLite through canonical `bridge.database`, existing `bridge.native_cache`, unittest/pytest/subprocess, staged compatibility runtime, GitHub Actions CI.

**Spec:** `docs/superpowers/specs/2026-09-21-phase-7b2-card-foundations-import-boundary-design.md`

## Global Constraints

- Baseline upstream `main` is `63fe8d2c3daa224d71fb1f1c40ca328c87b8987d`.
- Preserve PNG character-card parsing, field truncation, alternate-greeting extraction, World Info activation, System Prompt catalog parsing, macro replacement, and system-prompt cache behavior exactly.
- Preserve callback-token format: `"t" + sha256(kind + "|" + chat_id + "|" + value).hexdigest()[:16]`.
- Preserve callback-token TTL at exactly `900` seconds.
- Preserve callback-token database table, persistence fallback, scope validation, and expiry/deletion behavior.
- Preserve panel labels, pagination, navigation, Persona menus, character menus, character info/delete menus, and session menus.
- Preserve ambient DB connection context and panel session/actor context semantics.
- Do not migrate `telegram.py`, `persona_sync.py`, provider/model catalog behavior, memory/RAG/groups, commands/callback routing, or main composition.
- Do not remove `cards.py` from `DEFAULT_RUNTIME_STAGES` in this phase.
- Do not add `runtime_context.py`, `panel_utils.py`, `card_content.py`, or `callback_tokens.py` to any runtime stage.
- Keep the relative order of all existing legacy runtime files unchanged.
- Do not introduce runtime configuration reload.
- Do not add `globals().get(...)`, runtime namespace lookup, wildcard facade exports, reflection-driven export maps, service locators, or generic helper bags.
- Do not merge the implementation PR automatically.

## Review Focus

1. **Legacy and ordinary code use different card/world paths after a test rebind** — newly ordinary helpers must read canonical `bridge.config`; tests that combine a legacy producer with an ordinary helper must synchronize the compatibility `rt.*` path and canonical `config.*` path where both are genuinely involved.
2. **Default-card fallback silently stops following canonical configuration** — `card_fields()` must read current `config.DEFAULT_CHARACTER_FILE` for fallback naming, while `CARD_FILE` itself remains startup-derived and is not recomputed when constituent config values are rebound.
3. **A second thread-local or callback cache is created by exec loading** — runtime/common/cards compatibility imports must point to the exact ordinary functions/state owners; no duplicate `threading.local()` or `_CALLBACK_TOKEN_VALUES` may remain in exec-loaded sources.
4. **Later `character_identity.py` overwrites canonical `character_display_name`** — delete its local definition and import the ordinary object so runtime-loader override validation stays clean.
5. **Residual `cards.py` menu functions lose late binding** — the UI shell must keep resolving `telegram_request`, Persona collaborators, and runtime-rebound canonical helper names through the shared runtime namespace; do not bind Telegram/Persona implementations into ordinary modules in 7B2.

## File Structure

- Modify `bridge/config.py` — add the eleven approved card/world/prompt settings and limits.
- Create `bridge/runtime_context.py` — sole owner of DB connection and panel session/actor thread-local context.
- Modify `bridge/common.py` — import/re-export canonical card config and runtime-context accessors; delete duplicate definitions/state only.
- Create `bridge/panel_utils.py` — pure panel label/pagination/navigation primitives.
- Create `bridge/card_content.py` — canonical card parsing, card/world paths, World Info, System Prompt, macro/system-prompt construction, and `character_display_name`.
- Create `bridge/callback_tokens.py` — canonical callback-token cache, persistence helper, creation, lookup, expiry, and invalidation.
- Modify `bridge/cards.py` — remove migrated implementations and import/re-export canonical foundations while retaining Persona/Telegram UI shell functions.
- Modify `bridge/character_identity.py` — import canonical `character_display_name`; leave rename/reconciliation behavior otherwise intact.
- Modify `bridge/runtime.py` — explicitly pre-import/re-export all moved public foundation APIs.
- Create `tests/test_card_foundations_import_island.py` — standalone imports, ownership, facade identity, source guards, state-sharing, config semantics, callback-cache semantics, and runtime-stage invariants.
- Modify `tests/test_default_character_name.py` — patch canonical `bridge.config.DEFAULT_CHARACTER_FILE` for ordinary `card_fields`.
- Modify `tests/test_world_management.py` only where necessary — synchronize `config.WORLD_DIR` with legacy `rt.WORLD_DIR` because this suite spans legacy catalog file-management code and newly ordinary World Info helpers.
- Modify `tests/test_character_rename.py` only where necessary — synchronize canonical character/card paths with compatibility paths because rename reconciliation remains legacy but character discovery/display becomes ordinary.
- Modify other tests only when exact-head failures prove a test currently patches a runtime-global seam that the approved ordinary ownership intentionally removes.
- Modify `tests/test_runtime_loader.py` — guard that the four new ordinary modules are never exec-loaded and that `cards.py` remains at the same core-stage position.

---

### Task 1: Extract canonical card configuration and runtime context

**Files:**
- Modify: `bridge/config.py`
- Create: `bridge/runtime_context.py`
- Modify: `bridge/common.py`
- Modify: `bridge/runtime.py`
- Create: `tests/test_card_foundations_import_island.py`

**Interfaces:**
- Produces canonical config values:
  - `config.SILLYTAVERN_DIR: Path`
  - `config.CHARACTER_DIR: Path`
  - `config.DEFAULT_CHARACTER_FILE: str`
  - `config.CARD_FILE: Path`
  - `config.WORLD_DIR: Path`
  - `config.SYSTEM_PROMPTS_DIR: Path`
  - `config.SYSTEM_PROMPTS_FILE: str`
  - `config.DEFAULT_USER_NAME: str`
  - `config.CATALOG_MAX_ITEMS: int`
  - `config.CARD_FIELD_MAX_CHARS: int`
  - `config.CARD_TOTAL_MAX_CHARS: int`
- Produces runtime-context API:
  - `set_panel_session_context(session_id: str | None) -> None`
  - `panel_session_context() -> str`
  - `set_panel_actor_context(user_id: str | None) -> None`
  - `panel_actor_context() -> str`
  - `set_db_connection_context(db: sqlite3.Connection | None) -> None`
  - `db_connection_context() -> sqlite3.Connection | None`
- Later tasks consume `bridge.config` and `bridge.runtime_context`.

- [ ] **Step 1: Write RED tests for standalone runtime context, config values, and facade identity**

Create `tests/test_card_foundations_import_island.py`:

```python
"""Phase 7B2 card-foundation ordinary-import boundary tests."""

from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).parents[1]


RUNTIME_CONTEXT_EXPORTS = (
    "set_panel_session_context",
    "panel_session_context",
    "set_panel_actor_context",
    "panel_actor_context",
    "set_db_connection_context",
    "db_connection_context",
)


class CardFoundationsImportIslandTests(unittest.TestCase):
    def _run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_runtime_context_imports_without_runtime_or_common(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.runtime_context as context\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
            "assert context.panel_session_context() == ''\n"
            "assert context.panel_actor_context() == ''\n"
            "assert context.db_connection_context() is None\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_config_exposes_card_foundation_defaults(self):
        import bridge.config as config

        self.assertIsInstance(config.SILLYTAVERN_DIR, Path)
        self.assertIsInstance(config.CHARACTER_DIR, Path)
        self.assertIsInstance(config.CARD_FILE, Path)
        self.assertIsInstance(config.WORLD_DIR, Path)
        self.assertIsInstance(config.SYSTEM_PROMPTS_DIR, Path)
        self.assertIsInstance(config.DEFAULT_CHARACTER_FILE, str)
        self.assertIsInstance(config.SYSTEM_PROMPTS_FILE, str)
        self.assertIsInstance(config.DEFAULT_USER_NAME, str)
        self.assertEqual(config.CATALOG_MAX_ITEMS, 40)
        self.assertEqual(config.CARD_FIELD_MAX_CHARS, 20000)
        self.assertEqual(config.CARD_TOTAL_MAX_CHARS, 60000)

    def test_card_file_remains_startup_derived(self):
        import bridge.config as config

        original_card = config.CARD_FILE
        with patch.object(
            config,
            "DEFAULT_CHARACTER_FILE",
            "temporary.png",
        ):
            self.assertEqual(config.CARD_FILE, original_card)

    def test_runtime_context_facade_exports_canonical_functions(self):
        import bridge.runtime as rt
        import bridge.runtime_context as context

        for name in RUNTIME_CONTEXT_EXPORTS:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(rt, name),
                    getattr(context, name),
                )

    def test_db_context_is_shared_across_runtime_and_module(self):
        import bridge.runtime as rt
        import bridge.runtime_context as context

        first = sqlite3.connect(":memory:")
        second = sqlite3.connect(":memory:")
        try:
            rt.set_db_connection_context(first)
            self.assertIs(context.db_connection_context(), first)
            context.set_db_connection_context(second)
            self.assertIs(rt.db_connection_context(), second)
        finally:
            context.set_db_connection_context(None)
            first.close()
            second.close()

    def test_panel_context_is_shared_across_runtime_and_module(self):
        import bridge.runtime as rt
        import bridge.runtime_context as context

        try:
            rt.set_panel_session_context("session-a")
            self.assertEqual(
                context.panel_session_context(),
                "session-a",
            )
            context.set_panel_actor_context("actor-b")
            self.assertEqual(rt.panel_actor_context(), "actor-b")
        finally:
            context.set_panel_session_context(None)
            context.set_panel_actor_context(None)
```

- [ ] **Step 2: Run the Task 1 RED tests**

Run:

```bash
python -m pytest   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_runtime_context_imports_without_runtime_or_common   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_config_exposes_card_foundation_defaults   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_runtime_context_facade_exports_canonical_functions   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_db_context_is_shared_across_runtime_and_module   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_panel_context_is_shared_across_runtime_and_module   -q
```

Expected: FAIL because `bridge.runtime_context` does not exist, the additional config values are not yet canonical, and runtime still exposes exec-created context functions.

- [ ] **Step 3: Extend `bridge.config` with exactly the approved values**

Add after the existing persistence-related values:

```python
SILLYTAVERN_DIR = Path(
    os.environ.get(
        "SILLYTAVERN_DIR",
        str(BRIDGE_HOME.parent / "SillyTavern"),
    )
)
CHARACTER_DIR = Path(
    os.environ.get(
        "SILLYTAVERN_CHARACTER_DIR",
        str(SILLYTAVERN_DIR / "data/default-user/characters"),
    )
)
DEFAULT_CHARACTER_FILE = os.environ.get(
    "SILLYTAVERN_DEFAULT_CHARACTER",
    "",
).strip()
CARD_FILE = CHARACTER_DIR / DEFAULT_CHARACTER_FILE
WORLD_DIR = Path(
    os.environ.get(
        "SILLYTAVERN_WORLD_DIR",
        str(SILLYTAVERN_DIR / "data/default-user/worlds"),
    )
)
SYSTEM_PROMPTS_DIR = Path(
    os.environ.get(
        "SILLYTAVERN_SYSTEM_PROMPTS_DIR",
        str(SILLYTAVERN_DIR / "data/default-user/sysprompt"),
    )
)
SYSTEM_PROMPTS_FILE = os.environ.get(
    "SILLYTAVERN_SYSTEM_PROMPTS_FILE",
    "",
)
DEFAULT_USER_NAME = os.environ.get(
    "SILLYTAVERN_DEFAULT_USER_NAME",
    "",
).strip()

CATALOG_MAX_ITEMS = 40
CARD_FIELD_MAX_CHARS = 20000
CARD_TOTAL_MAX_CHARS = 60000
```

Do not move `CHARACTER_BACKUP_DIR`, provider/model config, RAG config, sync limits, image limits, Telegram config, or logging config.

- [ ] **Step 4: Create the canonical runtime-context module**

Create `bridge/runtime_context.py`:

```python
"""Canonical ambient runtime context for bridge request handling."""

import sqlite3
import threading


_PANEL_SESSION_CONTEXT = threading.local()
_DB_CONNECTION_CONTEXT = threading.local()


def set_panel_session_context(session_id: str | None) -> None:
    _PANEL_SESSION_CONTEXT.session_id = (
        str(session_id) if session_id else ""
    )


def panel_session_context() -> str:
    return str(
        getattr(_PANEL_SESSION_CONTEXT, "session_id", "") or ""
    )


def set_panel_actor_context(user_id: str | None) -> None:
    _PANEL_SESSION_CONTEXT.user_id = (
        str(user_id) if user_id else ""
    )


def panel_actor_context() -> str:
    return str(
        getattr(_PANEL_SESSION_CONTEXT, "user_id", "") or ""
    )


def set_db_connection_context(
    db: sqlite3.Connection | None,
) -> None:
    _DB_CONNECTION_CONTEXT.connection = db


def db_connection_context() -> sqlite3.Connection | None:
    return getattr(_DB_CONNECTION_CONTEXT, "connection", None)
```

Do not add reload preservation or compatibility globals.

- [ ] **Step 5: Make `common.py` re-export canonical config and context**

Extend its `from bridge.config import (...)` block with:

```python
    CARD_FIELD_MAX_CHARS,
    CARD_FILE,
    CARD_TOTAL_MAX_CHARS,
    CATALOG_MAX_ITEMS,
    CHARACTER_DIR,
    DEFAULT_CHARACTER_FILE,
    DEFAULT_USER_NAME,
    SILLYTAVERN_DIR,
    SYSTEM_PROMPTS_DIR,
    SYSTEM_PROMPTS_FILE,
    WORLD_DIR,
```

Add:

```python
from bridge.runtime_context import (
    db_connection_context,
    panel_actor_context,
    panel_session_context,
    set_db_connection_context,
    set_panel_actor_context,
    set_panel_session_context,
)
```

Delete the local definitions of exactly those eleven config values.

Delete:

```python
_PANEL_SESSION_CONTEXT = threading.local()
_DB_CONNECTION_CONTEXT = threading.local()
```

and delete the six local context function definitions.

Do not delete `threading` itself; `common.py` still uses it extensively for background runtime state.

- [ ] **Step 6: Pre-import canonical runtime-context functions in `runtime.py`**

Before `RUNTIME_LOAD_REPORT`, add:

```python
from bridge.runtime_context import (
    db_connection_context,
    panel_actor_context,
    panel_session_context,
    set_db_connection_context,
    set_panel_actor_context,
    set_panel_session_context,
)
```

Do not import the private thread-local objects.

- [ ] **Step 7: Add source guards for duplicate context/config ownership**

Append to `tests/test_card_foundations_import_island.py`:

```python
    def test_common_no_longer_owns_extracted_context_state(self):
        source = (
            REPO_ROOT / "bridge" / "common.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn(
            "_PANEL_SESSION_CONTEXT = threading.local()",
            source,
        )
        self.assertNotIn(
            "_DB_CONNECTION_CONTEXT = threading.local()",
            source,
        )
        for name in RUNTIME_CONTEXT_EXPORTS:
            self.assertNotIn(f"def {name}(", source)

    def test_common_no_longer_defines_extracted_card_config(self):
        source = (
            REPO_ROOT / "bridge" / "common.py"
        ).read_text(encoding="utf-8")

        for prefix in (
            "SILLYTAVERN_DIR = ",
            "CHARACTER_DIR = ",
            "DEFAULT_CHARACTER_FILE = ",
            "CARD_FILE = ",
            "WORLD_DIR = ",
            "SYSTEM_PROMPTS_DIR = ",
            "SYSTEM_PROMPTS_FILE = ",
            "DEFAULT_USER_NAME = ",
            "CATALOG_MAX_ITEMS = ",
            "CARD_FIELD_MAX_CHARS = ",
            "CARD_TOTAL_MAX_CHARS = ",
        ):
            with self.subTest(prefix=prefix):
                self.assertNotIn("\n" + prefix, source)
```

- [ ] **Step 8: Run Task 1 tests and Phase 7A/7B1 architecture regressions**

Run:

```bash
python -m pytest   tests/test_card_foundations_import_island.py   tests/test_runtime_import_islands.py   tests/test_persistence_import_island.py   tests/test_runtime_loader.py   -q
```

Expected: PASS for Task 1 additions and all prior import-island guards.

- [ ] **Step 9: Run context consumers before committing**

Run:

```bash
python -m pytest   tests/test_panel_lifecycle.py   tests/test_panel_expiry_feedback.py   tests/test_panelification.py   -q
```

Expected: PASS; panel/DB ambient context behavior is unchanged.

- [ ] **Step 10: Commit Task 1**

```bash
git add   bridge/config.py   bridge/runtime_context.py   bridge/common.py   bridge/runtime.py   tests/test_card_foundations_import_island.py
git commit -m "refactor: extract card config and runtime context"
```

---

### Task 2: Extract pure panel primitives and canonical card/content ownership

**Files:**
- Create: `bridge/panel_utils.py`
- Create: `bridge/card_content.py`
- Modify: `bridge/cards.py`
- Modify: `bridge/character_identity.py`
- Modify: `bridge/runtime.py`
- Modify: `tests/test_card_foundations_import_island.py`
- Modify: `tests/test_default_character_name.py`
- Modify: `tests/test_world_management.py` only where mixed legacy/ordinary path patching requires synchronization
- Modify: `tests/test_character_rename.py` only where mixed legacy/ordinary path patching requires synchronization

**Interfaces:**
- Produces pure panel API:
  - `panel_label(value: str, limit: int = 48) -> str`
  - `panel_page(items: list, page: int) -> tuple[list, int, int]`
  - `panel_navigation(prefix: str, page: int, total_pages: int) -> list[dict[str, str]]`
- Produces canonical card-content API:
  - `read_png_chara(path: Path) -> dict`
  - `parse_png_chara_bytes(raw: bytes) -> dict`
  - `card_fields(card: dict) -> dict[str, str]`
  - `character_card_paths() -> list[Path]`
  - `safe_character_path(name: str) -> Path | None`
  - `card_fields_from_file(name: str) -> dict[str, str]`
  - `world_file_paths() -> list[Path]`
  - `safe_world_path(name: str) -> Path | None`
  - `active_world_files(value: str | list[str] | None) -> list[str]`
  - `encode_world_files(names: list[str]) -> str`
  - `build_world_info(world_names: str | list[str], context: str, fields: dict[str, str], user_name: str = config.DEFAULT_USER_NAME) -> str`
  - `load_system_prompts() -> dict[str, dict[str, str]]`
  - `get_system_prompt_choice(name: str) -> str | None`
  - `system_prompt_label(prompt: str | None) -> str`
  - `system_prompt_callback_token(key: str) -> str`
  - `system_prompt_choices() -> list[tuple[str, str]]`
  - `replace_macros(text: str, fields: dict[str, str], user_name: str = config.DEFAULT_USER_NAME) -> str`
  - `build_system_prompt(fields: dict[str, str], user_name: str = config.DEFAULT_USER_NAME) -> str`
  - `character_display_name(path: Path) -> str`
- Leaves `cards.py` exec-loaded with only Persona compatibility and Telegram/panel menu rendering plus canonical imports.

- [ ] **Step 1: Add RED standalone-import, facade, and source-ownership tests**

Extend `tests/test_card_foundations_import_island.py` with:

```python
PANEL_UTIL_EXPORTS = (
    "panel_label",
    "panel_page",
    "panel_navigation",
)

CARD_CONTENT_EXPORTS = (
    "read_png_chara",
    "parse_png_chara_bytes",
    "card_fields",
    "character_card_paths",
    "safe_character_path",
    "card_fields_from_file",
    "world_file_paths",
    "safe_world_path",
    "active_world_files",
    "encode_world_files",
    "build_world_info",
    "load_system_prompts",
    "get_system_prompt_choice",
    "system_prompt_label",
    "system_prompt_callback_token",
    "system_prompt_choices",
    "replace_macros",
    "build_system_prompt",
    "character_display_name",
)


class CardFoundationsImportIslandTests(unittest.TestCase):
    # keep existing methods

    def test_panel_utils_imports_without_runtime_or_common(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.panel_utils as panel_utils\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
            "assert panel_utils.panel_label('abcdef', 4) == 'abc…'\n"
            "assert panel_utils.panel_page(list(range(9)), 0)[2] == 2\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_card_content_imports_without_legacy_or_persistence_modules(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.card_content\n"
            "for name in ("
            "'bridge.runtime', 'bridge.common', 'bridge.database', "
            "'bridge.telegram', 'bridge.persona_sync'"
            "):\n"
            "    assert name not in sys.modules, name\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_runtime_facade_exports_canonical_panel_utils(self):
        import bridge.panel_utils as panel_utils
        import bridge.runtime as rt

        for name in PANEL_UTIL_EXPORTS:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(rt, name),
                    getattr(panel_utils, name),
                )

    def test_runtime_facade_exports_complete_canonical_card_content_api(self):
        import bridge.card_content as card_content
        import bridge.runtime as rt

        for name in CARD_CONTENT_EXPORTS:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(rt, name),
                    getattr(card_content, name),
                )

    def test_cards_shell_does_not_redefine_migrated_content_or_panel_functions(self):
        source = (
            REPO_ROOT / "bridge" / "cards.py"
        ).read_text(encoding="utf-8")

        for name in (*PANEL_UTIL_EXPORTS, *CARD_CONTENT_EXPORTS):
            with self.subTest(name=name):
                self.assertNotIn(f"def {name}(", source)

    def test_character_identity_imports_character_display_name(self):
        source = (
            REPO_ROOT / "bridge" / "character_identity.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "from bridge.card_content import character_display_name",
            source,
        )
        self.assertNotIn(
            "def character_display_name(",
            source,
        )
```

- [ ] **Step 2: Add RED canonical-config behavior tests**

Append:

```python
    def test_card_fields_default_name_follows_canonical_config(self):
        import bridge.card_content as card_content
        import bridge.config as config

        with patch.object(
            config,
            "DEFAULT_CHARACTER_FILE",
            "Seraphina.png",
        ):
            fields = card_content.card_fields(
                {"data": {"name": ""}}
            )
        self.assertEqual(fields["name"], "Seraphina")

    def test_character_and_world_paths_follow_canonical_config(self):
        import bridge.card_content as card_content
        import bridge.config as config

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            characters = root / "characters"
            worlds = root / "worlds"
            characters.mkdir()
            worlds.mkdir()
            (characters / "one.png").write_bytes(b"not parsed here")
            (worlds / "lore.json").write_text(
                "{}",
                encoding="utf-8",
            )

            with (
                patch.object(config, "CHARACTER_DIR", characters),
                patch.object(config, "WORLD_DIR", worlds),
            ):
                self.assertEqual(
                    [path.name for path in card_content.character_card_paths()],
                    ["one.png"],
                )
                self.assertEqual(
                    [path.name for path in card_content.world_file_paths()],
                    ["lore.json"],
                )
                self.assertEqual(
                    card_content.safe_character_path("one.png"),
                    characters / "one.png",
                )
                self.assertEqual(
                    card_content.safe_world_path("lore.json"),
                    worlds / "lore.json",
                )
```

    def test_deterministic_system_prompt_uses_canonical_text_cache(self):
        import bridge.card_content as card_content

        fields = {
            "system_prompt": "Hello {{char}}",
            "description": "",
            "personality": "",
            "scenario": "",
            "mes_example": "",
            "name": "Mira",
        }
        calls = []

        def fake_cached_text(key, builder):
            calls.append(key)
            return builder()

        with patch.object(
            card_content,
            "cached_text",
            side_effect=fake_cached_text,
        ):
            result = card_content.build_system_prompt(
                fields,
                "User",
            )

        self.assertEqual(result, "Hello Mira")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("system-prompt:"))

    def test_dynamic_system_prompt_macros_bypass_text_cache(self):
        import bridge.card_content as card_content

        fields = {
            "system_prompt": "{{time}}",
            "description": "",
            "personality": "",
            "scenario": "",
            "mes_example": "",
            "name": "Mira",
        }
        with patch.object(
            card_content,
            "cached_text",
            side_effect=AssertionError("dynamic prompt used cache"),
        ):
            result = card_content.build_system_prompt(
                fields,
                "User",
            )

        self.assertRegex(result, r"^\\d{2}:\\d{2}$")


- [ ] **Step 3: Run Task 2 tests and verify RED**

Run:

```bash
python -m pytest   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_panel_utils_imports_without_runtime_or_common   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_card_content_imports_without_legacy_or_persistence_modules   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_runtime_facade_exports_canonical_panel_utils   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_runtime_facade_exports_complete_canonical_card_content_api   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_cards_shell_does_not_redefine_migrated_content_or_panel_functions   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_character_identity_imports_character_display_name   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_card_fields_default_name_follows_canonical_config   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_character_and_world_paths_follow_canonical_config   -q
```

Expected: FAIL because the two ordinary modules do not exist and implementations still live in exec-loaded files.

- [ ] **Step 4: Create pure `bridge/panel_utils.py`**

Create:

```python
"""Pure panel label, pagination, and navigation helpers."""

PANEL_PAGE_SIZE = 8


def panel_label(value: str, limit: int = 48) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def panel_page(items: list, page: int) -> tuple[list, int, int]:
    total_pages = max(
        1,
        (len(items) + PANEL_PAGE_SIZE - 1)
        // PANEL_PAGE_SIZE,
    )
    current_page = min(
        max(int(page), 0),
        total_pages - 1,
    )
    start = current_page * PANEL_PAGE_SIZE
    return (
        items[start:start + PANEL_PAGE_SIZE],
        current_page,
        total_pages,
    )


def panel_navigation(
    prefix: str,
    page: int,
    total_pages: int,
) -> list[dict[str, str]]:
    if total_pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(
            {
                "text": "⬅️ Previous",
                "callback_data": f"{prefix}:page:{page - 1}",
            }
        )
    if page < total_pages - 1:
        row.append(
            {
                "text": "Next ➡️",
                "callback_data": f"{prefix}:page:{page + 1}",
            }
        )
    return row
```

Preserve `PANEL_PAGE_SIZE = 8` exactly.

- [ ] **Step 5: Create `bridge/card_content.py` with explicit ordinary dependencies**

Use this import block:

```python
"""Character-card, World Info, and System Prompt content helpers."""

from pathlib import Path
import base64
import hashlib
import json
import logging
import random
import re
import struct
import time

from bridge import config as _config
from bridge.native_cache import (
    cached_json,
    cached_png_metadata,
    cached_text,
)
from bridge.panel_utils import panel_label
```

Move the current implementations from `cards.py` unchanged except for the exact global ownership substitutions below.

Use these config substitutions:

```text
DEFAULT_CHARACTER_FILE
    -> _config.DEFAULT_CHARACTER_FILE

CARD_FIELD_MAX_CHARS
    -> _config.CARD_FIELD_MAX_CHARS

CARD_TOTAL_MAX_CHARS
    -> _config.CARD_TOTAL_MAX_CHARS

CHARACTER_DIR
    -> _config.CHARACTER_DIR

CARD_FILE
    -> _config.CARD_FILE

WORLD_DIR
    -> _config.WORLD_DIR

SYSTEM_PROMPTS_DIR
    -> _config.SYSTEM_PROMPTS_DIR

SYSTEM_PROMPTS_FILE
    -> _config.SYSTEM_PROMPTS_FILE

CATALOG_MAX_ITEMS
    -> _config.CATALOG_MAX_ITEMS
```

For existing default parameters, bind startup user-name semantics explicitly:

```python
def build_world_info(
    world_names: str | list[str],
    context: str,
    fields: dict[str, str],
    user_name: str = _config.DEFAULT_USER_NAME,
) -> str:
    ...
```

```python
def replace_macros(
    text: str,
    fields: dict[str, str],
    user_name: str = _config.DEFAULT_USER_NAME,
) -> str:
    ...
```

```python
def build_system_prompt(
    fields: dict[str, str],
    user_name: str = _config.DEFAULT_USER_NAME,
) -> str:
    ...
```

```python
def _build_system_prompt_uncached(
    fields: dict[str, str],
    user_name: str = _config.DEFAULT_USER_NAME,
) -> str:
    ...
```

Move the exact existing bodies for these functions/private helpers:

```text
read_png_chara
parse_png_chara_bytes
_default_character_name
card_fields
character_card_paths
safe_character_path
card_fields_from_file
world_file_paths
safe_world_path
active_world_files
encode_world_files
build_world_info
_prompt_catalog_label
_merge_system_prompt_file
_merge_system_prompt_json
_merge_system_prompt_text
load_system_prompts
get_system_prompt_choice
system_prompt_label
system_prompt_callback_token
system_prompt_choices
replace_macros
build_system_prompt
_build_system_prompt_uncached
```

Then move `character_display_name` from `character_identity.py` into `card_content.py` unchanged except that it naturally calls the local canonical `card_fields` and `read_png_chara`:

```python
def character_display_name(path: Path) -> str:
    """Return the embedded card name with a safe filename fallback."""
    try:
        return str(
            card_fields(read_png_chara(path)).get("name")
            or path.stem
        )
    except Exception:
        return path.stem
```

Do not import database, Telegram, Persona, runtime, or common.

- [ ] **Step 6: Convert `cards.py` into a canonical-foundation consumer**

At the top of `cards.py`, add explicit imports:

```python
"""Persona and Telegram-facing card/panel compatibility shell.

Content, callback-token state, and pure panel helpers live in ordinary
modules. This file remains exec-loaded until the Phase 7 UI migration.
"""

from pathlib import Path
import logging

from bridge import config as _config
from bridge.card_content import (
    active_world_files,
    build_system_prompt,
    build_world_info,
    card_fields,
    card_fields_from_file,
    character_card_paths,
    character_display_name,
    encode_world_files,
    get_system_prompt_choice,
    load_system_prompts,
    parse_png_chara_bytes,
    read_png_chara,
    replace_macros,
    safe_character_path,
    safe_world_path,
    system_prompt_callback_token,
    system_prompt_choices,
    system_prompt_label,
    world_file_paths,
)
from bridge.panel_utils import (
    panel_label,
    panel_navigation,
    panel_page,
)
```

Delete the moved function definitions and their private helper definitions from `cards.py`.

Keep these Persona/UI definitions in `cards.py`:

```text
get_persona
default_persona_id
persona_name
send_panel_message
send_persona_menu
send_character_menu
send_character_info_menu
send_character_delete_menu
send_character_delete_confirm
send_session_menu
```

Where the residual menu shell itself directly needs card config, replace bare globals with canonical reads:

```text
CATALOG_MAX_ITEMS
    -> _config.CATALOG_MAX_ITEMS

DEFAULT_CHARACTER_FILE
    -> _config.DEFAULT_CHARACTER_FILE

CARD_FILE
    -> _config.CARD_FILE
```

Do not bind `telegram_request`, `resolve_persona_service`, `load_personas`, or `_native_settings` via ordinary imports in this phase; they remain late-bound runtime collaborators.

- [ ] **Step 7: Make `character_identity.py` consume canonical display-name ownership**

Add:

```python
from bridge.card_content import character_display_name
```

Delete only the local `character_display_name` definition.

Keep `hashlib`, `logging`, `Path`, `sqlite3`, and `struct` imports required by the remaining identity implementation.

- [ ] **Step 8: Pre-import panel/card-content public functions in `runtime.py`**

Add explicit imports before runtime loading:

```python
from bridge.card_content import (
    active_world_files,
    build_system_prompt,
    build_world_info,
    card_fields,
    card_fields_from_file,
    character_card_paths,
    character_display_name,
    encode_world_files,
    get_system_prompt_choice,
    load_system_prompts,
    parse_png_chara_bytes,
    read_png_chara,
    replace_macros,
    safe_character_path,
    safe_world_path,
    system_prompt_callback_token,
    system_prompt_choices,
    system_prompt_label,
    world_file_paths,
)
from bridge.panel_utils import (
    panel_label,
    panel_navigation,
    panel_page,
)
```

Do not import `PANEL_PAGE_SIZE` into the runtime facade.

- [ ] **Step 9: Retarget default-character-name tests to canonical config**

Modify `tests/test_default_character_name.py`:

```python
from pathlib import Path
import unittest

import bridge.config as config
import bridge.runtime as rt
```

Change the two tests that currently rebind `rt.DEFAULT_CHARACTER_FILE` to patch `config.DEFAULT_CHARACTER_FILE` instead:

```python
    def test_fallback_name_follows_configured_default_card(self):
        original = config.DEFAULT_CHARACTER_FILE
        config.DEFAULT_CHARACTER_FILE = "Seraphina.png"
        try:
            fields = rt.card_fields(
                {"data": {"name": ""}}
            )
            self.assertEqual(fields["name"], "Seraphina")
        finally:
            config.DEFAULT_CHARACTER_FILE = original

    def test_fallback_name_last_resort_for_empty_config(self):
        original = config.DEFAULT_CHARACTER_FILE
        config.DEFAULT_CHARACTER_FILE = ""
        try:
            fields = rt.card_fields(
                {"data": {"name": ""}}
            )
            self.assertEqual(fields["name"], "Character")
        finally:
            config.DEFAULT_CHARACTER_FILE = original
```

Use `config.DEFAULT_CHARACTER_FILE` for the expected default stem in the remaining fallback test.

- [ ] **Step 10: Synchronize World Info path patches only where one test crosses legacy and ordinary code**

In `tests/test_world_management.py`, preserve the legacy `rt.WORLD_DIR` patch because `install_world_info_document` and `delete_world_info_file` remain in legacy code, but also patch the canonical owner used by `safe_world_path` / `active_world_files`:

```python
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_world = rt.WORLD_DIR
        self.old_config_world = config.WORLD_DIR
        self.old_db = config.DB_FILE

        world_dir = root / "worlds"
        world_dir.mkdir()
        rt.WORLD_DIR = world_dir
        config.WORLD_DIR = world_dir
        config.DB_FILE = root / "bridge.sqlite3"

        self.db = rt.db_connect()
        self.session = rt.ensure_session(
            self.db,
            "chat",
            rt.DEFAULT_MODEL,
        )

    def tearDown(self):
        self.db.close()
        rt.WORLD_DIR = self.old_world
        config.WORLD_DIR = self.old_config_world
        config.DB_FILE = self.old_db
        self.tmp.cleanup()
```

Do not convert unrelated legacy test seams in this suite.

- [ ] **Step 11: Synchronize character/card paths in rename tests where the flow spans legacy identity code and ordinary card discovery**

In `tests/test_character_rename.py`, add/save canonical values:

```python
self.old_config_character_dir = config.CHARACTER_DIR
self.old_config_card_file = config.CARD_FILE
```

When the test assigns its temporary character directory/card file, assign both compatibility and canonical owners:

```python
rt.CHARACTER_DIR = character_dir
config.CHARACTER_DIR = character_dir

rt.CARD_FILE = card_file
config.CARD_FILE = card_file
```

Restore both in `tearDown`.

Keep `CHARACTER_BACKUP_DIR` on `rt`; that owner intentionally remains legacy in 7B2.

- [ ] **Step 12: Run focused Task 2 architecture and behavior tests**

Run:

```bash
python -m pytest   tests/test_card_foundations_import_island.py   tests/test_default_character_name.py   tests/test_alternate_greetings.py   tests/test_character_rename.py   tests/test_world_management.py   tests/test_panelification.py   -q
```

Expected: PASS.

- [ ] **Step 13: Run native-cache and prompt/card-adjacent regressions**

Run:

```bash
python -m pytest   tests/test_runtime_import_islands.py   tests/test_catalog_panel.py   tests/test_item_panel_layouts.py   -q
```

Expected: PASS. Any failure caused by a test rebinding an intentionally migrated runtime path is fixed by targeting the canonical owner or, for mixed legacy/ordinary flows, synchronizing both owners. Do not change production ownership to satisfy an obsolete test seam.

- [ ] **Step 14: Commit Task 2**

```bash
git add   bridge/panel_utils.py   bridge/card_content.py   bridge/cards.py   bridge/character_identity.py   bridge/runtime.py   tests/test_card_foundations_import_island.py   tests/test_default_character_name.py   tests/test_world_management.py   tests/test_character_rename.py
git commit -m "refactor: extract canonical card content foundations"
```

---

### Task 3: Extract callback-token state and persistence into one ordinary owner

**Files:**
- Create: `bridge/callback_tokens.py`
- Modify: `bridge/cards.py`
- Modify: `bridge/runtime.py`
- Modify: `tests/test_card_foundations_import_island.py`

**Interfaces:**
- Consumes:
  - `bridge.database.db_connect(database_path: Path | None = None) -> sqlite3.Connection`
  - `bridge.runtime_context.db_connection_context() -> sqlite3.Connection | None`
- Produces:
  - `dynamic_callback_token(kind: str, value: str, chat_id: str = "", db=None) -> str`
  - `resolve_dynamic_callback_token(token: str, kind: str, chat_id: str = "") -> str | None`
  - private canonical `_CALLBACK_TOKEN_VALUES: dict[str, tuple[str, str, str, float]]`
  - private canonical `_CALLBACK_TOKEN_TTL_SECONDS = 900`

- [ ] **Step 1: Add RED callback-token ownership and behavior tests**

Extend `tests/test_card_foundations_import_island.py`:

```python
CALLBACK_TOKEN_EXPORTS = (
    "dynamic_callback_token",
    "resolve_dynamic_callback_token",
)


class CardFoundationsImportIslandTests(unittest.TestCase):
    # keep existing methods

    def test_callback_tokens_imports_without_runtime_or_common(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.callback_tokens as callback_tokens\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.common' not in sys.modules\n"
            "assert callback_tokens._CALLBACK_TOKEN_TTL_SECONDS == 900\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_runtime_facade_exports_canonical_callback_token_functions(self):
        import bridge.callback_tokens as callback_tokens
        import bridge.runtime as rt

        for name in CALLBACK_TOKEN_EXPORTS:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(rt, name),
                    getattr(callback_tokens, name),
                )

    def test_runtime_and_module_share_one_callback_cache(self):
        import bridge.callback_tokens as callback_tokens
        import bridge.runtime as rt

        callback_tokens._CALLBACK_TOKEN_VALUES.clear()
        db = sqlite3.connect(":memory:")
        try:
            db.execute(
                "CREATE TABLE callback_tokens("
                "token TEXT PRIMARY KEY,"
                "kind TEXT NOT NULL,"
                "value TEXT NOT NULL,"
                "chat_id TEXT NOT NULL,"
                "expires_at REAL NOT NULL)"
            )

            token = rt.dynamic_callback_token(
                "character",
                "mira.png",
                "chat",
                db=db,
            )
            self.assertEqual(
                callback_tokens.resolve_dynamic_callback_token(
                    token,
                    "character",
                    "chat",
                ),
                "mira.png",
            )

            callback_tokens._CALLBACK_TOKEN_VALUES.clear()
            token2 = callback_tokens.dynamic_callback_token(
                "world",
                "lore.json",
                "chat",
                db=db,
            )
            self.assertEqual(
                rt.resolve_dynamic_callback_token(
                    token2,
                    "world",
                    "chat",
                ),
                "lore.json",
            )
        finally:
            callback_tokens._CALLBACK_TOKEN_VALUES.clear()
            db.close()

    def test_callback_token_persistent_fallback_restores_cache(self):
        import bridge.callback_tokens as callback_tokens
        import bridge.runtime_context as runtime_context

        callback_tokens._CALLBACK_TOKEN_VALUES.clear()
        db = sqlite3.connect(":memory:")
        try:
            db.execute(
                "CREATE TABLE callback_tokens("
                "token TEXT PRIMARY KEY,"
                "kind TEXT NOT NULL,"
                "value TEXT NOT NULL,"
                "chat_id TEXT NOT NULL,"
                "expires_at REAL NOT NULL)"
            )
            runtime_context.set_db_connection_context(db)
            token = callback_tokens.dynamic_callback_token(
                "persona",
                "p1",
                "chat",
                db=db,
            )
            callback_tokens._CALLBACK_TOKEN_VALUES.clear()

            self.assertEqual(
                callback_tokens.resolve_dynamic_callback_token(
                    token,
                    "persona",
                    "chat",
                ),
                "p1",
            )
            self.assertIn(
                token,
                callback_tokens._CALLBACK_TOKEN_VALUES,
            )
        finally:
            runtime_context.set_db_connection_context(None)
            callback_tokens._CALLBACK_TOKEN_VALUES.clear()
            db.close()

    def test_callback_token_expiry_invalidates_memory_and_database(self):
        import bridge.callback_tokens as callback_tokens
        import bridge.runtime_context as runtime_context

        db = sqlite3.connect(":memory:")
        try:
            db.execute(
                "CREATE TABLE callback_tokens("
                "token TEXT PRIMARY KEY,"
                "kind TEXT NOT NULL,"
                "value TEXT NOT NULL,"
                "chat_id TEXT NOT NULL,"
                "expires_at REAL NOT NULL)"
            )
            runtime_context.set_db_connection_context(db)
            token = "texpired"
            callback_tokens._CALLBACK_TOKEN_VALUES[token] = (
                "world",
                "lore.json",
                "chat",
                0.0,
            )
            db.execute(
                "INSERT INTO callback_tokens("
                "token,kind,value,chat_id,expires_at"
                ") VALUES(?,?,?,?,?)",
                (
                    token,
                    "world",
                    "lore.json",
                    "chat",
                    0.0,
                ),
            )
            db.commit()

            self.assertIsNone(
                callback_tokens.resolve_dynamic_callback_token(
                    token,
                    "world",
                    "chat",
                )
            )
            self.assertNotIn(
                token,
                callback_tokens._CALLBACK_TOKEN_VALUES,
            )
            self.assertIsNone(
                db.execute(
                    "SELECT 1 FROM callback_tokens WHERE token=?",
                    (token,),
                ).fetchone()
            )
        finally:
            runtime_context.set_db_connection_context(None)
            callback_tokens._CALLBACK_TOKEN_VALUES.clear()
            db.close()

    def test_runtime_does_not_republish_private_callback_cache(self):
        import bridge.runtime as rt

        self.assertFalse(hasattr(rt, "_CALLBACK_TOKEN_VALUES"))
        self.assertFalse(
            hasattr(rt, "_CALLBACK_TOKEN_TTL_SECONDS")
        )

    def test_cards_shell_does_not_define_callback_token_state_or_functions(self):
        source = (
            REPO_ROOT / "bridge" / "cards.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("_CALLBACK_TOKEN_VALUES", source)
        self.assertNotIn("_CALLBACK_TOKEN_TTL_SECONDS", source)
        self.assertNotIn("def dynamic_callback_token(", source)
        self.assertNotIn(
            "def resolve_dynamic_callback_token(",
            source,
        )
```

    def test_callback_token_format_remains_stable(self):
        import hashlib
        import bridge.callback_tokens as callback_tokens

        db = sqlite3.connect(":memory:")
        try:
            db.execute(
                "CREATE TABLE callback_tokens("
                "token TEXT PRIMARY KEY,"
                "kind TEXT NOT NULL,"
                "value TEXT NOT NULL,"
                "chat_id TEXT NOT NULL,"
                "expires_at REAL NOT NULL)"
            )
            token = callback_tokens.dynamic_callback_token(
                "character",
                "mira.png",
                "chat",
                db=db,
            )
        finally:
            callback_tokens._CALLBACK_TOKEN_VALUES.clear()
            db.close()

        expected = (
            "t"
            + hashlib.sha256(
                b"character|chat|mira.png"
            ).hexdigest()[:16]
        )
        self.assertEqual(token, expected)

    def test_callback_token_scope_mismatch_invalidates_cache_and_database(self):
        import bridge.callback_tokens as callback_tokens
        import bridge.runtime_context as runtime_context

        db = sqlite3.connect(":memory:")
        try:
            db.execute(
                "CREATE TABLE callback_tokens("
                "token TEXT PRIMARY KEY,"
                "kind TEXT NOT NULL,"
                "value TEXT NOT NULL,"
                "chat_id TEXT NOT NULL,"
                "expires_at REAL NOT NULL)"
            )
            runtime_context.set_db_connection_context(db)
            token = callback_tokens.dynamic_callback_token(
                "world",
                "lore.json",
                "chat-a",
                db=db,
            )

            self.assertIsNone(
                callback_tokens.resolve_dynamic_callback_token(
                    token,
                    "world",
                    "chat-b",
                )
            )
            self.assertNotIn(
                token,
                callback_tokens._CALLBACK_TOKEN_VALUES,
            )
            self.assertIsNone(
                db.execute(
                    "SELECT 1 FROM callback_tokens WHERE token=?",
                    (token,),
                ).fetchone()
            )
        finally:
            runtime_context.set_db_connection_context(None)
            callback_tokens._CALLBACK_TOKEN_VALUES.clear()
            db.close()


- [ ] **Step 2: Run Task 3 tests and verify RED**

Run:

```bash
python -m pytest   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_callback_tokens_imports_without_runtime_or_common   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_runtime_facade_exports_canonical_callback_token_functions   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_runtime_and_module_share_one_callback_cache   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_callback_token_persistent_fallback_restores_cache   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_callback_token_expiry_invalidates_memory_and_database   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_runtime_does_not_republish_private_callback_cache   tests/test_card_foundations_import_island.py::CardFoundationsImportIslandTests::test_cards_shell_does_not_define_callback_token_state_or_functions   -q
```

Expected: FAIL because callback-token implementation/state still belongs to exec-loaded `cards.py`.

- [ ] **Step 3: Create `bridge/callback_tokens.py` with the existing behavior**

Create:

```python
"""Canonical callback-token cache and persistence helpers."""

import hashlib
import logging
import time

from bridge.database import db_connect
from bridge.runtime_context import db_connection_context


_CALLBACK_TOKEN_VALUES: dict[
    str,
    tuple[str, str, str, float],
] = {}
_CALLBACK_TOKEN_TTL_SECONDS = 900


def _use_db_connection(action, error_message: str):
    """Run action(conn) on ambient/short-lived DB; log failures."""
    conn = db_connection_context()
    owns_connection = conn is None
    try:
        conn = conn or db_connect()
        return action(conn)
    except Exception:
        logging.debug(error_message, exc_info=True)
        return None
    finally:
        if owns_connection and conn is not None:
            conn.close()


def dynamic_callback_token(
    kind: str,
    value: str,
    chat_id: str = "",
    db=None,
) -> str:
    raw = f"{kind}|{chat_id}|{value}"
    token = (
        "t"
        + hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()[:16]
    )
    expires_at = time.time() + _CALLBACK_TOKEN_TTL_SECONDS
    _CALLBACK_TOKEN_VALUES[token] = (
        str(kind),
        str(value),
        str(chat_id),
        expires_at,
    )

    def persist(conn):
        conn.execute(
            "INSERT OR REPLACE INTO callback_tokens("
            "token,kind,value,chat_id,expires_at"
            ") VALUES(?,?,?,?,?)",
            (
                token,
                str(kind),
                str(value),
                str(chat_id),
                expires_at,
            ),
        )
        conn.commit()

    if db is not None:
        try:
            persist(db)
        except Exception:
            logging.debug(
                "Could not persist callback token",
                exc_info=True,
            )
    else:
        _use_db_connection(
            persist,
            "Could not persist callback token",
        )
    return token


def resolve_dynamic_callback_token(
    token: str,
    kind: str,
    chat_id: str = "",
) -> str | None:
    item = _CALLBACK_TOKEN_VALUES.get(str(token))
    if item is None:

        def load(conn):
            row = conn.execute(
                "SELECT kind,value,chat_id,expires_at "
                "FROM callback_tokens WHERE token=?",
                (str(token),),
            ).fetchone()
            if row:
                found = (
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    float(row[3]),
                )
                _CALLBACK_TOKEN_VALUES[str(token)] = found
                return found
            return None

        item = _use_db_connection(
            load,
            "Could not load callback token",
        )

    if item is None:
        return None

    (
        stored_kind,
        value,
        stored_chat_id,
        expires_at,
    ) = item

    if (
        expires_at < time.time()
        or stored_kind != str(kind)
        or (
            stored_chat_id
            and stored_chat_id != str(chat_id)
        )
    ):
        _CALLBACK_TOKEN_VALUES.pop(str(token), None)

        def forget(conn):
            conn.execute(
                "DELETE FROM callback_tokens WHERE token=?",
                (str(token),),
            )
            conn.commit()

        _use_db_connection(
            forget,
            "Could not remove expired callback token",
        )
        return None

    return value
```

Keep the behavior semantically identical to the source moved from `cards.py`.

- [ ] **Step 4: Make `cards.py` import canonical callback-token functions**

Add:

```python
from bridge.callback_tokens import (
    dynamic_callback_token,
    resolve_dynamic_callback_token,
)
```

Delete:

```text
_use_db_connection
_CALLBACK_TOKEN_VALUES
_CALLBACK_TOKEN_TTL_SECONDS
dynamic_callback_token
resolve_dynamic_callback_token
```

from `cards.py`.

Do not add any callback cache alias to the shell.

- [ ] **Step 5: Pre-import callback-token functions in `runtime.py`**

Add:

```python
from bridge.callback_tokens import (
    dynamic_callback_token,
    resolve_dynamic_callback_token,
)
```

Do not import the private cache or TTL.

- [ ] **Step 6: Run callback-token and panel/menu regressions**

Run:

```bash
python -m pytest   tests/test_card_foundations_import_island.py   tests/test_item_panel_layouts.py   tests/test_panelification.py   tests/test_panel_lifecycle.py   tests/test_panel_expiry_feedback.py   tests/test_session_command_routing.py   -q
```

Expected: PASS.

If an existing test monkeypatches `rt.dynamic_callback_token` specifically to control a still-legacy menu function, preserve that test seam: exec-created menu functions resolve their globals from the runtime namespace at call time. Do not rewrite the residual shell to bind the ordinary function in a default argument or closure.

- [ ] **Step 7: Commit Task 3**

```bash
git add   bridge/callback_tokens.py   bridge/cards.py   bridge/runtime.py   tests/test_card_foundations_import_island.py
git commit -m "refactor: extract canonical callback token state"
```

---

### Task 4: Lock permanent ownership, runtime-stage invariants, and full behavior verification

**Files:**
- Modify: `tests/test_card_foundations_import_island.py`
- Modify: `tests/test_runtime_loader.py`
- No production changes expected unless a verified regression exposes an implementation defect.

**Interfaces:**
- Consumes the canonical modules from Tasks 1–3.
- Produces permanent import-order, state-ownership, source-ownership, stage-boundary, and behavior verification evidence.

- [ ] **Step 1: Add runtime-stage and residual-shell guards**

Append to `tests/test_card_foundations_import_island.py`:

```python
    def test_phase_7b2_foundations_are_never_exec_loaded(self):
        from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

        loaded = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertTrue(
            {
                "runtime_context.py",
                "panel_utils.py",
                "card_content.py",
                "callback_tokens.py",
            }.isdisjoint(loaded)
        )

    def test_cards_shell_remains_exec_loaded(self):
        from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

        core = next(
            stage
            for stage in DEFAULT_RUNTIME_STAGES
            if stage.name == "core"
        )
        self.assertIn("cards.py", core.modules)
        self.assertEqual(
            core.modules[:4],
            (
                "common.py",
                "cards.py",
                "memory.py",
                "rag.py",
            ),
        )
```

- [ ] **Step 2: Add both import-order proofs**

Append:

```python
    def test_card_foundations_before_runtime_keep_canonical_identity(self):
        completed = self._run_python(
            "import bridge.runtime_context as context\n"
            "import bridge.panel_utils as panel_utils\n"
            "import bridge.card_content as card_content\n"
            "import bridge.callback_tokens as callback_tokens\n"
            "import bridge.runtime as rt\n"
            "assert rt.db_connection_context is context.db_connection_context\n"
            "assert rt.panel_page is panel_utils.panel_page\n"
            "assert rt.card_fields is card_content.card_fields\n"
            "assert rt.character_display_name is card_content.character_display_name\n"
            "assert rt.dynamic_callback_token is callback_tokens.dynamic_callback_token\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_runtime_before_card_foundations_keeps_canonical_identity(self):
        completed = self._run_python(
            "import bridge.runtime as rt\n"
            "import bridge.runtime_context as context\n"
            "import bridge.panel_utils as panel_utils\n"
            "import bridge.card_content as card_content\n"
            "import bridge.callback_tokens as callback_tokens\n"
            "assert rt.db_connection_context is context.db_connection_context\n"
            "assert rt.panel_page is panel_utils.panel_page\n"
            "assert rt.card_fields is card_content.card_fields\n"
            "assert rt.character_display_name is card_content.character_display_name\n"
            "assert rt.dynamic_callback_token is callback_tokens.dynamic_callback_token\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
```

- [ ] **Step 3: Add exact single-owner state tests**

Append:

```python
    def test_runtime_context_functions_resolve_canonical_threadlocals(self):
        import bridge.runtime_context as context

        self.assertIs(
            context.db_connection_context.__globals__["_DB_CONNECTION_CONTEXT"],
            context._DB_CONNECTION_CONTEXT,
        )
        self.assertIs(
            context.panel_session_context.__globals__["_PANEL_SESSION_CONTEXT"],
            context._PANEL_SESSION_CONTEXT,
        )

    def test_callback_functions_resolve_canonical_cache(self):
        import bridge.callback_tokens as callback_tokens

        self.assertIs(
            callback_tokens.dynamic_callback_token.__globals__["_CALLBACK_TOKEN_VALUES"],
            callback_tokens._CALLBACK_TOKEN_VALUES,
        )
        self.assertIs(
            callback_tokens.resolve_dynamic_callback_token.__globals__["_CALLBACK_TOKEN_VALUES"],
            callback_tokens._CALLBACK_TOKEN_VALUES,
        )
```

- [ ] **Step 4: Add source dependency guards for all newly ordinary modules**

Append:

```python
    def test_phase_7b2_ordinary_modules_do_not_import_runtime_or_common(self):
        for filename in (
            "runtime_context.py",
            "panel_utils.py",
            "card_content.py",
            "callback_tokens.py",
        ):
            with self.subTest(filename=filename):
                source = (
                    REPO_ROOT / "bridge" / filename
                ).read_text(encoding="utf-8")
                self.assertNotIn("import bridge.runtime", source)
                self.assertNotIn(
                    "from bridge.runtime import",
                    source,
                )
                self.assertNotIn("import bridge.common", source)
                self.assertNotIn(
                    "from bridge.common import",
                    source,
                )

    def test_card_content_has_no_database_telegram_or_persona_dependency(self):
        source = (
            REPO_ROOT / "bridge" / "card_content.py"
        ).read_text(encoding="utf-8")

        for forbidden in (
            "bridge.database",
            "bridge.telegram",
            "bridge.persona_sync",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
```

    def test_cards_shell_keeps_telegram_and_persona_collaborators_late_bound(self):
        source = (
            REPO_ROOT / "bridge" / "cards.py"
        ).read_text(encoding="utf-8")

        for forbidden in (
            "import bridge.telegram",
            "from bridge.telegram import",
            "import bridge.persona_sync",
            "from bridge.persona_sync import",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

        for collaborator in (
            "telegram_request",
            "resolve_persona_service",
            "load_personas",
            "_native_settings",
        ):
            with self.subTest(collaborator=collaborator):
                self.assertIn(collaborator, source)


- [ ] **Step 5: Add exact core-order guard to `tests/test_runtime_loader.py`**

Add a Phase 7B2 test that retains the full Phase 7B1 tuple exactly:

```python
    def test_phase_7b2_preserves_legacy_core_order_and_cards_shell(self):
        core = next(
            stage
            for stage in DEFAULT_RUNTIME_STAGES
            if stage.name == "core"
        )
        self.assertEqual(
            core.modules,
            (
                "common.py", "cards.py", "memory.py",
                "rag.py", "groups.py", "telegram.py",
                "persona_delete_panel.py", "language.py",
                "greetings.py", "help_details.py", "help.py",
                "input_flows.py", "catalog.py", "update.py",
                "image_generation.py", "expressions.py",
                "media.py", "generation.py", "commands.py",
                "status_panels.py", "command_routes.py",
                "message_commands.py", "callbacks.py",
                "panel_callback_routes.py", "main.py",
            ),
        )

        loaded = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertTrue(
            {
                "runtime_context.py",
                "panel_utils.py",
                "card_content.py",
                "callback_tokens.py",
            }.isdisjoint(loaded)
        )
```

Do not modify `bridge/runtime_loader.py` unless the test reveals unplanned production drift. The expected implementation leaves the loader tuple unchanged.

- [ ] **Step 6: Run the complete Phase 7B2 architecture guard suite**

Run:

```bash
python -m pytest   tests/test_card_foundations_import_island.py   tests/test_runtime_import_islands.py   tests/test_persistence_import_island.py   tests/test_runtime_loader.py   -q
```

Expected: PASS.

- [ ] **Step 7: Run card/content/identity regressions**

Run:

```bash
python -m pytest   tests/test_default_character_name.py   tests/test_alternate_greetings.py   tests/test_character_rename.py   tests/test_world_management.py   tests/test_catalog_panel.py   -q
```

Expected: PASS.

- [ ] **Step 8: Run panel/Persona/session regressions**

Run:

```bash
python -m pytest   tests/test_item_panel_layouts.py   tests/test_panelification.py   tests/test_panel_lifecycle.py   tests/test_panel_expiry_feedback.py   tests/test_persona_service.py   tests/test_persona_editor.py   tests/test_persona_integrity.py   tests/test_persona_native_storage.py   tests/test_persona_native_sync.py   tests/test_session_command_routing.py   tests/test_session_delete.py   tests/test_session_naming.py   -q
```

Expected: PASS.

- [ ] **Step 9: Commit permanent Phase 7B2 guards**

```bash
git add   tests/test_card_foundations_import_island.py   tests/test_runtime_loader.py
git commit -m "test: guard Phase 7B2 card foundation ownership"
```

- [ ] **Step 10: Verify current upstream drift**

Run:

```bash
git fetch upstream main
git rev-list --left-right --count upstream/main...HEAD
```

Expected: behind count is `0`.

If upstream moved, inspect overlap in the Phase 7B2 production/test files before rebasing. Do not rebase blindly.

- [ ] **Step 11: Run exact source/ownership smoke verification**

Run:

```bash
python - <<'PY'
import bridge.callback_tokens as callback_tokens
import bridge.card_content as card_content
import bridge.panel_utils as panel_utils
import bridge.runtime as rt
import bridge.runtime_context as runtime_context
from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

assert rt.db_connection_context is runtime_context.db_connection_context
assert rt.panel_session_context is runtime_context.panel_session_context
assert rt.panel_actor_context is runtime_context.panel_actor_context
assert rt.panel_label is panel_utils.panel_label
assert rt.panel_page is panel_utils.panel_page
assert rt.panel_navigation is panel_utils.panel_navigation
assert rt.card_fields is card_content.card_fields
assert rt.character_display_name is card_content.character_display_name
assert rt.build_system_prompt is card_content.build_system_prompt
assert rt.dynamic_callback_token is callback_tokens.dynamic_callback_token
assert rt.resolve_dynamic_callback_token is callback_tokens.resolve_dynamic_callback_token

loaded = {
    module
    for stage in DEFAULT_RUNTIME_STAGES
    for module in stage.modules
}
assert "cards.py" in loaded
for name in (
    "runtime_context.py",
    "panel_utils.py",
    "card_content.py",
    "callback_tokens.py",
):
    assert name not in loaded

assert not hasattr(rt, "_CALLBACK_TOKEN_VALUES")
assert not hasattr(rt, "_CALLBACK_TOKEN_TTL_SECONDS")

print("Phase 7B2 card foundations verified")
PY
```

Expected: prints `Phase 7B2 card foundations verified`.

- [ ] **Step 12: Run Python compilation exactly as CI does**

```bash
python -m compileall -q bridge tests sillytavern_telegram_bridge.py
```

Expected: exit 0.

- [ ] **Step 13: Run full unittest suite**

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 14: Run full pytest suite**

```bash
python -m pytest -q
```

Expected: all tests and subtests PASS.

- [ ] **Step 15: Validate dependency environment**

```bash
python -m pip check
```

Expected:

```text
No broken requirements found.
```

- [ ] **Step 16: Audit locked dependencies**

```bash
python -m pip_audit -r requirements.lock
```

Expected: no known vulnerabilities.

- [ ] **Step 17: Review the entire branch against the approved Phase 7B2 spec**

Run:

```bash
git diff --stat 63fe8d2c3daa224d71fb1f1c40ca328c87b8987d...HEAD
git diff 63fe8d2c3daa224d71fb1f1c40ca328c87b8987d...HEAD --   bridge/config.py   bridge/runtime_context.py   bridge/common.py   bridge/panel_utils.py   bridge/card_content.py   bridge/callback_tokens.py   bridge/cards.py   bridge/character_identity.py   bridge/runtime.py   tests/test_card_foundations_import_island.py   tests/test_default_character_name.py   tests/test_world_management.py   tests/test_character_rename.py   tests/test_runtime_loader.py
```

Review specifically for:

- exactly the eleven approved card/world/prompt config values moved to `bridge.config`;
- no `CHARACTER_BACKUP_DIR`, provider/model, RAG, sync, image, Telegram, or logging config migrated;
- exactly one `_PANEL_SESSION_CONTEXT` and one `_DB_CONNECTION_CONTEXT`, both in `runtime_context.py`;
- exactly one `_CALLBACK_TOKEN_VALUES`, in `callback_tokens.py`;
- `card_content.py` has no runtime/common/database/Telegram/Persona dependency;
- `panel_utils.py` is pure;
- `cards.py` contains no migrated implementations or callback cache;
- `cards.py` still contains only the intended Persona/menu shell responsibilities;
- `cards.py` still uses late-bound Telegram/Persona collaborators rather than ordinary-binding them;
- `character_identity.py` imports and does not redefine `character_display_name`;
- all moved public functions are explicitly imported by `runtime.py`;
- private context/cache state is not exported by `runtime.py`;
- `runtime_loader.py` production tuple is unchanged from the 7B1 baseline;
- `cards.py` remains exec-loaded;
- the four new foundation modules are not exec-loaded;
- callback token format and TTL are unchanged;
- user-visible panel/menu strings are unchanged.

- [ ] **Step 18: Create/update the PR and require exact-head GitHub Actions**

Open a draft or ordinary PR so authoritative CI runs against the branch head.

The PR description must summarize:

```text
- canonical card/world/prompt config ownership
- single runtime-context thread-local ownership
- pure panel utility extraction
- canonical card/content ownership
- canonical character_display_name ownership
- single callback-token cache ownership
- residual cards.py UI/Persona shell intentionally remains exec-loaded
- no Telegram/Persona subsystem migration in this phase
```

Confirm exact-head CI:

- compile: success;
- audit-regression/unittest step: success;
- full pytest: success;
- `pip check`: success;
- `pip-audit`: success;
- branch: 0 behind current `main`;
- PR: mergeable;
- review threads/comments: none unresolved;
- whole-branch review: no Critical or Important findings.

Do not merge the PR.

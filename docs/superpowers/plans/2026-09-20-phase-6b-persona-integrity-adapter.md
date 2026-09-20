# Phase 6B Persona Integrity Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Persona portion of `state_integrity.py` late overrides with an ordinary `IntegrityCheckedPersonaStore` while preserving native Persona serialization, logical-ID collision protection, avatar-extension behavior, and PersonaService semantics.

**Architecture:** `persona_sync.py` becomes the single runtime owner of native Persona persistence. It keeps native file/API mechanics and composes an ordinary-import `IntegrityCheckedPersonaStore` decorator that owns direct-store serialization and bridge logical-ID uniqueness; `PersonaService` continues to own multi-step Persona use cases.

**Tech Stack:** Python 3.11, stdlib `dataclasses`, `pathlib`, `threading`, `unittest`, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-20-phase-6b-persona-integrity-adapter-design.md`

## Global Constraints

- Native Persona upserts and deletes remain process-serialized.
- Direct raw Persona compatibility calls remain no weaker than current behavior.
- Bridge logical Persona IDs must not collide with an existing `bridge-<logical-id>.*` native avatar stem.
- Explicit existing native avatar updates remain allowed.
- Valid source avatar extensions are preserved; unsupported source suffixes still fall back to `.png`.
- Native settings optimistic-concurrency checks, backup behavior, file permissions, backup retention, save/readback verification, cache invalidation, and avatar cleanup semantics remain unchanged.
- Persona deletion removes metadata but preserves avatar media.
- `PersonaService` keeps whole-use-case locking, rollback-on-failed-selection behavior, and reference-aware deletion.
- `state_integrity.py` remains responsible only for its later Hindsight/Live-Sync safety work after this phase.
- No new public-callable runtime override, `_ORIGINAL_*` capture, runtime stage, global service locator, ORM, or runtime-order dependency.
- No Phase 6C Hindsight or Phase 6D Sync work.

## Review Focus

- A logical ID is created when `bridge-<id>.webp` already exists but `bridge-<id>.png` does not: reject by stem, not exact filename.
- An explicit native avatar such as `native.png` is updated while its stem resembles a bridge ID: allow it because explicit avatar updates are not logical-ID creation.
- A service call holds the Persona edit lock and then invokes the integrity store, which acquires the same lock again: the production path must remain reentrant and not deadlock.
- The native settings file changes after the first read but before save: preserve the existing optimistic-hash rejection and do not weaken it in the adapter.
- A newly cloned avatar exists but settings save/readback fails: preserve existing cleanup rules so an orphan image is not left when current code would remove it.

---

## File Structure

**Create `bridge/persona_integrity.py`**
- Ordinary-import safety decorator only.
- Defines `IntegrityCheckedPersonaStore`.
- Owns logical bridge-ID collision checks and direct-store serialization.
- Does not import `bridge.runtime`, `persona_sync.py`, Telegram/UI code, or SillyTavern-specific paths.

**Modify `bridge/persona_sync.py`**
- Import `IntegrityCheckedPersonaStore` under a private alias.
- Own `_choose_native_avatar`.
- Rename the existing native persistence bodies to private backend functions.
- Construct `_PERSONA_STORE`.
- Keep `upsert_native_persona` and `delete_native_persona` as the only public runtime storage entry points, delegating to `_PERSONA_STORE`.

**Modify `bridge/state_integrity.py`**
- Remove Persona-specific captures, helpers, and public replacements.
- Leave Hindsight and Live Sync sections unchanged.

**Modify `bridge/runtime_loader.py`**
- Remove `upsert_native_persona` and `delete_native_persona` from the `state_integrity.py` allowed override set only.

**Create `tests/test_persona_integrity.py`**
- Ordinary unit tests for the decorator.
- No `bridge.runtime` import.

**Create `tests/test_persona_native_storage.py`**
- Runtime/native-storage integration tests for avatar allocation, persistence behavior, and public ownership.

**Modify `tests/test_state_integrity.py`**
- Remove Persona tests that mutate `_ORIGINAL_UPSERT_NATIVE_PERSONA` and `_ORIGINAL_DELETE_NATIVE_PERSONA`.
- Keep Hindsight and Live Sync coverage.

**Modify `tests/test_runtime_loader.py`**
- Assert Persona public owners are `persona_sync.py`.
- Assert state-integrity no longer allowlists Persona replacements.
- Assert runtime report has no state-integrity Persona override.

**Do not modify `tests/test_composition.py`**
- The existing `test_startup_builds_persona_service_from_final_runtime_collaborators` already pins `services.persona.upsert_persona` and `services.persona.delete_persona` to the final runtime collaborators.

---

### Task 1: Add the Ordinary IntegrityCheckedPersonaStore

**Files:**
- Create: `bridge/persona_integrity.py`
- Create: `tests/test_persona_integrity.py`

**Interfaces:**
- Produces:
  - `IntegrityCheckedPersonaStore(load_personas, upsert_backend, delete_backend, valid_avatar, edit_lock)`
  - `IntegrityCheckedPersonaStore.upsert(identifier: str, name: str, description: str, *, client=None) -> str`
  - `IntegrityCheckedPersonaStore.delete(identifier: str, *, client=None) -> bool`
- Consumes:
  - `load_personas(force=True) -> dict[str, dict[str, object]]`
  - `upsert_backend(identifier, name, description, client=None) -> str`
  - `delete_backend(identifier, client=None) -> bool`
  - `valid_avatar(value) -> str`
  - `edit_lock() -> context manager`

- [ ] **Step 1: Write the failing ordinary-import adapter tests**

Create `tests/test_persona_integrity.py`:

```python
import threading
import time
import unittest

from bridge.persona_integrity import IntegrityCheckedPersonaStore


class IntegrityCheckedPersonaStoreTests(unittest.TestCase):
    def setUp(self):
        self.personas = {
            "native.png": {
                "name": "Native",
                "description": "Native description",
                "sillytavern_avatar": "native.png",
            }
        }
        self.lock = threading.RLock()
        self.upserts = []
        self.deletes = []

        def load_personas(*, force=False):
            self.assertTrue(force)
            return dict(self.personas)

        def upsert(identifier, name, description, client=None):
            self.upserts.append(
                (identifier, name, description, client)
            )
            return str(identifier)

        def delete(identifier, client=None):
            self.deletes.append((identifier, client))
            return True

        self.store = IntegrityCheckedPersonaStore(
            load_personas=load_personas,
            upsert_backend=upsert,
            delete_backend=delete,
            valid_avatar=lambda value: (
                str(value)
                if str(value).endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))
                else ""
            ),
            edit_lock=lambda: self.lock,
        )

    def test_rejects_existing_bridge_logical_id_by_avatar_stem(self):
        self.personas["bridge-writer.webp"] = {
            "name": "Existing",
            "description": "Existing",
            "sillytavern_avatar": "bridge-writer.webp",
        }

        with self.assertRaisesRegex(ValueError, "Persona ID already exists"):
            self.store.upsert(
                "writer",
                "Writer",
                "Description",
            )

        self.assertEqual(self.upserts, [])

    def test_explicit_native_avatar_update_bypasses_logical_id_collision_check(self):
        self.personas["native.png"] = {
            "name": "Native",
            "description": "Existing",
            "sillytavern_avatar": "native.png",
        }

        result = self.store.upsert(
            "native.png",
            "Updated",
            "Updated description",
            client="client",
        )

        self.assertEqual(result, "native.png")
        self.assertEqual(
            self.upserts,
            [(
                "native.png",
                "Updated",
                "Updated description",
                "client",
            )],
        )

    def test_delete_delegates_and_preserves_client(self):
        self.assertTrue(
            self.store.delete(
                "native.png",
                client="client",
            )
        )
        self.assertEqual(
            self.deletes,
            [("native.png", "client")],
        )

    def test_backend_exceptions_propagate_unchanged(self):
        original = RuntimeError("offline")
        store = IntegrityCheckedPersonaStore(
            load_personas=lambda force=False: {},
            upsert_backend=lambda *_args, **_kwargs: (_ for _ in ()).throw(original),
            delete_backend=lambda *_args, **_kwargs: True,
            valid_avatar=lambda _value: "",
            edit_lock=lambda: self.lock,
        )

        with self.assertRaises(RuntimeError) as caught:
            store.upsert("writer", "Writer", "Description")

        self.assertIs(caught.exception, original)
```

- [ ] **Step 2: Add RED concurrency tests for upsert/upsert and upsert/delete**

Append this helper and the two tests. The overlap counter lives inside the backend, so it measures work protected by the store lock:

```python
    def _maximum_backend_parallelism(self, operations):
        active = 0
        maximum = 0
        counter_lock = threading.Lock()
        start = threading.Barrier(len(operations) + 1)
        errors = []

        def backend_enter():
            nonlocal active, maximum
            with counter_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with counter_lock:
                active -= 1

        store = IntegrityCheckedPersonaStore(
            load_personas=lambda force=False: {},
            upsert_backend=lambda *_args, **_kwargs: (
                backend_enter() or "avatar.png"
            ),
            delete_backend=lambda *_args, **_kwargs: (
                backend_enter() or True
            ),
            valid_avatar=lambda value: (
                str(value)
                if str(value).endswith(".png")
                else ""
            ),
            edit_lock=lambda: self.lock,
        )

        def run(operation):
            try:
                start.wait(timeout=2)
                operation(store)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=run, args=(operation,))
            for operation in operations
        ]
        for thread in threads:
            thread.start()
        start.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=2)

        self.assertEqual(errors, [])
        return maximum

    def test_two_upserts_are_serialized(self):
        maximum = self._maximum_backend_parallelism([
            lambda store: store.upsert(
                "one",
                "One",
                "Description",
            ),
            lambda store: store.upsert(
                "two",
                "Two",
                "Description",
            ),
        ])
        self.assertEqual(maximum, 1)

    def test_upsert_and_delete_are_serialized(self):
        maximum = self._maximum_backend_parallelism([
            lambda store: store.upsert(
                "writer",
                "Writer",
                "Description",
            ),
            lambda store: store.delete("native.png"),
        ])
        self.assertEqual(maximum, 1)
```

- [ ] **Step 3: Run the focused test and verify RED**

Run:

```bash
python -m unittest tests.test_persona_integrity -v
```

Expected: import failure because `bridge/persona_integrity.py` does not exist.

- [ ] **Step 4: Implement the ordinary integrity adapter**

Create `bridge/persona_integrity.py`:

```python
"""Ordinary Persona-store integrity decorator."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IntegrityCheckedPersonaStore:
    load_personas: Callable[..., dict[str, dict[str, object]]]
    upsert_backend: Callable[..., str]
    delete_backend: Callable[..., bool]
    valid_avatar: Callable[[object], str]
    edit_lock: Callable[[], AbstractContextManager[object]]

    def _logical_id_is_taken(self, identifier: str) -> bool:
        value = str(identifier or "")
        if self.valid_avatar(value):
            return False
        expected_stem = f"bridge-{value}"
        return any(
            Path(str(avatar)).stem == expected_stem
            for avatar in self.load_personas(force=True)
        )

    def upsert(
        self,
        identifier: str,
        name: str,
        description: str,
        *,
        client=None,
    ) -> str:
        with self.edit_lock():
            if self._logical_id_is_taken(identifier):
                raise ValueError("Persona ID already exists")
            return str(
                self.upsert_backend(
                    identifier,
                    name,
                    description,
                    client=client,
                )
            )

    def delete(
        self,
        identifier: str,
        *,
        client=None,
    ) -> bool:
        with self.edit_lock():
            return bool(
                self.delete_backend(
                    identifier,
                    client=client,
                )
            )
```

- [ ] **Step 5: Run Task 1 tests**

Run:

```bash
python -m unittest tests.test_persona_integrity -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add bridge/persona_integrity.py tests/test_persona_integrity.py
git commit -m "refactor: add persona integrity store"
```

---

### Task 2: Make persona_sync.py the Canonical Native Persona Store

**Files:**
- Modify: `bridge/persona_sync.py`
- Create: `tests/test_persona_native_storage.py`

**Interfaces:**
- Consumes:
  - `IntegrityCheckedPersonaStore` from Task 1.
- Produces:
  - `_choose_native_avatar(persona_id: str, persona: dict, settings: dict, native_names: dict) -> str`
  - `_upsert_native_persona_storage(identifier: str, name: str, description: str, client=None) -> str`
  - `_delete_native_persona_storage(identifier: str, client=None) -> bool`
  - `_PERSONA_STORE: IntegrityCheckedPersonaStore`
  - canonical public `upsert_native_persona(identifier: str, name: str, description: str, client=None) -> str`
  - canonical public `delete_native_persona(identifier: str, client=None) -> bool`

- [ ] **Step 1: Add RED canonical-store integration tests**

Create `tests/test_persona_native_storage.py` with a temporary native SillyTavern fixture:

```python
import json
from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class NativePersonaStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_settings = rt.NATIVE_PERSONA_SETTINGS_FILE
        self.old_avatars = rt.NATIVE_PERSONA_AVATAR_DIR
        self.old_backups = rt.NATIVE_PERSONA_BACKUP_DIR
        self.old_cache = rt._NATIVE_PERSONA_CACHE
        self.old_cache_time = rt._NATIVE_PERSONA_CACHE_LAST_REFRESH
        self.old_phase3 = rt.phase3_api_configured

        rt.NATIVE_PERSONA_SETTINGS_FILE = root / "settings.json"
        rt.NATIVE_PERSONA_AVATAR_DIR = root / "avatars"
        rt.NATIVE_PERSONA_BACKUP_DIR = root / "backups"
        rt.NATIVE_PERSONA_AVATAR_DIR.mkdir()
        (rt.NATIVE_PERSONA_AVATAR_DIR / "source.webp").write_bytes(
            b"webp-source-bytes"
        )
        self.settings = {
            "user_avatar": "source.webp",
            "power_user": {
                "personas": {
                    "native.png": "Native",
                },
                "persona_descriptions": {
                    "native.png": {
                        "description": "Native description",
                    },
                },
            },
            "unrelated": {"keep": True},
        }
        rt.NATIVE_PERSONA_SETTINGS_FILE.write_text(
            json.dumps(self.settings),
            encoding="utf-8",
        )
        rt._NATIVE_PERSONA_CACHE = {}
        rt._NATIVE_PERSONA_CACHE_LAST_REFRESH = 0
        rt.phase3_api_configured = lambda: False

    def tearDown(self):
        rt.phase3_api_configured = self.old_phase3
        rt._NATIVE_PERSONA_CACHE = self.old_cache
        rt._NATIVE_PERSONA_CACHE_LAST_REFRESH = self.old_cache_time
        rt.NATIVE_PERSONA_SETTINGS_FILE = self.old_settings
        rt.NATIVE_PERSONA_AVATAR_DIR = self.old_avatars
        rt.NATIVE_PERSONA_BACKUP_DIR = self.old_backups
        self.tmp.cleanup()

    def _settings(self):
        return json.loads(
            rt.NATIVE_PERSONA_SETTINGS_FILE.read_text(
                encoding="utf-8"
            )
        )

    def test_explicit_persona_store_preserves_source_extension_and_bytes(self):
        avatar = rt._PERSONA_STORE.upsert(
            "writer",
            "Writer",
            "Writer description",
        )

        self.assertEqual(avatar, "bridge-writer.webp")
        self.assertEqual(
            (rt.NATIVE_PERSONA_AVATAR_DIR / avatar).read_bytes(),
            b"webp-source-bytes",
        )
        settings = self._settings()
        self.assertEqual(
            settings["power_user"]["personas"][avatar],
            "Writer",
        )
        self.assertEqual(
            settings["unrelated"],
            {"keep": True},
        )

    def test_explicit_persona_store_rejects_duplicate_bridge_stem(self):
        first = rt._PERSONA_STORE.upsert(
            "writer",
            "Writer",
            "Writer description",
        )
        self.assertEqual(first, "bridge-writer.webp")

        with self.assertRaisesRegex(
            ValueError,
            "Persona ID already exists",
        ):
            rt._PERSONA_STORE.upsert(
                "writer",
                "Writer 2",
                "Another description",
            )

    def test_delete_preserves_avatar_media(self):
        avatar = rt._PERSONA_STORE.upsert(
            "writer",
            "Writer",
            "Writer description",
        )
        path = rt.NATIVE_PERSONA_AVATAR_DIR / avatar

        self.assertTrue(
            rt._PERSONA_STORE.delete(avatar)
        )

        self.assertTrue(path.is_file())
        self.assertNotIn(
            avatar,
            self._settings()["power_user"]["personas"],
        )
```

- [ ] **Step 2: Add RED source-ownership tests**

Append:

```python
class NativePersonaSourceBoundaryTests(unittest.TestCase):
    def test_persona_sync_owns_avatar_allocator_and_explicit_store(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "persona_sync.py"
        ).read_text(encoding="utf-8")

        self.assertIn("def _choose_native_avatar(", source)
        self.assertIn("_PERSONA_STORE = _IntegrityCheckedPersonaStore(", source)
        self.assertIn("def _upsert_native_persona_storage(", source)
        self.assertIn("def _delete_native_persona_storage(", source)
```

- [ ] **Step 3: Run Task 2 tests and verify RED**

Run:

```bash
python -m unittest tests.test_persona_native_storage -v
```

Expected: failures because `rt._PERSONA_STORE` and the canonical private storage names do not yet exist in `persona_sync.py`.

- [ ] **Step 4: Import the integrity adapter in persona_sync.py**

Near the existing ordinary imports, add:

```python
from bridge.persona_integrity import (
    IntegrityCheckedPersonaStore as _IntegrityCheckedPersonaStore,
)
```

Do not import `state_integrity.py`.

- [ ] **Step 5: Move _choose_native_avatar into persona_sync.py**

Insert the existing algorithm before the native upsert backend:

```python
def _choose_native_avatar(
    persona_id: str,
    persona: dict,
    settings: dict,
    native_names: dict,
) -> str:
    mapped = _valid_native_avatar(
        persona.get("sillytavern_avatar")
    )
    if mapped:
        return mapped

    matches = [
        avatar
        for avatar, name in native_names.items()
        if (
            str(name).casefold()
            == str(persona["name"]).casefold()
            and _valid_native_avatar(avatar)
        )
    ]
    if len(matches) == 1:
        return matches[0]

    source_name = _valid_native_avatar(
        settings.get("user_avatar")
    )
    suffix = (
        Path(source_name).suffix.casefold()
        if source_name
        else ".png"
    )
    if suffix not in _NATIVE_AVATAR_SUFFIXES:
        suffix = ".png"

    base = f"bridge-{persona_id}"
    candidate = f"{base}{suffix}"
    for attempt in range(257):
        if (
            candidate not in native_names
            and not (
                NATIVE_PERSONA_AVATAR_DIR / candidate
            ).exists()
        ):
            return candidate
        token = hashlib.sha256(
            f"{persona_id}:{attempt}".encode("utf-8")
        ).hexdigest()[:8]
        candidate = (
            f"{base[:54]}-{token}{suffix}"
        )
    raise ValueError(
        "Could not allocate a unique native persona avatar"
    )
```

Use the exact current algorithm from `state_integrity.py`; do not invent a new naming scheme.

- [ ] **Step 6: Rename native persistence bodies to private backends without changing their statements**

Perform these exact signature substitutions in `bridge/persona_sync.py`:

```python
# before
def upsert_native_persona(
    identifier: str,
    name: str,
    description: str,
    client=None,
) -> str:

# after
def _upsert_native_persona_storage(
    identifier: str,
    name: str,
    description: str,
    client=None,
) -> str:
```

and:

```python
# before
def delete_native_persona(
    identifier: str,
    client=None,
) -> bool:

# after
def _delete_native_persona_storage(
    identifier: str,
    client=None,
) -> bool:
```

Only the function names change in this step. Keep every statement in both existing bodies byte-for-byte equivalent so validation, backup, optimistic settings-hash checks, avatar creation, save/readback verification, deletion semantics, and cache invalidation cannot drift.

- [ ] **Step 7: Compose the explicit store and add stable public delegates**

Immediately after both private backends:

```python
_PERSONA_STORE = _IntegrityCheckedPersonaStore(
    load_personas=load_native_personas,
    upsert_backend=_upsert_native_persona_storage,
    delete_backend=_delete_native_persona_storage,
    valid_avatar=_valid_native_avatar,
    edit_lock=lambda: PERSONA_EDIT_LOCK,
)


def upsert_native_persona(
    identifier: str,
    name: str,
    description: str,
    client=None,
) -> str:
    return _PERSONA_STORE.upsert(
        identifier,
        name,
        description,
        client=client,
    )


def delete_native_persona(
    identifier: str,
    client=None,
) -> bool:
    return _PERSONA_STORE.delete(
        identifier,
        client=client,
    )
```

Leave `compatibility_persona_service()` using these public names. Python resolves those globals at call time.

- [ ] **Step 8: Add the reentrant service/store regression**

Store `self.root = root` in `NativePersonaStorageTests.setUp`, then append:

```python
    def test_persona_service_lock_can_nest_into_integrity_store(self):
        service = rt.compatibility_persona_service()
        db = rt.db_connect(
            self.root / "persona-service.sqlite3"
        )
        try:
            session = rt.create_session(
                db,
                "chat",
                rt.DEFAULT_MODEL,
            )
            avatar = service.create_and_select(
                db,
                "chat",
                session["session_id"],
                "nested",
                "Nested",
                "Nested lock test",
            )
        finally:
            db.close()

        self.assertTrue(
            Path(avatar).stem.startswith("bridge-nested")
        )
```

This uses one isolated temporary database and pins the Review Focus case where `PersonaService` holds `PERSONA_EDIT_LOCK` and the integrity store re-enters the same production lock.

- [ ] **Step 9: Run Task 1 and Task 2 focused tests**

Run:

```bash
python -m unittest   tests.test_persona_integrity   tests.test_persona_native_storage   tests.test_persona_service -v
```

Expected: PASS while the late `state_integrity.py` public override may still be active; Task 2 validates the future canonical store directly through `rt._PERSONA_STORE`.

- [ ] **Step 10: Commit Task 2**

```bash
git add   bridge/persona_sync.py   tests/test_persona_native_storage.py
git commit -m "refactor: make native persona storage explicit"
```

---

### Task 3: Retire Persona Overrides From state_integrity.py

**Files:**
- Modify: `bridge/state_integrity.py`
- Modify: `bridge/runtime_loader.py`
- Modify: `tests/test_state_integrity.py`
- Modify: `tests/test_runtime_loader.py`
- Modify: `tests/test_persona_native_storage.py`
- Verify unchanged: `tests/test_composition.py`

**Interfaces:**
- Consumes:
  - canonical `persona_sync.py::upsert_native_persona`
  - canonical `persona_sync.py::delete_native_persona`
- Produces:
  - no Persona replacements in `state_integrity.py`
  - runtime state-integrity allowlist limited to `retain_session_memory`, `purge_hindsight_session`, and `apply_sync_snapshot`

- [ ] **Step 1: Add RED runtime ownership tests**

In `tests/test_runtime_loader.py`, add:

```python
    def test_persona_integrity_writes_are_not_state_integrity_overrides(self):
        state_stage = next(
            stage
            for stage in DEFAULT_RUNTIME_STAGES
            if stage.name == "safety_overrides"
        )
        allowed = state_stage.allowed_overrides_for(
            "state_integrity.py"
        )

        self.assertNotIn(
            "upsert_native_persona",
            allowed,
        )
        self.assertNotIn(
            "delete_native_persona",
            allowed,
        )

    def test_persona_write_owners_are_persona_sync(self):
        self.assertEqual(
            Path(
                rt.upsert_native_persona.__code__.co_filename
            ).name,
            "persona_sync.py",
        )
        self.assertEqual(
            Path(
                rt.delete_native_persona.__code__.co_filename
            ).name,
            "persona_sync.py",
        )

    def test_state_integrity_runtime_report_has_no_persona_overrides(self):
        entry = next(
            item
            for item in rt.RUNTIME_LOAD_REPORT
            if item["module"] == "state_integrity.py"
        )
        self.assertNotIn(
            "upsert_native_persona",
            entry["public_callable_overrides"],
        )
        self.assertNotIn(
            "delete_native_persona",
            entry["public_callable_overrides"],
        )
```

- [ ] **Step 2: Add RED source-boundary guard**

Append to `tests/test_persona_native_storage.py`:

```python
    def test_state_integrity_no_longer_owns_persona_storage(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "state_integrity.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn(
            "_ORIGINAL_UPSERT_NATIVE_PERSONA",
            source,
        )
        self.assertNotIn(
            "_ORIGINAL_DELETE_NATIVE_PERSONA",
            source,
        )
        self.assertNotIn(
            "def upsert_native_persona(",
            source,
        )
        self.assertNotIn(
            "def delete_native_persona(",
            source,
        )
        self.assertNotIn(
            "def _choose_native_avatar(",
            source,
        )
```

- [ ] **Step 3: Run the ownership tests and verify RED**

Run:

```bash
python -m unittest   tests.test_runtime_loader.RuntimeLoaderTests.test_persona_integrity_writes_are_not_state_integrity_overrides   tests.test_runtime_loader.RuntimeLoaderTests.test_persona_write_owners_are_persona_sync   tests.test_runtime_loader.RuntimeLoaderTests.test_state_integrity_runtime_report_has_no_persona_overrides   tests.test_persona_native_storage.NativePersonaSourceBoundaryTests.test_state_integrity_no_longer_owns_persona_storage -v
```

Expected: failures because `state_integrity.py` still captures and replaces the two Persona functions and the allowlist still permits those replacements.

- [ ] **Step 4: Delete only the Persona section from state_integrity.py**

Remove these exact responsibilities:

```python
_ORIGINAL_UPSERT_NATIVE_PERSONA = upsert_native_persona
_ORIGINAL_DELETE_NATIVE_PERSONA = delete_native_persona
```

Remove the complete current definition beginning with:

```python
def _native_persona_id_is_taken(
    identifier: str,
) -> bool:
```

Remove the complete current definition beginning with:

```python
def _choose_native_avatar(
    persona_id: str,
    persona: dict,
    settings: dict,
    native_names: dict,
) -> str:
```

Remove the complete replacement public definition beginning with:

```python
def upsert_native_persona(
    identifier: str,
    name: str,
    description: str,
    client=None,
) -> str:
```

and the complete replacement public definition beginning with:

```python
def delete_native_persona(
    identifier: str,
    client=None,
) -> bool:
```

Do not modify:

```python
_ORIGINAL_APPLY_SYNC_SNAPSHOT
_ORIGINAL_RETAIN_SESSION_MEMORY_WORKER
_ORIGINAL_PURGE_HINDSIGHT_SESSION
```

or any Hindsight/Live-Sync implementation in this task.

- [ ] **Step 5: Shrink the runtime-loader allowlist**

Change the `state_integrity.py` allowed overrides from:

```python
(
    "upsert_native_persona",
    "delete_native_persona",
    "retain_session_memory",
    "purge_hindsight_session",
    "apply_sync_snapshot",
)
```

to:

```python
(
    "retain_session_memory",
    "purge_hindsight_session",
    "apply_sync_snapshot",
)
```

Keep `state_integrity.py` in the `safety_overrides` stage.

- [ ] **Step 6: Migrate old state-integrity Persona tests**

In `tests/test_state_integrity.py`, remove:

- `test_native_persona_write_is_process_serialized`;
- `test_native_persona_delete_serializes_with_upsert`;
- `test_new_native_persona_preserves_source_avatar_extension_and_id_is_unique`.

Their guarantees are now covered by:

- `tests/test_persona_integrity.py` for serialization and collision policy;
- `tests/test_persona_native_storage.py` for extension preservation, bytes, persistence, and runtime integration.

Remove `threading` import from `tests/test_state_integrity.py` if no remaining test uses it.

Do not delete the Hindsight or Live Sync tests in that file.

- [ ] **Step 7: Pin direct public compatibility functions after cutover**

Add to `tests/test_persona_native_storage.py`:

```python
    def test_public_persona_functions_use_explicit_store_after_cutover(self):
        avatar = rt.upsert_native_persona(
            "public",
            "Public",
            "Public description",
        )
        self.assertTrue(
            Path(avatar).stem.startswith("bridge-public")
        )

        self.assertTrue(
            rt.delete_native_persona(avatar)
        )
        self.assertTrue(
            (rt.NATIVE_PERSONA_AVATAR_DIR / avatar).is_file()
        )
```

This validates the actual compatibility surface after `state_integrity.py` stops replacing it.

- [ ] **Step 8: Verify the existing PersonaService composition test remains unchanged and green**

The current `tests/test_composition.py::CompositionIntegrationTests.test_startup_builds_persona_service_from_final_runtime_collaborators` already patches `rt.upsert_native_persona` and `rt.delete_native_persona`, builds startup services, and asserts those exact callables are injected into `PersonaService`.

Do not edit that test. Run it after the cutover:

```bash
python -m unittest \
  tests.test_composition.CompositionIntegrationTests.test_startup_builds_persona_service_from_final_runtime_collaborators -v
```

Expected: PASS, proving startup composition automatically follows the new canonical public owners.

- [ ] **Step 9: Run the focused Persona/runtime suite**

Run:

```bash
python -m unittest   tests.test_persona_integrity   tests.test_persona_native_storage   tests.test_persona_service   tests.test_persona_editor   tests.test_state_integrity   tests.test_runtime_loader   tests.test_composition -v
```

Expected: PASS.

- [ ] **Step 10: Run source-boundary scan**

Run:

```bash
python - <<'PY'
from pathlib import Path
import bridge.runtime as rt
from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

state = Path("bridge/state_integrity.py").read_text(
    encoding="utf-8"
)
sync = Path("bridge/persona_sync.py").read_text(
    encoding="utf-8"
)

assert "_ORIGINAL_UPSERT_NATIVE_PERSONA" not in state
assert "_ORIGINAL_DELETE_NATIVE_PERSONA" not in state
assert "def upsert_native_persona(" not in state
assert "def delete_native_persona(" not in state
assert "def _choose_native_avatar(" not in state

assert "def _choose_native_avatar(" in sync
assert "_PERSONA_STORE = _IntegrityCheckedPersonaStore(" in sync
assert (
    Path(rt.upsert_native_persona.__code__.co_filename).name
    == "persona_sync.py"
)
assert (
    Path(rt.delete_native_persona.__code__.co_filename).name
    == "persona_sync.py"
)

state_stage = next(
    stage
    for stage in DEFAULT_RUNTIME_STAGES
    if stage.name == "safety_overrides"
)
allowed = state_stage.allowed_overrides_for(
    "state_integrity.py"
)
assert "upsert_native_persona" not in allowed
assert "delete_native_persona" not in allowed
assert {
    "retain_session_memory",
    "purge_hindsight_session",
    "apply_sync_snapshot",
}.issubset(allowed)

print("Phase 6B Persona ownership verified")
PY
```

Expected: `Phase 6B Persona ownership verified`.

- [ ] **Step 11: Commit Task 3**

```bash
git add   bridge/state_integrity.py   bridge/runtime_loader.py   tests/test_state_integrity.py   tests/test_runtime_loader.py   tests/test_persona_native_storage.py   tests/test_composition.py
git commit -m "refactor: retire persona state-integrity overrides"
```

If `tests/test_composition.py` was unchanged, omit it from `git add`.

---

### Task 4: Exact-Head Verification and PR Readiness

**Files:**
- Modify: `docs/superpowers/plans/2026-09-20-phase-6b-persona-integrity-adapter.md`
- No production changes unless verification reveals a defect. Any defect returns to the owning task's RED -> GREEN cycle.

**Interfaces:**
- Consumes: complete Phase 6B branch.
- Produces: exact-head verification evidence and a PR against upstream `main`.
- Does not merge the PR.

- [ ] **Step 1: Run compile verification**

Run:

```bash
python -m compileall -q   bridge   tests   sillytavern_telegram_bridge.py
```

Expected: exit 0.

- [ ] **Step 2: Run the full unittest suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 3: Run the full pytest suite**

Run:

```bash
python -m pytest -q
```

Expected: all tests PASS.

- [ ] **Step 4: Validate installed dependency consistency**

Run:

```bash
python -m pip check
```

Expected:

```text
No broken requirements found.
```

- [ ] **Step 5: Audit locked dependencies where available**

Run:

```bash
python -m pip_audit -r requirements.lock
```

Expected: no known vulnerabilities.

If local `pip-audit` is unavailable, do not install unrelated packages solely for this step; the GitHub Actions audit is authoritative.

- [ ] **Step 6: Run final architecture verification**

Run:

```bash
python - <<'PY'
from pathlib import Path
import bridge.runtime as rt
from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

assert (
    Path(rt.upsert_native_persona.__code__.co_filename).name
    == "persona_sync.py"
)
assert (
    Path(rt.delete_native_persona.__code__.co_filename).name
    == "persona_sync.py"
)

state = Path("bridge/state_integrity.py").read_text(
    encoding="utf-8"
)
for forbidden in (
    "_ORIGINAL_UPSERT_NATIVE_PERSONA",
    "_ORIGINAL_DELETE_NATIVE_PERSONA",
    "def upsert_native_persona(",
    "def delete_native_persona(",
    "def _choose_native_avatar(",
):
    assert forbidden not in state

stage = next(
    stage
    for stage in DEFAULT_RUNTIME_STAGES
    if stage.name == "safety_overrides"
)
allowed = stage.allowed_overrides_for(
    "state_integrity.py"
)
assert "upsert_native_persona" not in allowed
assert "delete_native_persona" not in allowed

entry = next(
    item
    for item in rt.RUNTIME_LOAD_REPORT
    if item["module"] == "state_integrity.py"
)
assert "upsert_native_persona" not in entry[
    "public_callable_overrides"
]
assert "delete_native_persona" not in entry[
    "public_callable_overrides"
]

print("Phase 6B final architecture verified")
PY
```

Expected:

```text
Phase 6B final architecture verified
```

- [ ] **Step 7: Review Hindsight/Sync non-scope diff**

Inspect the branch diff and verify that no Phase 6B production edit changes the bodies of:

- `retain_session_memory`;
- `_retain_session_memory`;
- `purge_hindsight_session`;
- `apply_sync_snapshot`;
- `sync_safety.py`.

If any such body changed, revert that unrelated change before proceeding.

- [ ] **Step 8: Record execution evidence in this plan**

Append an `## Execution Evidence` section containing the exact:

- Task-level RED commit SHAs and failing assertions;
- corresponding GREEN commit SHAs;
- final implementation head SHA;
- unittest result;
- pytest result;
- `pip check` result;
- dependency audit result;
- architecture scan result;
- current upstream `main` SHA;
- any non-overlapping upstream drift decision.

Do not claim exact-head CI success before that exact head completes CI.

- [ ] **Step 9: Commit verification documentation**

Run:

```bash
git add   docs/superpowers/plans/2026-09-20-phase-6b-persona-integrity-adapter.md
git commit -m "docs: record Phase 6B verification"
```

- [ ] **Step 10: Open or update a Draft PR**

Target:

```text
base: cepeter/SillyTavern-Telegram-Bridge:main
head: punzer4-code:refactor/phase-6b-persona-integrity-adapter
```

Title:

```text
refactor: retire persona integrity runtime overrides
```

The PR body must state:

- `persona_sync.py` is now the canonical Persona storage owner;
- `IntegrityCheckedPersonaStore` owns direct-store serialization and logical-ID collision protection;
- avatar allocation now lives with native storage;
- `state_integrity.py` no longer captures/replaces Persona writes;
- Hindsight and Sync safety were intentionally left unchanged;
- exact RED -> GREEN evidence and current verification results.

Keep the PR Draft while exact-head CI is incomplete.

- [ ] **Step 11: Check upstream drift**

Compare the feature branch base with current upstream `main`.

If upstream changed only in unrelated files, document that and continue.

If upstream changed any Phase 6B file or its test surface, inspect and reconcile the delta, then rerun affected focused and full tests before proceeding.

- [ ] **Step 12: Inspect GitHub Actions on the exact final head**

Required workflow steps:

- checkout;
- Python 3.11 setup;
- packaging tools;
- locked runtime dependencies;
- `pip check`;
- development test tooling;
- compile;
- unittest discovery;
- pytest;
- pip-audit installation;
- locked dependency audit.

Expected: workflow conclusion `success` on the exact PR head SHA.

- [ ] **Step 13: Review PR comments and review threads**

Before Ready-for-review, verify:

- no unresolved review threads;
- no unaddressed review comments;
- PR remains mergeable;
- exact head still matches the green CI SHA.

- [ ] **Step 14: Mark the PR Ready for review**

Only after Steps 12-13 are satisfied.

Do not merge the PR. Merge remains a separate user decision.


## Execution Evidence

### Execution ruling

- Ruling: this harness has no runnable local repository/worktree, so the Draft PR GitHub Actions workflow is the native RED -> GREEN runner for every task. The exact repository CI executes compile, full unittest discovery, full pytest, `pip check`, and locked dependency audit. Cost if wrong: focused local commands are not separately recorded, but every RED assertion and every GREEN state is observed on the actual branch SHA under the repository's authoritative CI environment.

### Task 1 — ordinary Persona integrity adapter

- RED head: `0a9630cf86a6ad8b7c8c98ee19a2fba79d636112`.
- CI #407 (`35508118147`) failed with `ModuleNotFoundError: No module named 'bridge.persona_integrity'`; 489 unittest tests ran with one import error.
- GREEN head: `86b42260b7c1653a0b9cc3f11f17e7519aeab493`.
- CI #408 (`35508176987`) completed successfully across every workflow step.
- Result: `IntegrityCheckedPersonaStore` exists as ordinary-import infrastructure and its serialization/collision/client/exception tests pass.

### Task 2 — canonical native Persona storage

- RED head: `03162f5c57bbb866ba972a7c5fabfb0b7609770d`.
- CI #409 (`35508241141`) failed with three `AttributeError` failures because `rt._PERSONA_STORE` did not exist and one source-boundary failure because `persona_sync.py` did not own `_choose_native_avatar`; 499 unittest tests ran.
- GREEN head: `6cc642a5ebc13cd5539a4129bf37821ef6a060a5`.
- CI #410 (`35508306082`) completed successfully across every workflow step.
- Result: avatar allocation and native persistence backends are canonical in `persona_sync.py`; `_PERSONA_STORE` wraps them explicitly.

### Task 3 — retire Persona state-integrity overrides

- RED head: `f32880509a6f68823297096f55a6f9f7b23b0256`.
- CI #412 (`35508376356`) failed exactly four ownership assertions:
  - `state_integrity.py` still contained `_ORIGINAL_UPSERT_NATIVE_PERSONA`;
  - the allowlist still contained `upsert_native_persona` / `delete_native_persona`;
  - runtime public owners still resolved to `state_integrity.py`;
  - the runtime report still listed the two Persona overrides.
- GREEN implementation head: `90a74c13cc46a7eeed30a9e81f53433be2733387`.
- CI #416 (`35508485578`) succeeded:
  - `501` unittest tests passed;
  - pytest: `501 passed, 103 subtests passed`;
  - `pip check`: `No broken requirements found.`;
  - locked dependency audit: `No known vulnerabilities found`.
- Source-boundary verification on the implementation head:
  - no `_ORIGINAL_UPSERT_NATIVE_PERSONA`;
  - no `_ORIGINAL_DELETE_NATIVE_PERSONA`;
  - no Persona public definitions or avatar allocator remain in `state_integrity.py`;
  - its allowed overrides are now only `retain_session_memory`, `purge_hindsight_session`, and `apply_sync_snapshot`;
  - the complete Hindsight/Live-Sync portion of `state_integrity.py`, beginning at `_hindsight_epoch_key`, is byte-for-byte identical to baseline `314b2137b36164608c0d427583564b0fcb1660d1`;
  - `sync_safety.py` is unchanged.

### Upstream and PR state before final exact-head gate

- Merge base / current upstream `main`: `314b2137b36164608c0d427583564b0fcb1660d1`; no upstream drift is present.
- Draft PR: #42.
- Final exact-head CI, review-thread inspection, whole-branch review, and Ready-for-review transition are intentionally performed after this evidence commit. Updating this document again after those checks would create another head and recursively invalidate exact-head CI evidence.

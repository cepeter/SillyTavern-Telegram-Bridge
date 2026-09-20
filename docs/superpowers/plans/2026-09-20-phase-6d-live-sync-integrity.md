# Phase 6D Live Sync Integrity Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the final `state_integrity.py::apply_sync_snapshot` runtime override with an ordinary `SyncSnapshotIntegrityAdapter` composed by `sync_core.py`, preserving Live Sync import, explicit-clear, hash, and best-effort memory-refresh behavior.

**Architecture:** `sync_core.py` remains the canonical public owner of `apply_sync_snapshot`. Its current raw importer becomes a private backend, while `bridge/sync_integrity.py` owns explicit-empty Persona/world handling and post-import memory refresh through injected collaborators. `SyncService` and `sync_api.py::phase3_sync_now` remain structurally unchanged.

**Tech Stack:** Python 3.11, stdlib `dataclasses`, `sqlite3`, `unittest`, `unittest.mock`, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-20-phase-6d-live-sync-integrity-design.md`

## Global Constraints

- Validated snapshot import semantics in `sync_core.py` must remain unchanged.
- An explicitly empty `persona` clears `persona_id`; an absent `persona` does not modify it.
- An explicitly empty `world_info` clears `world_file`; an absent `world_info` does not modify it.
- Valid non-empty Persona/world values remain the raw backend's responsibility.
- Generation settings, messages, response variants, commits, and transcript hash behavior remain unchanged.
- The final stored session must be reloaded before Hindsight refresh.
- Reload/card-fields/memory-retain failures remain isolated and logged after the import succeeds.
- Raw snapshot and explicit-clear failures propagate.
- The adapter returns the raw imported hash unchanged.
- `SyncService` remains structurally unchanged.
- `sync_api.py::phase3_sync_now` conflict/checkpoint workflow remains structurally unchanged.
- `sync_safety.py` must have no Phase 6D diff.
- `state_integrity.py` is deleted after its final coverage migrates.
- No new public runtime override, `_ORIGINAL_*` capture, runtime stage, service locator, or load-order dependency.

## Review Focus

- `persona="   "`: treat whitespace-only Persona as an explicit clear and write `persona_id=""`.
- `world_info=None` with the key present: treat it as an explicit clear and write `world_file=""`.
- `world_info=""` with the key present: treat it as an explicit clear and write `world_file=""`.
- Raw import succeeds but reload/card parsing/memory retention fails: log the existing warning and still return the imported hash.
- Raw import changes `character_file`: memory refresh must derive card fields from the reloaded final session, not the pre-import session argument.

---

## File Structure

**Create `bridge/sync_integrity.py`**
- Ordinary-import integrity adapter only.
- Defines `SyncSnapshotIntegrityAdapter`.
- Owns explicit-empty metadata clearing and best-effort post-import memory refresh.
- Imports no `bridge.runtime`, `sync_core.py`, `sync_api.py`, `sync_safety.py`, `state_integrity.py`, or Telegram/UI code.

**Modify `bridge/sync_core.py`**
- Import `SyncSnapshotIntegrityAdapter`.
- In Task 2, compose `_SYNC_SNAPSHOT_INTEGRITY` around the existing raw public `apply_sync_snapshot` without changing public ownership.
- In Task 3, atomically rename the raw implementation to `_apply_sync_snapshot_backend`, rebind the adapter, and expose the stable public delegate while deleting `state_integrity.py`.
- Use narrow call-time lambdas for shared-runtime collaborators that tests and compatibility callers patch dynamically.

**Delete `bridge/state_integrity.py`**
- Its only remaining responsibility is replaced by the ordinary adapter.

**Modify `bridge/runtime_loader.py`**
- Remove `state_integrity.py` from `safety_overrides`.
- Remove its `apply_sync_snapshot` override allowlist entry.
- Do not change `sync_safety.py` entries.

**Create `tests/test_sync_integrity.py`**
- Ordinary-import unit tests for the adapter.
- Must not import `bridge.runtime`.

**Modify `tests/test_sync_phase3.py`**
- Keep the full remote-wins integration path pinned after the ownership cutover.
- Verify explicit clears and memory refresh through the public runtime path.

**Delete `tests/test_state_integrity.py`**
- Its one remaining Live Sync regression migrates to `test_sync_phase3.py` / `test_sync_integrity.py`.

**Modify `tests/test_runtime_loader.py`**
- Assert `apply_sync_snapshot` is owned by `sync_core.py`.
- Assert `state_integrity.py` is absent from stages and runtime load report.

**Do not modify `bridge/sync_service.py` or `bridge/sync_safety.py`.**

---

### Task 1: Add the Ordinary SyncSnapshotIntegrityAdapter

**Files:**
- Create: `bridge/sync_integrity.py`
- Create: `tests/test_sync_integrity.py`

**Interfaces:**
- Produces:
  - `SyncSnapshotIntegrityAdapter(apply_backend, update_session, load_session, retain_memory, card_fields, default_model, log_warning)`
  - `SyncSnapshotIntegrityAdapter.apply(db, chat_id, session, metadata, messages, variants) -> str`
- Consumes:
  - `apply_backend(db, chat_id, session, metadata, messages, variants) -> str`
  - `update_session(db, chat_id, session_id, **updates) -> object`
  - `load_session(db, chat_id, session_id, default_model) -> dict[str, str]`
  - `retain_memory(db, chat_id, session, fields) -> None`
  - `card_fields(character_file: str) -> dict[str, str]`
  - `log_warning(message: str, *, exc_info: bool) -> None`

- [ ] **Step 1: Write the failing ordinary-import tests**

Create `tests/test_sync_integrity.py`:

```python
import unittest

from bridge.sync_integrity import SyncSnapshotIntegrityAdapter


class SyncSnapshotIntegrityAdapterTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.updates = []
        self.retained = []
        self.warnings = []
        self.final_session = {
            "session_id": "session",
            "character_file": "final.png",
        }
        self.db = object()
        self.session = {
            "session_id": "session",
            "character_file": "before.png",
        }

        def apply_backend(
            db,
            chat_id,
            session,
            metadata,
            messages,
            variants,
        ):
            self.events.append(
                (
                    "backend",
                    db,
                    chat_id,
                    session,
                    metadata,
                    messages,
                    variants,
                )
            )
            return "raw-hash"

        def update_session(
            db,
            chat_id,
            session_id,
            **updates,
        ):
            self.events.append(
                ("update", db, chat_id, session_id, updates)
            )
            self.updates.append(updates)

        def load_session(
            db,
            chat_id,
            session_id,
            default_model,
        ):
            self.events.append(
                (
                    "load",
                    db,
                    chat_id,
                    session_id,
                    default_model,
                )
            )
            return dict(self.final_session)

        def card_fields(character_file):
            self.events.append(("card", character_file))
            return {"name": f"card:{character_file}"}

        def retain_memory(db, chat_id, session, fields):
            self.events.append(
                ("retain", db, chat_id, session, fields)
            )
            self.retained.append(
                (db, chat_id, session, fields)
            )

        def log_warning(message, *, exc_info):
            self.warnings.append((message, exc_info))

        self.adapter = SyncSnapshotIntegrityAdapter(
            apply_backend=apply_backend,
            update_session=update_session,
            load_session=load_session,
            retain_memory=retain_memory,
            card_fields=card_fields,
            default_model="provider/model",
            log_warning=log_warning,
        )

    def test_explicit_empty_persona_clears_persona_id(self):
        result = self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {"persona": ""},
            [("user", "remote")],
            {},
        )

        self.assertEqual(result, "raw-hash")
        self.assertEqual(
            self.updates,
            [{"persona_id": ""}],
        )

    def test_whitespace_persona_clears_persona_id(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {"persona": "   "},
            [("user", "remote")],
            {},
        )

        self.assertEqual(
            self.updates,
            [{"persona_id": ""}],
        )

    def test_absent_persona_does_not_clear_persona(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {},
            [("user", "remote")],
            {},
        )

        self.assertEqual(self.updates, [])

    def test_empty_world_list_clears_world_file(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {"world_info": []},
            [("user", "remote")],
            {},
        )

        self.assertEqual(
            self.updates,
            [{"world_file": ""}],
        )

    def test_world_none_clears_world_file(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {"world_info": None},
            [("user", "remote")],
            {},
        )

        self.assertEqual(
            self.updates,
            [{"world_file": ""}],
        )

    def test_world_empty_string_clears_world_file(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {"world_info": ""},
            [("user", "remote")],
            {},
        )

        self.assertEqual(
            self.updates,
            [{"world_file": ""}],
        )

    def test_absent_world_info_does_not_clear_world_file(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {},
            [("user", "remote")],
            {},
        )

        self.assertEqual(self.updates, [])

    def test_combined_explicit_clears_use_one_update(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {
                "persona": "",
                "world_info": [],
            },
            [("user", "remote")],
            {},
        )

        self.assertEqual(
            self.updates,
            [
                {
                    "persona_id": "",
                    "world_file": "",
                }
            ],
        )

    def test_non_empty_metadata_does_not_duplicate_backend_updates(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {
                "persona": "person.png",
                "world_info": ["world.json"],
            },
            [("user", "remote")],
            {},
        )

        self.assertEqual(self.updates, [])

    def test_backend_receives_original_arguments_and_hash_is_unchanged(self):
        metadata = {"name": "Remote"}
        messages = [("user", "one"), ("assistant", "two")]
        variants = {1: (["a", "b"], 1)}

        result = self.adapter.apply(
            self.db,
            "chat",
            self.session,
            metadata,
            messages,
            variants,
        )

        self.assertEqual(result, "raw-hash")
        backend = self.events[0]
        self.assertEqual(backend[0], "backend")
        self.assertIs(backend[1], self.db)
        self.assertEqual(backend[2], "chat")
        self.assertIs(backend[3], self.session)
        self.assertIs(backend[4], metadata)
        self.assertIs(backend[5], messages)
        self.assertIs(backend[6], variants)

    def test_refresh_uses_reloaded_final_session_and_character_file(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {},
            [("user", "remote")],
            {},
        )

        self.assertEqual(
            self.retained,
            [
                (
                    self.db,
                    "chat",
                    self.final_session,
                    {"name": "card:final.png"},
                )
            ],
        )
        self.assertIn(("card", "final.png"), self.events)
        self.assertNotIn(("card", "before.png"), self.events)

    def test_backend_error_propagates_and_stops_followup_work(self):
        expected = RuntimeError("backend failed")

        def fail_backend(*_args, **_kwargs):
            raise expected

        self.adapter = SyncSnapshotIntegrityAdapter(
            apply_backend=fail_backend,
            update_session=self.adapter.update_session,
            load_session=self.adapter.load_session,
            retain_memory=self.adapter.retain_memory,
            card_fields=self.adapter.card_fields,
            default_model=self.adapter.default_model,
            log_warning=self.adapter.log_warning,
        )

        with self.assertRaises(RuntimeError) as caught:
            self.adapter.apply(
                self.db,
                "chat",
                self.session,
                {"persona": ""},
                [("user", "remote")],
                {},
            )

        self.assertIs(caught.exception, expected)
        self.assertEqual(self.updates, [])
        self.assertEqual(self.retained, [])

    def test_explicit_clear_error_propagates_and_skips_refresh(self):
        expected = RuntimeError("clear failed")

        def fail_update(*_args, **_kwargs):
            raise expected

        self.adapter = SyncSnapshotIntegrityAdapter(
            apply_backend=self.adapter.apply_backend,
            update_session=fail_update,
            load_session=self.adapter.load_session,
            retain_memory=self.adapter.retain_memory,
            card_fields=self.adapter.card_fields,
            default_model=self.adapter.default_model,
            log_warning=self.adapter.log_warning,
        )

        with self.assertRaises(RuntimeError) as caught:
            self.adapter.apply(
                self.db,
                "chat",
                self.session,
                {"persona": ""},
                [("user", "remote")],
                {},
            )

        self.assertIs(caught.exception, expected)
        self.assertEqual(self.retained, [])

    def test_refresh_failure_is_logged_and_hash_still_returns(self):
        def fail_load(*_args, **_kwargs):
            raise RuntimeError("reload failed")

        self.adapter = SyncSnapshotIntegrityAdapter(
            apply_backend=self.adapter.apply_backend,
            update_session=self.adapter.update_session,
            load_session=fail_load,
            retain_memory=self.adapter.retain_memory,
            card_fields=self.adapter.card_fields,
            default_model=self.adapter.default_model,
            log_warning=self.adapter.log_warning,
        )

        result = self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {},
            [("user", "remote")],
            {},
        )

        self.assertEqual(result, "raw-hash")
        self.assertEqual(
            self.warnings,
            [
                (
                    "Could not refresh Hindsight after Live Sync import",
                    True,
                )
            ],
        )
```

- [ ] **Step 2: Add refresh-failure coverage for card parsing and retention**

Append:

```python
    def test_card_fields_failure_is_logged_and_isolated(self):
        def fail_card(_character_file):
            raise RuntimeError("card failed")

        self.adapter = SyncSnapshotIntegrityAdapter(
            apply_backend=self.adapter.apply_backend,
            update_session=self.adapter.update_session,
            load_session=self.adapter.load_session,
            retain_memory=self.adapter.retain_memory,
            card_fields=fail_card,
            default_model=self.adapter.default_model,
            log_warning=self.adapter.log_warning,
        )

        result = self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {},
            [("user", "remote")],
            {},
        )

        self.assertEqual(result, "raw-hash")
        self.assertEqual(len(self.warnings), 1)
        self.assertEqual(self.retained, [])

    def test_retain_failure_is_logged_and_isolated(self):
        def fail_retain(*_args, **_kwargs):
            raise RuntimeError("retain failed")

        self.adapter = SyncSnapshotIntegrityAdapter(
            apply_backend=self.adapter.apply_backend,
            update_session=self.adapter.update_session,
            load_session=self.adapter.load_session,
            retain_memory=fail_retain,
            card_fields=self.adapter.card_fields,
            default_model=self.adapter.default_model,
            log_warning=self.adapter.log_warning,
        )

        result = self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {},
            [("user", "remote")],
            {},
        )

        self.assertEqual(result, "raw-hash")
        self.assertEqual(len(self.warnings), 1)
```

- [ ] **Step 3: Run the ordinary adapter tests and verify RED**

Run:

```bash
python -m unittest tests.test_sync_integrity -v
```

Expected: import failure because `bridge/sync_integrity.py` does not exist.

- [ ] **Step 4: Implement the minimal ordinary adapter**

Create `bridge/sync_integrity.py`:

```python
"""Ordinary integrity adapter for Live Sync snapshot imports."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True)
class SyncSnapshotIntegrityAdapter:
    apply_backend: Callable[..., str]
    update_session: Callable[..., object]
    load_session: Callable[..., dict[str, str]]
    retain_memory: Callable[..., None]
    card_fields: Callable[[str], dict[str, str]]
    default_model: str
    log_warning: Callable[..., None]

    def apply(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session: dict[str, str],
        metadata: dict,
        messages: list[tuple[str, str]],
        variants: dict[int, tuple[list[str], int]],
    ) -> str:
        imported_hash = self.apply_backend(
            db,
            chat_id,
            session,
            metadata,
            messages,
            variants,
        )

        updates = {}
        if (
            "persona" in metadata
            and not str(metadata.get("persona") or "").strip()
        ):
            updates["persona_id"] = ""

        if "world_info" in metadata:
            raw_worlds = metadata.get("world_info")
            candidates = (
                raw_worlds
                if isinstance(raw_worlds, list)
                else ([raw_worlds] if raw_worlds else [])
            )
            if not candidates:
                updates["world_file"] = ""

        if updates:
            self.update_session(
                db,
                chat_id,
                session["session_id"],
                **updates,
            )

        try:
            refreshed = self.load_session(
                db,
                chat_id,
                session["session_id"],
                self.default_model,
            )
            fields = self.card_fields(
                refreshed["character_file"]
            )
            self.retain_memory(
                db,
                chat_id,
                refreshed,
                fields,
            )
        except Exception:
            self.log_warning(
                "Could not refresh Hindsight after Live Sync import",
                exc_info=True,
            )

        return imported_hash
```

- [ ] **Step 5: Run Task 1 tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_sync_integrity -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add   bridge/sync_integrity.py   tests/test_sync_integrity.py
git commit -m "refactor: add Live Sync integrity adapter"
```

---

### Task 2: Compose the Adapter in sync_core.py

**Files:**
- Modify: `bridge/sync_core.py`
- Modify: `tests/test_sync_phase3.py`

**Interfaces:**
- Consumes:
  - `SyncSnapshotIntegrityAdapter` from Task 1.
- Produces:
  - `_SYNC_SNAPSHOT_INTEGRITY: SyncSnapshotIntegrityAdapter` composed around the existing raw public `apply_sync_snapshot`.
  - Task 2 deliberately preserves the existing public raw `apply_sync_snapshot` name so the still-loaded Phase 6C `state_integrity.py` continues to wrap it exactly once until Task 3.

- [ ] **Step 1: Add public-path characterization coverage for absent metadata preservation**

Append to `Phase3SyncTests` in `tests/test_sync_phase3.py`:

```python
    def test_public_snapshot_absent_persona_and_world_preserve_assignments(self):
        rt.update_session(
            self.db,
            "chat",
            "phase3",
            persona_id="existing.png",
            world_file='["existing.json"]',
        )
        current = rt.load_session(
            self.db,
            "chat",
            "phase3",
            rt.DEFAULT_MODEL,
        )

        with patch.object(
            rt,
            "retain_session_memory",
            return_value=None,
        ), patch.object(
            rt,
            "card_fields_from_file",
            return_value={"name": "Test"},
        ):
            result = rt.apply_sync_snapshot(
                self.db,
                "chat",
                current,
                {"name": "Remote"},
                [("user", "remote transcript")],
                {},
            )

        refreshed = rt.load_session(
            self.db,
            "chat",
            "phase3",
            rt.DEFAULT_MODEL,
        )
        self.assertEqual(
            refreshed["persona_id"],
            "existing.png",
        )
        self.assertEqual(
            refreshed["world_file"],
            '["existing.json"]',
        )
        self.assertEqual(
            result,
            rt.sync_transcript_hash(
                [("user", "remote transcript")]
            ),
        )
```

- [ ] **Step 2: Add public-path characterization coverage for runtime collaborator patching**

Append:

```python
    def test_public_snapshot_uses_final_runtime_memory_collaborators(self):
        current = rt.load_session(
            self.db,
            "chat",
            "phase3",
            rt.DEFAULT_MODEL,
        )
        retained = []

        with patch.object(
            rt,
            "retain_session_memory",
            side_effect=lambda db, chat_id, session, fields:
                retained.append(
                    (
                        db,
                        chat_id,
                        session["session_id"],
                        fields["name"],
                    )
                ),
        ), patch.object(
            rt,
            "card_fields_from_file",
            return_value={"name": "patched-card"},
        ):
            rt.apply_sync_snapshot(
                self.db,
                "chat",
                current,
                {},
                [("user", "remote transcript")],
                {},
            )

        self.assertEqual(
            retained,
            [
                (
                    self.db,
                    "chat",
                    "phase3",
                    "patched-card",
                )
            ],
        )
```

This test requires call-time collaborator resolution. Binding `retain_session_memory` or `card_fields_from_file` directly into the adapter at module-execution time would make the test fail after `state_integrity.py` is removed.

- [ ] **Step 3: Run Task 2 tests and verify the current late-owner baseline**

Run:

```bash
python -m unittest   tests.test_sync_phase3.Phase3SyncTests.test_public_snapshot_absent_persona_and_world_preserve_assignments   tests.test_sync_phase3.Phase3SyncTests.test_public_snapshot_uses_final_runtime_memory_collaborators -v
```

Expected at this transition point: both tests PASS under the existing `state_integrity.py` late override. This is a behavioral characterization step, not the Task 2 architectural RED.

- [ ] **Step 4: Add a source-level RED assertion requiring explicit sync_core composition**

Append to `tests/test_sync_phase3.py`:

```python
class SyncSnapshotOwnershipTests(unittest.TestCase):
    def test_sync_core_composes_snapshot_integrity_adapter(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "sync_core.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "_SYNC_SNAPSHOT_INTEGRITY = _SyncSnapshotIntegrityAdapter(",
            source,
        )
        self.assertIn(
            "apply_backend=apply_sync_snapshot,",
            source,
        )
        self.assertNotIn(
            "def _apply_sync_snapshot_backend(",
            source,
        )
```

- [ ] **Step 5: Run the ownership assertion and verify RED**

Run:

```bash
python -m unittest   tests.test_sync_phase3.SyncSnapshotOwnershipTests.test_sync_core_composes_snapshot_integrity_adapter -v
```

Expected: FAIL because `sync_core.py` still defines only the raw public `apply_sync_snapshot`.

- [ ] **Step 6: Import the adapter in sync_core.py**

At the top of `bridge/sync_core.py`, add:

```python
from bridge.sync_integrity import (
    SyncSnapshotIntegrityAdapter as _SyncSnapshotIntegrityAdapter,
)
```

Do not import `bridge.runtime`, `state_integrity.py`, or `sync_safety.py`.

- [ ] **Step 7: Compose the adapter without cutting over the public owner yet**

Immediately after the existing raw public `apply_sync_snapshot` definition, add:

```python
_SYNC_SNAPSHOT_INTEGRITY = _SyncSnapshotIntegrityAdapter(
    apply_backend=apply_sync_snapshot,
    update_session=(
        lambda db, chat_id, session_id, **updates:
        update_session(
            db,
            chat_id,
            session_id,
            **updates,
        )
    ),
    load_session=(
        lambda db, chat_id, session_id, default_model:
        load_session(
            db,
            chat_id,
            session_id,
            default_model,
        )
    ),
    retain_memory=(
        lambda db, chat_id, session, fields:
        retain_session_memory(
            db,
            chat_id,
            session,
            fields,
        )
    ),
    card_fields=(
        lambda character_file:
        card_fields_from_file(character_file)
    ),
    default_model=DEFAULT_MODEL,
    log_warning=(
        lambda message, **kwargs:
        logging.warning(message, **kwargs)
    ),
)
```

Do **not** rename the raw public `apply_sync_snapshot` and do **not** route it through the adapter in Task 2. The still-loaded `state_integrity.py` must continue to capture and wrap the raw implementation exactly once.

- [ ] **Step 8: Add transition-state tests for direct adapter behavior and unchanged public ownership**

Append to `Phase3SyncTests`:

```python
    def test_direct_integrity_adapter_uses_final_runtime_memory_collaborators(self):
        current = rt.load_session(
            self.db,
            "chat",
            "phase3",
            rt.DEFAULT_MODEL,
        )
        retained = []

        with patch.object(
            rt,
            "retain_session_memory",
            side_effect=lambda db, chat_id, session, fields:
                retained.append(
                    (
                        db,
                        chat_id,
                        session["session_id"],
                        fields["name"],
                    )
                ),
        ), patch.object(
            rt,
            "card_fields_from_file",
            return_value={"name": "patched-card"},
        ):
            rt._SYNC_SNAPSHOT_INTEGRITY.apply(
                self.db,
                "chat",
                current,
                {},
                [("user", "remote transcript")],
                {},
            )

        self.assertEqual(
            retained,
            [
                (
                    self.db,
                    "chat",
                    "phase3",
                    "patched-card",
                )
            ],
        )

    def test_task2_preserves_state_integrity_public_owner(self):
        self.assertEqual(
            Path(
                rt.apply_sync_snapshot.__code__.co_filename
            ).name,
            "state_integrity.py",
        )
```

The first test proves the ordinary composition works directly. The second is an intentional transition-state assertion preventing an early cutover that would double-apply integrity behavior.

- [ ] **Step 9: Run Task 1 + Task 2 focused tests**

Run:

```bash
python -m unittest   tests.test_sync_integrity   tests.test_sync_phase3   tests.test_sync_service -v
```

Expected: PASS. The ordinary adapter is composed and tested directly, while the transition-state owner assertion confirms `state_integrity.py` still owns the public runtime function until Task 3.

- [ ] **Step 10: Commit Task 2**

```bash
git add   bridge/sync_core.py   tests/test_sync_phase3.py
git commit -m "refactor: compose Live Sync snapshot integrity"
```

---

### Task 3: Retire state_integrity.py and Cut Over Runtime Ownership

**Files:**
- Modify: `bridge/sync_core.py`
- Delete: `bridge/state_integrity.py`
- Modify: `bridge/runtime_loader.py`
- Delete: `tests/test_state_integrity.py`
- Modify: `tests/test_sync_phase3.py`
- Modify: `tests/test_runtime_loader.py`

**Interfaces:**
- Consumes:
  - `_SYNC_SNAPSHOT_INTEGRITY` ordinary composition from Task 2 while the public runtime owner is still intentionally `state_integrity.py`.
- Produces:
  - `_apply_sync_snapshot_backend(db, chat_id, session, metadata, messages, variants) -> str`;
  - stable public `sync_core.py::apply_sync_snapshot` delegate through `_SYNC_SNAPSHOT_INTEGRITY`;
  - no `state_integrity.py` runtime module;
  - no `state_integrity.py` allowlist entry;
  - no `_ORIGINAL_APPLY_SYNC_SNAPSHOT`.

- [ ] **Step 1: Add RED runtime ownership tests**

In `tests/test_runtime_loader.py`, add:

```python
    def test_sync_snapshot_public_owner_is_sync_core(self):
        self.assertEqual(
            Path(
                rt.apply_sync_snapshot.__code__.co_filename
            ).name,
            "sync_core.py",
        )

    def test_state_integrity_is_not_a_runtime_module(self):
        loaded = {
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn(
            "state_integrity.py",
            loaded,
        )

    def test_runtime_report_has_no_state_integrity_entry(self):
        modules = {
            item["module"]
            for item in rt.RUNTIME_LOAD_REPORT
        }
        self.assertNotIn(
            "state_integrity.py",
            modules,
        )

    def test_apply_sync_snapshot_has_no_runtime_override_allowlist(self):
        for stage in DEFAULT_RUNTIME_STAGES:
            for filename, names in (
                stage.allowed_public_callable_overrides
            ):
                self.assertNotIn(
                    "apply_sync_snapshot",
                    names,
                    msg=f"{filename} still overrides apply_sync_snapshot",
                )
```

- [ ] **Step 2: Add RED source-boundary tests**

Append to `SyncSnapshotOwnershipTests` in `tests/test_sync_phase3.py`:

```python
    def test_state_integrity_module_is_retired(self):
        path = (
            Path(__file__).parents[1]
            / "bridge"
            / "state_integrity.py"
        )
        self.assertFalse(path.exists())

    def test_no_original_apply_sync_snapshot_capture_remains(self):
        root = Path(__file__).parents[1] / "bridge"
        offenders = []
        for path in root.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "_ORIGINAL_APPLY_SYNC_SNAPSHOT" in source:
                offenders.append(path.name)

        self.assertEqual(offenders, [])
```

- [ ] **Step 3: Run Task 3 architecture tests and verify RED**

Run:

```bash
python -m unittest   tests.test_runtime_loader.RuntimeLoaderTests.test_sync_snapshot_public_owner_is_sync_core   tests.test_runtime_loader.RuntimeLoaderTests.test_state_integrity_is_not_a_runtime_module   tests.test_runtime_loader.RuntimeLoaderTests.test_runtime_report_has_no_state_integrity_entry   tests.test_runtime_loader.RuntimeLoaderTests.test_apply_sync_snapshot_has_no_runtime_override_allowlist   tests.test_sync_phase3.SyncSnapshotOwnershipTests.test_state_integrity_module_is_retired   tests.test_sync_phase3.SyncSnapshotOwnershipTests.test_no_original_apply_sync_snapshot_capture_remains -v
```

Expected: failures because `state_integrity.py` is still loaded and still captures/replaces `apply_sync_snapshot`.

- [ ] **Step 4: Migrate the final state-integrity integration regression into Phase3SyncTests**

Move the behavior from:

```text
tests/test_state_integrity.py::
StateIntegrityTests.test_live_sync_explicitly_clears_persona_and_world_and_refreshes_memory
```

into `tests/test_sync_phase3.py` as:

```python
    def test_public_snapshot_explicitly_clears_persona_and_world_and_refreshes_memory(self):
        rt.update_session(
            self.db,
            "chat",
            "phase3",
            persona_id="existing.png",
            world_file='["existing.json"]',
        )
        current = rt.load_session(
            self.db,
            "chat",
            "phase3",
            rt.DEFAULT_MODEL,
        )
        retained = []

        with patch.object(
            rt,
            "retain_session_memory",
            side_effect=lambda _db, chat_id, session, fields:
                retained.append(
                    (
                        chat_id,
                        session["session_id"],
                        fields["name"],
                    )
                ),
        ), patch.object(
            rt,
            "card_fields_from_file",
            return_value={"name": "Test"},
        ):
            imported_hash = rt.apply_sync_snapshot(
                self.db,
                "chat",
                current,
                {
                    "name": "Remote",
                    "persona": "",
                    "world_info": [],
                },
                [("user", "remote transcript")],
                {},
            )

        refreshed = rt.load_session(
            self.db,
            "chat",
            "phase3",
            rt.DEFAULT_MODEL,
        )
        self.assertEqual(refreshed["persona_id"], "")
        self.assertEqual(refreshed["world_file"], "")
        self.assertEqual(
            retained,
            [("chat", "phase3", "Test")],
        )
        self.assertEqual(
            imported_hash,
            rt.sync_transcript_hash(
                [("user", "remote transcript")]
            ),
        )
```

Delete `tests/test_state_integrity.py` after this migration.

- [ ] **Step 5: Atomically cut sync_core.py public ownership over to the adapter**

In `bridge/sync_core.py`, rename the existing raw function:

```python
# before
def apply_sync_snapshot(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    metadata: dict,
    messages: list[tuple[str, str]],
    variants: dict[int, tuple[list[str], int]],
) -> str:

# after
def _apply_sync_snapshot_backend(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    metadata: dict,
    messages: list[tuple[str, str]],
    variants: dict[int, tuple[list[str], int]],
) -> str:
```

Keep the entire raw function body statement-equivalent to baseline.

Update the existing adapter composition from:

```python
    apply_backend=apply_sync_snapshot,
```

to:

```python
    apply_backend=_apply_sync_snapshot_backend,
```

Then define the stable public delegate after the adapter:

```python
def apply_sync_snapshot(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    metadata: dict,
    messages: list[tuple[str, str]],
    variants: dict[int, tuple[list[str], int]],
) -> str:
    return _SYNC_SNAPSHOT_INTEGRITY.apply(
        db,
        chat_id,
        session,
        metadata,
        messages,
        variants,
    )
```

Delete the Task 2 transition-state test `test_task2_preserves_state_integrity_public_owner`; Task 3 runtime-owner tests now require the opposite final ownership.

- [ ] **Step 6: Delete state_integrity.py from the runtime**

Delete:

```text
bridge/state_integrity.py
```

Do not move any code from it elsewhere; the Task 2 adapter plus the Task 3 atomic `sync_core.py` cutover already provide its replacement.

- [ ] **Step 7: Remove state_integrity.py and its allowlist from runtime_loader.py**

Change the `safety_overrides` modules from:

```python
(
    "sync_safety.py",
    "state_integrity.py",
    "scene_state.py",
    "director_goals.py",
    "memory_curator.py",
)
```

to:

```python
(
    "sync_safety.py",
    "scene_state.py",
    "director_goals.py",
    "memory_curator.py",
)
```

Remove:

```python
(
    "state_integrity.py",
    (
        "apply_sync_snapshot",
    ),
),
```

Do not change:

```python
("sync_safety.py", ("initialize_database_schema", "phase3_sync_poll"))
```

- [ ] **Step 8: Run the focused Sync/runtime suite**

Run:

```bash
python -m unittest   tests.test_sync_integrity   tests.test_sync_phase3   tests.test_sync_service   tests.test_runtime_loader   tests.test_composition -v
```

Expected: PASS.

- [ ] **Step 9: Run source/architecture guards**

Run:

```bash
python - <<'PY'
from pathlib import Path
import bridge.runtime as rt
from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

root = Path("bridge")
assert not (root / "state_integrity.py").exists()

for path in root.glob("*.py"):
    source = path.read_text(encoding="utf-8")
    assert "_ORIGINAL_APPLY_SYNC_SNAPSHOT" not in source

assert (
    Path(
        rt.apply_sync_snapshot.__code__.co_filename
    ).name
    == "sync_core.py"
)

loaded = {
    module
    for stage in DEFAULT_RUNTIME_STAGES
    for module in stage.modules
}
assert "state_integrity.py" not in loaded

for stage in DEFAULT_RUNTIME_STAGES:
    for filename, names in (
        stage.allowed_public_callable_overrides
    ):
        assert "apply_sync_snapshot" not in names, (
            filename,
            names,
        )

report_modules = {
    item["module"]
    for item in rt.RUNTIME_LOAD_REPORT
}
assert "state_integrity.py" not in report_modules

print("Phase 6D Sync ownership verified")
PY
```

Expected:

```text
Phase 6D Sync ownership verified
```

- [ ] **Step 10: Commit Task 3**

```bash
git add   bridge/sync_core.py   bridge/runtime_loader.py   tests/test_sync_phase3.py   tests/test_runtime_loader.py
git rm   bridge/state_integrity.py   tests/test_state_integrity.py
git commit -m "refactor: retire Live Sync state-integrity override"
```

---

### Task 4: Exact-Head Verification and PR Readiness

**Files:**
- Modify: `docs/superpowers/plans/2026-09-20-phase-6d-live-sync-integrity.md`
- No production file changes unless verification finds a defect. Any defect returns to the owning task's RED -> GREEN cycle.

**Interfaces:**
- Consumes: completed Phase 6D branch.
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

- [ ] **Step 4: Validate dependency consistency**

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

If local `pip-audit` is unavailable, do not install unrelated tooling just for this command; GitHub Actions dependency audit is authoritative.

- [ ] **Step 6: Verify Phase 6D non-scope equivalence**

Compare these files against baseline:

```text
e23d9750610bd2b6987d3a59933ff574f1b0523f
```

Required:

```text
bridge/sync_safety.py
bridge/sync_service.py
```

Both must have no Phase 6D diff.

Also compare the body of final:

```text
bridge/sync_core.py::_apply_sync_snapshot_backend
```

against baseline:

```text
bridge/sync_core.py::apply_sync_snapshot
```

The raw backend body must be statement-equivalent aside from the function name/formatting.

- [ ] **Step 7: Verify phase3_sync_now remained structurally unchanged**

Compare final:

```text
bridge/sync_api.py::phase3_sync_now
```

against baseline `e23d9750610bd2b6987d3a59933ff574f1b0523f`.

The function body must be byte-for-byte identical.

If not, revert unrelated changes before proceeding.

- [ ] **Step 8: Run final architecture verification**

Run:

```bash
python - <<'PY'
from pathlib import Path
import bridge.runtime as rt
from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

assert (
    Path(
        rt.apply_sync_snapshot.__code__.co_filename
    ).name
    == "sync_core.py"
)

assert not Path("bridge/state_integrity.py").exists()
assert not Path("tests/test_state_integrity.py").exists()

for path in Path("bridge").glob("*.py"):
    source = path.read_text(encoding="utf-8")
    assert "_ORIGINAL_APPLY_SYNC_SNAPSHOT" not in source

stage = next(
    item
    for item in DEFAULT_RUNTIME_STAGES
    if item.name == "safety_overrides"
)
assert "state_integrity.py" not in stage.modules
assert (
    stage.allowed_overrides_for("state_integrity.py")
    == frozenset()
)

assert (
    stage.allowed_overrides_for("sync_safety.py")
    == frozenset(
        {
            "initialize_database_schema",
            "phase3_sync_poll",
        }
    )
)

assert all(
    item["module"] != "state_integrity.py"
    for item in rt.RUNTIME_LOAD_REPORT
)

print("Phase 6D final architecture verified")
PY
```

Expected:

```text
Phase 6D final architecture verified
```

- [ ] **Step 9: Record execution evidence in this plan**

Append an `## Execution Evidence` section containing:

- Task-level RED commit SHAs and exact expected failures;
- matching GREEN commit SHAs;
- any execution rulings and their cost-if-wrong notes;
- final implementation head SHA;
- unittest result;
- pytest result;
- `pip check` result;
- dependency audit result;
- architecture scan result;
- raw backend equivalence result;
- `phase3_sync_now` equivalence result;
- `sync_safety.py` / `sync_service.py` non-scope result;
- current upstream `main` SHA;
- upstream drift/reconciliation decision.

Do not claim exact-head CI success until the workflow has completed on that exact head.

- [ ] **Step 10: Commit verification documentation**

Run:

```bash
git add   docs/superpowers/plans/2026-09-20-phase-6d-live-sync-integrity.md
git commit -m "docs: record Phase 6D verification"
```

- [ ] **Step 11: Open or update a Draft PR**

Target:

```text
base: cepeter/SillyTavern-Telegram-Bridge:main
head: punzer4-code:refactor/phase-6d-live-sync-integrity
```

Title:

```text
refactor: retire Live Sync integrity runtime override
```

The PR body must state:

- `SyncSnapshotIntegrityAdapter` now owns explicit-clear and post-import refresh policy;
- `sync_core.py` is the canonical `apply_sync_snapshot` owner;
- `state_integrity.py` is deleted;
- empty Persona/world clears remain preserved;
- absent Persona/world metadata remains non-destructive;
- raw snapshot/hash behavior is preserved;
- post-import Hindsight refresh remains best-effort;
- `SyncService`, `phase3_sync_now`, and `sync_safety.py` are intentionally unchanged;
- RED -> GREEN evidence and exact verification results.

Keep the PR Draft until exact-head CI succeeds and review is clear.

- [ ] **Step 12: Check upstream drift**

Compare the feature branch merge base with current upstream `main`.

If upstream has not changed, continue.

If upstream changed only unrelated files, document that result.

If upstream changed any Phase 6D implementation/test file, inspect and reconcile it, then rerun affected focused tests and full verification.

- [ ] **Step 13: Inspect GitHub Actions on the exact final head**

Required successful steps:

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

- [ ] **Step 14: Review the whole branch and PR feedback**

Before Ready-for-review, verify:

- no Critical or Important code-review findings remain;
- no unresolved review threads;
- no unaddressed review comments;
- PR is mergeable;
- exact PR head equals the green CI SHA.

If no independent reviewer/subagent capability exists in the harness, perform the Superpowers fallback whole-branch self-review and explicitly record that limitation in the PR body.

- [ ] **Step 15: Mark the PR Ready for review**

Only after Steps 13-14 pass.

Do not merge the PR. Merge is a separate user decision.


## Execution Ledger (in progress)

- Ruling: This harness has no local Git checkout/worktree. The isolated feature branch `punzer4-code:refactor/phase-6d-live-sync-integrity` is the execution workspace and Draft PR GitHub Actions is the authoritative RED -> GREEN runner. Cost if wrong: focused local commands cannot be recorded separately, but each RED/GREEN state is observed on an exact repository SHA.
- Pre-flight: Task 1 produces `SyncSnapshotIntegrityAdapter`, consumed by Task 2; signatures match the plan/spec.
- Pre-flight: Task 2 produces `_SYNC_SNAPSHOT_INTEGRITY` while intentionally preserving the public `state_integrity.py` owner, consumed by Task 3's atomic cutover; transition contract is explicit and consistent.
- Pre-flight: Task 3 produces canonical `sync_core.py::apply_sync_snapshot` ownership and deletes `state_integrity.py`, consumed by Task 4 verification; acceptance criteria align.

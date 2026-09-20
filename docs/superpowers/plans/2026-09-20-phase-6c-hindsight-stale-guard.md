# Phase 6C Hindsight Stale Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Hindsight late overrides in `state_integrity.py` with an ordinary `HindsightStaleGuard` composed by `memory.py`, preserving stale-retain rejection, fail-closed purge semantics, per-session serialization, document-mapping cleanup, and post-retain hooks.

**Architecture:** `memory.py` remains the canonical Hindsight infrastructure owner and exposes the stable public retain/purge functions. A new ordinary-import `HindsightStaleGuard` owns stale-snapshot and purge-invalidation policy around private raw Hindsight backends; `MemoryService` remains unchanged and continues to receive the final public functions.

**Tech Stack:** Python 3.11, stdlib `dataclasses`, `sqlite3`, `threading`, `unittest`, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-20-phase-6c-hindsight-stale-guard-design.md`

## Global Constraints

- Queued Hindsight retains must not restore older transcript state after the transcript changes.
- Queued retains must not restore session memory after a successful purge.
- Deleted sessions must not be retained by background work.
- Remote Hindsight purge remains fail-closed.
- Local purge epoch and document mappings change only after successful remote purge.
- Successful purge clears the session's `hindsight_documents` mappings and increments `hindsight_epoch:<chat_id>:<session_id>` exactly once.
- Retain and purge for the same session remain serialized by the existing per-session `RLock`.
- Unrelated sessions remain independently executable.
- Post-retain hooks continue to run with current extension-registry semantics, including when Hindsight memory mode is off.
- `MemoryService` public structure and application workflow remain unchanged.
- Live Sync `apply_sync_snapshot`, `sync_safety.py`, recall/search, explicit `remember_fact`, Memory Curator policy, summary generation, document IDs/tags, database schema, recovery overrides, and runtime-loader retirement are out of scope.
- No new public runtime override, `_ORIGINAL_*` capture, runtime stage, service locator, or runtime-order dependency.

## Review Focus

- Memory mode is off: no Hindsight retain is queued, but post-retain hooks still run exactly once.
- Remote purge fails after mappings already exist: the exception propagates and neither mappings nor purge epoch change.
- A queued retain is released after its session row has been deleted: the worker exits without calling the retain backend.
- A malformed or negative persisted purge epoch is read: it behaves as epoch `0`, matching current defensive semantics.
- Retain stale-check and purge start concurrently for the same session: the shared session lock prevents purge from interleaving between stale validation and backend retain dispatch.

---

## File Structure

**Create `bridge/hindsight_integrity.py`**
- Ordinary-import stale-memory policy only.
- Defines `HindsightStaleGuard`.
- Owns foreground retain scheduling, guarded background validation, and purge ordering.
- Imports no `bridge.runtime`, `memory.py`, Telegram/UI module, or `state_integrity.py`.

**Modify `bridge/memory.py`**
- Import `HindsightStaleGuard` and `run_post_retain_hooks`.
- Own canonical epoch/snapshot/session-existence/local-purge-state helpers under names that cannot collide with the still-loaded Phase 6B state-integrity helpers.
- Compose `_HINDSIGHT_STALE_GUARD` first without changing public ownership.
- During the Task 3 cutover, rename current raw retain/purge implementations to private backend functions and expose stable public delegates through the guard.

**Modify `bridge/state_integrity.py`**
- Remove Hindsight captures, epoch/snapshot helpers, private worker replacement, and public retain/purge replacements.
- Keep only Live Sync integrity behavior.

**Modify `bridge/runtime_loader.py`**
- Remove `retain_session_memory` and `purge_hindsight_session` from `state_integrity.py`'s allowlist.
- Keep `apply_sync_snapshot` as the sole allowed state-integrity override.

**Create `tests/test_hindsight_integrity.py`**
- Ordinary unit tests for `HindsightStaleGuard`.
- Must not import `bridge.runtime`.

**Create `tests/test_memory_native_backend.py`**
- Runtime/native integration tests for snapshot/epoch persistence, real guard composition, Hindsight retain/purge behavior, and document mappings.

**Modify `tests/test_state_integrity.py`**
- Remove migrated Hindsight tests.
- Keep the Live Sync test.

**Modify `tests/test_runtime_loader.py`**
- Assert final Hindsight owners are `memory.py`.
- Assert the state-integrity allowlist/report contain only the Live Sync replacement.

**Do not modify `bridge/memory_service.py`**
- Its injected collaborator interface is already the desired application boundary.

---

### Task 1: Add the Ordinary HindsightStaleGuard

**Files:**
- Create: `bridge/hindsight_integrity.py`
- Create: `tests/test_hindsight_integrity.py`

**Interfaces:**
- Produces:
  - `HindsightStaleGuard(open_db, session_lock, memory_enabled, submit_background, session_exists, read_epoch, snapshot, retain_backend, purge_backend, write_successful_purge_state, run_post_retain_hooks)`
  - `HindsightStaleGuard.retain(db, chat_id: str, session: dict[str, str], fields: dict[str, str]) -> None`
  - `HindsightStaleGuard.purge(db, chat_id: str, session_id: str) -> int`
- Consumes:
  - `open_db() -> sqlite3.Connection`
  - `session_lock(chat_id: str, session_id: str) -> context manager`
  - `memory_enabled(db, chat_id: str) -> bool`
  - `submit_background(name: str, fn, *args, **kwargs) -> object`
  - `session_exists(db, chat_id: str, session_id: str) -> bool`
  - `read_epoch(db, chat_id: str, session_id: str) -> int`
  - `snapshot(db, chat_id: str, session_id: str) -> tuple[str, str]`
  - `retain_backend(chat_id: str, session: dict[str, str], character_name: str, conversation: str) -> None`
  - `purge_backend(db, chat_id: str, session_id: str) -> int`
  - `write_successful_purge_state(db, chat_id: str, session_id: str) -> None`
  - `run_post_retain_hooks(db, chat_id: str, session: dict[str, str], fields: dict[str, str]) -> None`

- [ ] **Step 1: Write the failing ordinary-import tests**

Create `tests/test_hindsight_integrity.py`:

```python
import sqlite3
import threading
import time
import unittest

from bridge.hindsight_integrity import HindsightStaleGuard


class HindsightStaleGuardTests(unittest.TestCase):
    def setUp(self):
        self.lock = threading.RLock()
        self.enabled = True
        self.exists = True
        self.epoch = 3
        self.current_snapshot = (
            '[{"role":"user","content":"hello"}]',
            "snapshot-a",
        )
        self.queued = []
        self.retained = []
        self.hooks = []
        self.purge_calls = []
        self.local_purge_writes = []
        self.connection = sqlite3.connect(":memory:")

        def submit_background(name, fn, *args, **kwargs):
            self.queued.append(
                (name, fn, args, kwargs)
            )
            return object()

        self.guard = HindsightStaleGuard(
            open_db=lambda: sqlite3.connect(":memory:"),
            session_lock=lambda _chat_id, _session_id: self.lock,
            memory_enabled=lambda _db, _chat_id: self.enabled,
            submit_background=submit_background,
            session_exists=lambda _db, _chat_id, _session_id: self.exists,
            read_epoch=lambda _db, _chat_id, _session_id: self.epoch,
            snapshot=lambda _db, _chat_id, _session_id: self.current_snapshot,
            retain_backend=lambda chat_id, session, name, conversation: (
                self.retained.append(
                    (
                        chat_id,
                        session["session_id"],
                        name,
                        conversation,
                    )
                )
            ),
            purge_backend=lambda _db, chat_id, session_id: (
                self.purge_calls.append(
                    (chat_id, session_id)
                )
                or 4
            ),
            write_successful_purge_state=(
                lambda _db, chat_id, session_id:
                self.local_purge_writes.append(
                    (chat_id, session_id)
                )
            ),
            run_post_retain_hooks=(
                lambda _db, chat_id, session, fields:
                self.hooks.append(
                    (
                        chat_id,
                        session["session_id"],
                        fields["name"],
                    )
                )
            ),
        )
        self.session = {"session_id": "session"}
        self.fields = {"name": "Mira"}

    def tearDown(self):
        self.connection.close()

    def _run_queued_retain(self):
        self.assertEqual(len(self.queued), 1)
        name, fn, args, kwargs = self.queued[0]
        self.assertEqual(name, "hindsight_retain")
        fn(*args, **kwargs)

    def test_current_snapshot_reaches_retain_backend(self):
        self.guard.retain(
            self.connection,
            "chat",
            self.session,
            self.fields,
        )

        self._run_queued_retain()

        self.assertEqual(
            self.retained,
            [
                (
                    "chat",
                    "session",
                    "Mira",
                    self.current_snapshot[0],
                )
            ],
        )
        self.assertEqual(
            self.hooks,
            [("chat", "session", "Mira")],
        )

    def test_transcript_mismatch_skips_retain_backend(self):
        self.guard.retain(
            self.connection,
            "chat",
            self.session,
            self.fields,
        )
        self.current_snapshot = (
            '[{"role":"user","content":"edited"}]',
            "snapshot-b",
        )

        self._run_queued_retain()

        self.assertEqual(self.retained, [])

    def test_epoch_mismatch_skips_retain_backend(self):
        self.guard.retain(
            self.connection,
            "chat",
            self.session,
            self.fields,
        )
        self.epoch += 1

        self._run_queued_retain()

        self.assertEqual(self.retained, [])

    def test_deleted_session_skips_retain_backend(self):
        self.guard.retain(
            self.connection,
            "chat",
            self.session,
            self.fields,
        )
        self.exists = False

        self._run_queued_retain()

        self.assertEqual(self.retained, [])

    def test_memory_off_still_runs_post_retain_hooks(self):
        self.enabled = False

        self.guard.retain(
            self.connection,
            "chat",
            self.session,
            self.fields,
        )

        self.assertEqual(self.queued, [])
        self.assertEqual(
            self.hooks,
            [("chat", "session", "Mira")],
        )

    def test_successful_purge_writes_local_state_after_backend(self):
        order = []

        guard = HindsightStaleGuard(
            open_db=lambda: sqlite3.connect(":memory:"),
            session_lock=lambda _chat_id, _session_id: self.lock,
            memory_enabled=lambda _db, _chat_id: True,
            submit_background=lambda *_args, **_kwargs: None,
            session_exists=lambda *_args: True,
            read_epoch=lambda *_args: 0,
            snapshot=lambda *_args: ("", ""),
            retain_backend=lambda *_args: None,
            purge_backend=lambda *_args: (
                order.append("remote") or 7
            ),
            write_successful_purge_state=lambda *_args: (
                order.append("local")
            ),
            run_post_retain_hooks=lambda *_args: None,
        )

        deleted = guard.purge(
            self.connection,
            "chat",
            "session",
        )

        self.assertEqual(deleted, 7)
        self.assertEqual(order, ["remote", "local"])

    def test_failed_purge_does_not_write_local_state(self):
        original = RuntimeError(
            "Hindsight session memory cleanup failed"
        )

        guard = HindsightStaleGuard(
            open_db=lambda: sqlite3.connect(":memory:"),
            session_lock=lambda _chat_id, _session_id: self.lock,
            memory_enabled=lambda _db, _chat_id: True,
            submit_background=lambda *_args, **_kwargs: None,
            session_exists=lambda *_args: True,
            read_epoch=lambda *_args: 0,
            snapshot=lambda *_args: ("", ""),
            retain_backend=lambda *_args: None,
            purge_backend=lambda *_args: (
                (_ for _ in ()).throw(original)
            ),
            write_successful_purge_state=lambda *_args: (
                self.local_purge_writes.append("unexpected")
            ),
            run_post_retain_hooks=lambda *_args: None,
        )

        with self.assertRaises(RuntimeError) as caught:
            guard.purge(
                self.connection,
                "chat",
                "session",
            )

        self.assertIs(caught.exception, original)
        self.assertEqual(
            self.local_purge_writes,
            [],
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add the same-session serialization regression**

Append to `HindsightStaleGuardTests`:

```python
    def test_retain_validation_and_purge_are_serialized_per_session(self):
        active = 0
        maximum = 0
        counter_lock = threading.Lock()
        barrier = threading.Barrier(3)
        errors = []

        def enter_backend():
            nonlocal active, maximum
            with counter_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with counter_lock:
                active -= 1

        guard = HindsightStaleGuard(
            open_db=lambda: sqlite3.connect(":memory:"),
            session_lock=lambda _chat_id, _session_id: self.lock,
            memory_enabled=lambda _db, _chat_id: True,
            submit_background=lambda *_args, **_kwargs: None,
            session_exists=lambda *_args: True,
            read_epoch=lambda *_args: 1,
            snapshot=lambda *_args: ("payload", "hash"),
            retain_backend=lambda *_args: enter_backend(),
            purge_backend=lambda *_args: (
                enter_backend() or 1
            ),
            write_successful_purge_state=lambda *_args: None,
            run_post_retain_hooks=lambda *_args: None,
        )

        def retain_worker():
            try:
                barrier.wait(timeout=2)
                guard._retain_if_current(
                    "chat",
                    {"session_id": "session"},
                    "Mira",
                    "payload",
                    "hash",
                    1,
                )
            except Exception as exc:
                errors.append(exc)

        def purge_worker():
            try:
                barrier.wait(timeout=2)
                guard.purge(
                    self.connection,
                    "chat",
                    "session",
                )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=retain_worker),
            threading.Thread(target=purge_worker),
        ]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=2)

        self.assertEqual(errors, [])
        self.assertEqual(maximum, 1)
```

This test intentionally exercises the guard's background worker directly because serialization is a property of the guard boundary, not the thread-pool scheduler.

- [ ] **Step 3: Run the focused unit test and verify RED**

Run:

```bash
python -m unittest tests.test_hindsight_integrity -v
```

Expected: import failure because `bridge/hindsight_integrity.py` does not exist.

- [ ] **Step 4: Implement the minimal ordinary guard**

Create `bridge/hindsight_integrity.py`:

```python
"""Ordinary stale-snapshot guard for Hindsight session memory."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import logging
import sqlite3


@dataclass(frozen=True)
class HindsightStaleGuard:
    open_db: Callable[[], sqlite3.Connection]
    session_lock: Callable[
        [str, str],
        AbstractContextManager[object],
    ]
    memory_enabled: Callable[
        [sqlite3.Connection, str],
        bool,
    ]
    submit_background: Callable[..., object]
    session_exists: Callable[
        [sqlite3.Connection, str, str],
        bool,
    ]
    read_epoch: Callable[
        [sqlite3.Connection, str, str],
        int,
    ]
    snapshot: Callable[
        [sqlite3.Connection, str, str],
        tuple[str, str],
    ]
    retain_backend: Callable[..., None]
    purge_backend: Callable[..., int]
    write_successful_purge_state: Callable[
        [sqlite3.Connection, str, str],
        None,
    ]
    run_post_retain_hooks: Callable[..., None]

    def retain(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session: dict[str, str],
        fields: dict[str, str],
    ) -> None:
        if self.memory_enabled(db, chat_id):
            conversation, snapshot_hash = self.snapshot(
                db,
                chat_id,
                session["session_id"],
            )
            if conversation:
                snapshot_epoch = self.read_epoch(
                    db,
                    chat_id,
                    session["session_id"],
                )
                self.submit_background(
                    "hindsight_retain",
                    self._retain_if_current,
                    chat_id,
                    dict(session),
                    fields["name"],
                    conversation,
                    snapshot_hash,
                    snapshot_epoch,
                )
        self.run_post_retain_hooks(
            db,
            chat_id,
            session,
            fields,
        )

    def _retain_if_current(
        self,
        chat_id: str,
        session: dict[str, str],
        character_name: str,
        conversation: str,
        snapshot_hash: str = "",
        snapshot_epoch: int | None = None,
    ) -> None:
        session_id = str(session["session_id"])
        with self.session_lock(chat_id, session_id):
            session_db = self.open_db()
            try:
                if not self.session_exists(
                    session_db,
                    chat_id,
                    session_id,
                ):
                    return
                current_epoch = self.read_epoch(
                    session_db,
                    chat_id,
                    session_id,
                )
                (
                    current_conversation,
                    current_hash,
                ) = self.snapshot(
                    session_db,
                    chat_id,
                    session_id,
                )
            finally:
                session_db.close()

            if (
                snapshot_epoch is not None
                and current_epoch != int(snapshot_epoch)
            ):
                logging.info(
                    "Skipping stale Hindsight retain after "
                    "session-memory purge for %s/%s",
                    chat_id,
                    session_id,
                )
                return
            if (
                snapshot_hash
                and current_hash != snapshot_hash
            ):
                logging.info(
                    "Skipping stale Hindsight retain after "
                    "transcript change for %s/%s",
                    chat_id,
                    session_id,
                )
                return

            payload = (
                current_conversation
                if snapshot_hash
                else conversation
            )
            if not payload:
                return

            self.retain_backend(
                chat_id,
                session,
                character_name,
                payload,
            )

    def purge(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        session_id: str,
    ) -> int:
        with self.session_lock(chat_id, session_id):
            deleted = self.purge_backend(
                db,
                chat_id,
                session_id,
            )
            self.write_successful_purge_state(
                db,
                chat_id,
                session_id,
            )
            return int(deleted)
```

- [ ] **Step 6: Run Task 1 tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_hindsight_integrity -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add   bridge/hindsight_integrity.py   tests/test_hindsight_integrity.py
git commit -m "refactor: add hindsight stale guard"
```

---

### Task 2: Compose the Guard in memory.py

**Files:**
- Modify: `bridge/memory.py`
- Create: `tests/test_memory_native_backend.py`

**Interfaces:**
- Consumes:
  - `HindsightStaleGuard` from Task 1.
- Produces:
  - `_memory_memory_memory_hindsight_epoch_key(chat_id: str, session_id: str) -> str`
  - `_memory_hindsight_epoch(db, chat_id: str, session_id: str) -> int`
  - `_memory_memory_memory_hindsight_conversation_snapshot(db, chat_id: str, session_id: str) -> tuple[str, str]`
  - `_memory_memory_memory_hindsight_session_exists(db, chat_id: str, session_id: str) -> bool`
  - `_write_hindsight_successful_purge_state(db, chat_id: str, session_id: str) -> None`
  - `_HINDSIGHT_STALE_GUARD: HindsightStaleGuard`
  - Task 2 deliberately preserves the existing public/raw `retain_session_memory`, `purge_hindsight_session`, and `_retain_session_memory` names so the still-loaded Phase 6B `state_integrity.py` continues to execute unchanged until Task 3.

- [ ] **Step 1: Add RED native-boundary tests**

Create `tests/test_memory_native_backend.py`:

```python
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import bridge.runtime as rt


class _FakeDocuments:
    def __init__(self):
        self.documents = {}
        self.deleted = []
        self.api_client = self

    async def close(self):
        return None

    async def list_documents(
        self,
        bank_id,
        q=None,
        tags=None,
        tags_match=None,
        limit=1000,
        offset=0,
    ):
        items = []
        for document_id, document_tags in self.documents.items():
            if q and q.casefold() not in document_id.casefold():
                continue
            if (
                tags
                and not set(tags).intersection(
                    set(document_tags)
                )
            ):
                continue
            items.append(
                SimpleNamespace(
                    id=document_id,
                    tags=document_tags,
                )
            )
        page = items[offset:offset + limit]
        return SimpleNamespace(
            items=page,
            total=len(items),
            limit=limit,
            offset=offset,
        )

    async def delete_document(
        self,
        bank_id,
        document_id,
    ):
        self.deleted.append(document_id)
        self.documents.pop(document_id, None)
        return SimpleNamespace(success=True)


class _FakeHindsight:
    def __init__(self):
        self.documents = _FakeDocuments()
        self.retained = []

    def retain(self, **kwargs):
        self.retained.append(kwargs)
        self.documents.documents[
            kwargs["document_id"]
        ] = list(kwargs.get("tags") or [])
        return SimpleNamespace(success=True)


class MemoryNativeBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = rt.DB_FILE
        self.old_hindsight = rt.hindsight_client
        rt.DB_FILE = (
            Path(self.tmp.name) / "bridge.sqlite3"
        )
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.session = rt.create_session(
            self.db,
            "chat",
            rt.DEFAULT_MODEL,
            session_id="memory-native",
        )
        self.fields = {"name": "Mira"}

    def tearDown(self):
        rt.hindsight_client = self.old_hindsight
        self.db.close()
        rt.DB_FILE = self.old_db
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.tmp.cleanup()

    def _add_message(self, content="old text"):
        self.db.execute(
            "INSERT INTO messages("
            "chat_id,session_id,role,content,created_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                "user",
                content,
                rt.time.time(),
            ),
        )
        self.db.commit()

    def test_memory_module_owns_guard_and_persistence_helpers(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "memory.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "_HINDSIGHT_STALE_GUARD = _HindsightStaleGuard(",
            source,
        )
        self.assertIn(
            "def _memory_memory_memory_hindsight_conversation_snapshot(",
            source,
        )
        self.assertIn(
            "def _memory_hindsight_epoch(",
            source,
        )
        self.assertIn(
            "def _memory_memory_memory_hindsight_session_exists(",
            source,
        )

    def test_malformed_and_negative_epoch_values_read_as_zero(self):
        key = (
            "hindsight_epoch:"
            "chat:"
            f"{self.session['session_id']}"
        )

        rt.set_meta(self.db, key, "not-an-int")
        self.assertEqual(
            rt._HINDSIGHT_STALE_GUARD.read_epoch(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            0,
        )

        rt.set_meta(self.db, key, "-9")
        self.assertEqual(
            rt._HINDSIGHT_STALE_GUARD.read_epoch(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            0,
        )

    def test_direct_guard_rejects_transcript_changed_after_queue(self):
        self._add_message()
        queued = []
        fake = _FakeHindsight()
        rt.hindsight_client = lambda: fake

        with patch.object(
            rt,
            "submit_background",
            side_effect=lambda name, fn, *args, **kwargs: (
                queued.append(
                    (name, fn, args, kwargs)
                )
            ),
        ):
            rt._HINDSIGHT_STALE_GUARD.retain(
                self.db,
                "chat",
                self.session,
                self.fields,
            )

        self.assertEqual(len(queued), 1)
        self.db.execute(
            "UPDATE messages SET content='new text' "
            "WHERE chat_id=? AND session_id=?",
            ("chat", self.session["session_id"]),
        )
        self.db.commit()

        _name, fn, args, kwargs = queued[0]
        fn(*args, **kwargs)

        self.assertEqual(fake.retained, [])

    def test_successful_direct_guard_purge_advances_epoch_and_clears_mapping(self):
        self._add_message()
        mapped = rt.hindsight_conversation_document_id(
            self.session["session_id"]
        )
        self.db.execute(
            "INSERT INTO hindsight_documents("
            "chat_id,session_id,document_id,kind,created_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                mapped,
                "conversation",
                rt.time.time(),
            ),
        )
        self.db.commit()

        fake = _FakeHindsight()
        fake.documents.documents[mapped] = [
            f"session:{self.session['session_id']}"
        ]
        rt.hindsight_client = lambda: fake

        old_epoch = (
            rt._HINDSIGHT_STALE_GUARD.read_epoch(
                self.db,
                "chat",
                self.session["session_id"],
            )
        )

        deleted = rt._HINDSIGHT_STALE_GUARD.purge(
            self.db,
            "chat",
            self.session["session_id"],
        )

        self.assertGreaterEqual(deleted, 1)
        self.assertEqual(
            rt._HINDSIGHT_STALE_GUARD.read_epoch(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            old_epoch + 1,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM hindsight_documents "
                "WHERE chat_id=? AND session_id=?",
                ("chat", self.session["session_id"]),
            ).fetchone()[0],
            0,
        )
```

- [ ] **Step 2: Add the failed-purge persistence regression**

Append:

```python
    def test_failed_remote_purge_preserves_epoch_and_mapping(self):
        mapped = rt.hindsight_conversation_document_id(
            self.session["session_id"]
        )
        self.db.execute(
            "INSERT INTO hindsight_documents("
            "chat_id,session_id,document_id,kind,created_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                mapped,
                "conversation",
                rt.time.time(),
            ),
        )
        self.db.commit()

        key = rt._memory_memory_hindsight_epoch_key(
            "chat",
            self.session["session_id"],
        )
        rt.set_meta(self.db, key, "5")

        class BrokenDocuments(_FakeDocuments):
            async def list_documents(self, *args, **kwargs):
                raise RuntimeError("remote offline")

        fake = _FakeHindsight()
        fake.documents = BrokenDocuments()
        rt.hindsight_client = lambda: fake

        with self.assertRaisesRegex(
            RuntimeError,
            "Hindsight session memory cleanup failed",
        ):
            rt._HINDSIGHT_STALE_GUARD.purge(
                self.db,
                "chat",
                self.session["session_id"],
            )

        self.assertEqual(
            rt.get_meta(self.db, key, ""),
            "5",
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM hindsight_documents "
                "WHERE chat_id=? AND session_id=?",
                ("chat", self.session["session_id"]),
            ).fetchone()[0],
            1,
        )
```

- [ ] **Step 3: Run Task 2 tests and verify RED**

Run:

```bash
python -m unittest tests.test_memory_native_backend -v
```

Expected: failures because `memory.py` does not yet define `_HINDSIGHT_STALE_GUARD` or the canonical Hindsight stale-state helpers/backends.

- [ ] **Step 4: Import the guard and post-retain hook in memory.py**

Change the imports at the top of `bridge/memory.py` to include:

```python
from bridge.extension_registry import (
    apply_summary_context_hooks as _apply_summary_context_hooks,
    run_post_retain_hooks as _run_post_retain_hooks,
    run_summary_clear_hooks as _run_summary_clear_hooks,
)
from bridge.hindsight_integrity import (
    HindsightStaleGuard as _HindsightStaleGuard,
)
from bridge.memory_service import MemoryService as _MemoryService
```

Do not import `bridge.runtime` or `state_integrity.py`.

- [ ] **Step 5: Add canonical epoch, snapshot, and session-state helpers to memory.py**

Add before the raw retain backend:

```python
def _memory_memory_hindsight_epoch_key(
    chat_id: str,
    session_id: str,
) -> str:
    return f"hindsight_epoch:{chat_id}:{session_id}"


def _memory_hindsight_epoch(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> int:
    try:
        return max(
            0,
            int(
                get_meta(
                    db,
                    _memory_memory_hindsight_epoch_key(
                        chat_id,
                        session_id,
                    ),
                    "0",
                )
                or 0
            ),
        )
    except (TypeError, ValueError):
        return 0


def _memory_memory_hindsight_session_exists(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> bool:
    return bool(
        db.execute(
            "SELECT 1 FROM sessions "
            "WHERE chat_id=? AND session_id=?",
            (str(chat_id), str(session_id)),
        ).fetchone()
    )


def _memory_memory_hindsight_conversation_snapshot(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> tuple[str, str]:
    rows = db.execute(
        "SELECT role,content,created_at FROM messages "
        "WHERE chat_id=? AND session_id=? "
        "ORDER BY created_at DESC,rowid DESC LIMIT ?",
        (
            chat_id,
            session_id,
            HINDSIGHT_RETAIN_MAX_MESSAGES,
        ),
    ).fetchall()
    rows = list(reversed(rows))
    if not rows:
        return "", ""

    conversation = json.dumps(
        [
            {
                "role": role,
                "content": content,
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(created_at),
                ),
            }
            for role, content, created_at in rows
        ],
        ensure_ascii=False,
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                [str(role), str(content)]
                for role, content, _created_at in rows
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return conversation, fingerprint


def _write_hindsight_successful_purge_state(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> None:
    def write_purge_state():
        db.execute(
            "DELETE FROM hindsight_documents "
            "WHERE chat_id=? AND session_id=?",
            (str(chat_id), str(session_id)),
        )
        next_epoch = (
            _memory_hindsight_epoch(
                db,
                chat_id,
                session_id,
            )
            + 1
        )
        db.execute(
            "INSERT OR REPLACE INTO meta(key,value) "
            "VALUES(?,?)",
            (
                _memory_memory_hindsight_epoch_key(
                    chat_id,
                    session_id,
                ),
                str(next_epoch),
            ),
        )
        db.commit()

    run_write_txn(db, write_purge_state)
```

These bodies preserve the current `state_integrity.py` algorithms exactly; formatting may change, semantics may not.

- [ ] **Step 6: Compose the guard without changing public/raw ownership yet**

Immediately after the existing raw `_retain_session_memory` definition and before the existing public `retain_session_memory` definition, add:

```python
_HINDSIGHT_STALE_GUARD = _HindsightStaleGuard(
    open_db=lambda: db_connect(),
    session_lock=lambda chat_id, session_id: (
        hindsight_session_lock(
            chat_id,
            session_id,
        )
    ),
    memory_enabled=lambda db, chat_id: (
        memory_mode(db, chat_id) == "on"
    ),
    submit_background=(
        lambda name, fn, *args, **kwargs:
        submit_background(
            name,
            fn,
            *args,
            **kwargs,
        )
    ),
    session_exists=_memory_hindsight_session_exists,
    read_epoch=_memory_hindsight_epoch,
    snapshot=_memory_hindsight_conversation_snapshot,
    retain_backend=_retain_session_memory,
    purge_backend=purge_hindsight_session,
    write_successful_purge_state=(
        _write_hindsight_successful_purge_state
    ),
    run_post_retain_hooks=_run_post_retain_hooks,
)
```

The dynamic lambda around `submit_background` is intentional: existing tests and compatibility paths patch the final shared-runtime function, so the guard must resolve that collaborator at call time.

Do **not** rename `_retain_session_memory`, `retain_session_memory`, or `purge_hindsight_session` in Task 2. Do **not** route the public functions through the guard yet. This preserves the exact Phase 6B late-override execution contract while the new guard is exercised directly.

- [ ] **Step 8: Assert Task 2 does not change current public ownership**

Append to `tests/test_memory_native_backend.py`:

```python
    def test_guard_composition_does_not_cut_over_public_ownership_early(self):
        self.assertEqual(
            Path(
                rt.retain_session_memory.__code__.co_filename
            ).name,
            "state_integrity.py",
        )
        self.assertEqual(
            Path(
                rt.purge_hindsight_session.__code__.co_filename
            ).name,
            "state_integrity.py",
        )
```

This is an intentional transition-state test. Task 3 deletes it when the cutover becomes the required behavior.

- [ ] **Step 9: Add post-retain-hook integration coverage**

Add this import to `tests/test_memory_native_backend.py`:

```python
import bridge.extension_registry as registry
```

Then append:

```python
    def test_direct_guard_runs_post_retain_hook_when_memory_off(self):
        calls = []

        def hook(db, chat_id, session, fields):
            calls.append(
                (
                    db,
                    chat_id,
                    session["session_id"],
                    fields["name"],
                )
            )

        with patch.dict(
            registry._POST_RETAIN_HOOKS,
            {"test": hook},
            clear=True,
        ):
            rt.set_meta(
                self.db,
                "memory_mode:chat",
                "off",
            )
            rt._HINDSIGHT_STALE_GUARD.retain(
                self.db,
                "chat",
                self.session,
                self.fields,
            )

        self.assertEqual(
            calls,
            [
                (
                    self.db,
                    "chat",
                    "memory-native",
                    "Mira",
                )
            ],
        )
```

- [ ] **Step 10: Run Task 1 + Task 2 focused tests**

Run:

```bash
python -m unittest   tests.test_hindsight_integrity   tests.test_memory_native_backend   tests.test_memory_service   tests.test_memory_curator   tests.test_extension_registry -v
```

Expected: PASS. At this point the explicit guard works directly, and the transition-state ownership test confirms the public retain/purge functions are still intentionally owned by `state_integrity.py`.

- [ ] **Step 11: Commit Task 2**

```bash
git add   bridge/memory.py   tests/test_memory_native_backend.py
git commit -m "refactor: compose hindsight stale guard"
```

---

### Task 3: Retire Hindsight Overrides From state_integrity.py

**Files:**
- Modify: `bridge/state_integrity.py`
- Modify: `bridge/runtime_loader.py`
- Modify: `tests/test_state_integrity.py`
- Modify: `tests/test_runtime_loader.py`
- Modify: `tests/test_memory_native_backend.py`

**Interfaces:**
- Consumes:
  - canonical `memory.py::retain_session_memory`;
  - canonical `memory.py::purge_hindsight_session`;
  - `_HINDSIGHT_STALE_GUARD` and helpers from Task 2.
- Produces:
  - `_retain_session_memory_backend(chat_id: str, session: dict[str, str], character_name: str, conversation: str) -> None`;
  - `_purge_hindsight_session_backend(db, chat_id: str, session_id: str) -> int`;
  - stable public `memory.py::retain_session_memory` and `memory.py::purge_hindsight_session` delegates through `_HINDSIGHT_STALE_GUARD`;
  - `state_integrity.py` with only Live Sync `apply_sync_snapshot` override behavior;
  - state-integrity allowlist containing only `apply_sync_snapshot`;
  - public Hindsight owners resolving to `memory.py`.

- [ ] **Step 1: Add RED runtime ownership tests**

In `tests/test_runtime_loader.py`, add:

```python
    def test_hindsight_writes_are_not_state_integrity_overrides(self):
        state_stage = next(
            stage
            for stage in DEFAULT_RUNTIME_STAGES
            if stage.name == "safety_overrides"
        )
        self.assertEqual(
            state_stage.allowed_overrides_for(
                "state_integrity.py"
            ),
            frozenset({"apply_sync_snapshot"}),
        )

    def test_hindsight_public_owners_are_memory_module(self):
        self.assertEqual(
            Path(
                rt.retain_session_memory.__code__.co_filename
            ).name,
            "memory.py",
        )
        self.assertEqual(
            Path(
                rt.purge_hindsight_session.__code__.co_filename
            ).name,
            "memory.py",
        )

    def test_state_integrity_runtime_report_has_no_hindsight_overrides(self):
        entry = next(
            item
            for item in rt.RUNTIME_LOAD_REPORT
            if item["module"] == "state_integrity.py"
        )
        self.assertNotIn(
            "retain_session_memory",
            entry["public_callable_overrides"],
        )
        self.assertNotIn(
            "purge_hindsight_session",
            entry["public_callable_overrides"],
        )
        self.assertEqual(
            entry["public_callable_overrides"],
            ("apply_sync_snapshot",),
        )
```

- [ ] **Step 2: Add RED source-boundary tests**

Append to `tests/test_memory_native_backend.py`:

```python
class HindsightSourceBoundaryTests(unittest.TestCase):
    def test_state_integrity_no_longer_owns_hindsight_safety(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "state_integrity.py"
        ).read_text(encoding="utf-8")

        for forbidden in (
            "_ORIGINAL_RETAIN_SESSION_MEMORY_WORKER",
            "_ORIGINAL_PURGE_HINDSIGHT_SESSION",
            "def _hindsight_epoch_key(",
            "def _hindsight_memory_epoch(",
            "def _hindsight_conversation_snapshot(",
            "def _retain_session_memory(",
            "def retain_session_memory(",
            "def purge_hindsight_session(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(
                    forbidden,
                    source,
                )

    def test_hindsight_integrity_has_no_runtime_import(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "hindsight_integrity.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn(
            "import bridge.runtime",
            source,
        )
        self.assertNotIn(
            "from bridge.runtime",
            source,
        )
```

- [ ] **Step 3: Add the final public compatibility regression**

Append to `MemoryNativeBackendTests`:

```python
    def test_public_retain_rejects_stale_transcript_after_cutover(self):
        self._add_message()
        queued = []
        fake = _FakeHindsight()
        rt.hindsight_client = lambda: fake

        with patch.object(
            rt,
            "submit_background",
            side_effect=lambda name, fn, *args, **kwargs: (
                queued.append(
                    (name, fn, args, kwargs)
                )
            ),
        ):
            rt.retain_session_memory(
                self.db,
                "chat",
                self.session,
                self.fields,
            )

        self.assertEqual(len(queued), 1)

        self.db.execute(
            "UPDATE messages SET content='new text' "
            "WHERE chat_id=? AND session_id=?",
            ("chat", self.session["session_id"]),
        )
        self.db.commit()

        _name, fn, args, kwargs = queued[0]
        fn(*args, **kwargs)

        self.assertEqual(fake.retained, [])

    def test_public_purge_invalidates_already_queued_retain(self):
        self._add_message()
        queued = []
        fake = _FakeHindsight()
        rt.hindsight_client = lambda: fake

        with patch.object(
            rt,
            "submit_background",
            side_effect=lambda name, fn, *args, **kwargs: (
                queued.append(
                    (name, fn, args, kwargs)
                )
            ),
        ):
            rt.retain_session_memory(
                self.db,
                "chat",
                self.session,
                self.fields,
            )

        self.assertEqual(len(queued), 1)

        rt.purge_hindsight_session(
            self.db,
            "chat",
            self.session["session_id"],
        )

        _name, fn, args, kwargs = queued[0]
        fn(*args, **kwargs)

        self.assertEqual(fake.retained, [])
```

- [ ] **Step 4: Run the ownership/source tests and verify RED**

Run:

```bash
python -m unittest   tests.test_runtime_loader.RuntimeLoaderTests.test_hindsight_writes_are_not_state_integrity_overrides   tests.test_runtime_loader.RuntimeLoaderTests.test_hindsight_public_owners_are_memory_module   tests.test_runtime_loader.RuntimeLoaderTests.test_state_integrity_runtime_report_has_no_hindsight_overrides   tests.test_memory_native_backend.HindsightSourceBoundaryTests -v
```

Expected: failures because `state_integrity.py` still captures/replaces Hindsight retain/purge and the allowlist still permits them.

- [ ] **Step 5: Atomically cut memory.py public ownership over to the guard**

In `bridge/memory.py`, rename the existing raw worker:

```python
# before
def _retain_session_memory(
    chat_id: str,
    session: dict[str, str],
    character_name: str,
    conversation: str,
) -> None:

# after
def _retain_session_memory_backend(
    chat_id: str,
    session: dict[str, str],
    character_name: str,
    conversation: str,
) -> None:
```

Rename the existing raw purge:

```python
# before
def purge_hindsight_session(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> int:

# after
def _purge_hindsight_session_backend(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> int:
```

Keep both raw bodies statement-equivalent to baseline.

Update the existing guard composition to bind:

```python
    retain_backend=_retain_session_memory_backend,
    purge_backend=_purge_hindsight_session_backend,
```

Replace the old public foreground retain body with:

```python
def retain_session_memory(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
) -> None:
    _HINDSIGHT_STALE_GUARD.retain(
        db,
        chat_id,
        session,
        fields,
    )
```

Define the new stable public purge delegate after the guard exists:

```python
def purge_hindsight_session(
    db: sqlite3.Connection,
    chat_id: str,
    session_id: str,
) -> int:
    return _HINDSIGHT_STALE_GUARD.purge(
        db,
        chat_id,
        session_id,
    )
```

Delete the Task 2 transition-state test `test_guard_composition_does_not_cut_over_public_ownership_early`; Task 3 runtime-owner tests now require the opposite final ownership.

- [ ] **Step 5: Remove only the Hindsight section from state_integrity.py**

Keep the module import:

```python
from bridge.extension_registry import (
    run_post_retain_hooks as _run_post_retain_hooks
)
```

only if Live Sync still uses it after the Hindsight removal. If no remaining code uses it, remove that import.

Remove:

```python
_ORIGINAL_RETAIN_SESSION_MEMORY_WORKER = _retain_session_memory
_ORIGINAL_PURGE_HINDSIGHT_SESSION = purge_hindsight_session
```

Remove the complete definitions beginning with:

```python
def _hindsight_epoch_key(
```

```python
def _hindsight_memory_epoch(
```

```python
def _hindsight_conversation_snapshot(
```

```python
def _retain_session_memory(
```

```python
def retain_session_memory(
```

```python
def purge_hindsight_session(
```

Do not modify the statements in the remaining `apply_sync_snapshot` definition or its `_ORIGINAL_APPLY_SYNC_SNAPSHOT` capture.

Update the module docstring so it describes Live Sync integrity only.

- [ ] **Step 6: Shrink the state-integrity runtime allowlist to one symbol**

In `bridge/runtime_loader.py`, replace:

```python
(
    "retain_session_memory",
    "purge_hindsight_session",
    "apply_sync_snapshot",
)
```

with:

```python
(
    "apply_sync_snapshot",
)
```

Keep `state_integrity.py` in `safety_overrides` for Phase 6D.

- [ ] **Step 7: Migrate Hindsight tests out of test_state_integrity.py**

Delete these tests from `tests/test_state_integrity.py`:

```text
test_stale_hindsight_retain_is_rejected_after_transcript_change
test_successful_hindsight_purge_invalidates_queued_retain_and_clears_mapping
```

Remove the local `_FakeDocuments` and `_FakeHindsight` test helpers if the remaining Live Sync test no longer uses them.

Remove now-unused imports from that file.

Keep:

```text
test_live_sync_explicitly_clears_persona_and_world_and_refreshes_memory
```

unchanged for Phase 6D.

- [ ] **Step 8: Add deleted-session public regression**

Append to `MemoryNativeBackendTests`:

```python
    def test_public_queued_retain_skips_deleted_session(self):
        self._add_message()
        queued = []
        fake = _FakeHindsight()
        rt.hindsight_client = lambda: fake

        with patch.object(
            rt,
            "submit_background",
            side_effect=lambda name, fn, *args, **kwargs: (
                queued.append(
                    (name, fn, args, kwargs)
                )
            ),
        ):
            rt.retain_session_memory(
                self.db,
                "chat",
                self.session,
                self.fields,
            )

        self.db.execute(
            "DELETE FROM sessions "
            "WHERE chat_id=? AND session_id=?",
            ("chat", self.session["session_id"]),
        )
        self.db.commit()

        _name, fn, args, kwargs = queued[0]
        fn(*args, **kwargs)

        self.assertEqual(fake.retained, [])
```

- [ ] **Step 9: Run the focused memory/runtime suite**

Run:

```bash
python -m unittest   tests.test_hindsight_integrity   tests.test_memory_native_backend   tests.test_memory_service   tests.test_memory_curator   tests.test_extension_registry   tests.test_state_integrity   tests.test_runtime_loader   tests.test_composition -v
```

Expected: PASS.

- [ ] **Step 10: Run the architecture/source scan**

Run:

```bash
python - <<'PY'
from pathlib import Path
import bridge.runtime as rt
from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

state = Path("bridge/state_integrity.py").read_text(
    encoding="utf-8"
)
guard = Path("bridge/hindsight_integrity.py").read_text(
    encoding="utf-8"
)

for forbidden in (
    "_ORIGINAL_RETAIN_SESSION_MEMORY_WORKER",
    "_ORIGINAL_PURGE_HINDSIGHT_SESSION",
    "def _hindsight_epoch_key(",
    "def _hindsight_memory_epoch(",
    "def _hindsight_conversation_snapshot(",
    "def _retain_session_memory(",
    "def retain_session_memory(",
    "def purge_hindsight_session(",
):
    assert forbidden not in state

assert "import bridge.runtime" not in guard
assert "from bridge.runtime" not in guard

assert (
    Path(
        rt.retain_session_memory.__code__.co_filename
    ).name
    == "memory.py"
)
assert (
    Path(
        rt.purge_hindsight_session.__code__.co_filename
    ).name
    == "memory.py"
)

stage = next(
    item
    for item in DEFAULT_RUNTIME_STAGES
    if item.name == "safety_overrides"
)
assert (
    stage.allowed_overrides_for(
        "state_integrity.py"
    )
    == frozenset({"apply_sync_snapshot"})
)

entry = next(
    item
    for item in rt.RUNTIME_LOAD_REPORT
    if item["module"] == "state_integrity.py"
)
assert (
    entry["public_callable_overrides"]
    == ("apply_sync_snapshot",)
)

print("Phase 6C Hindsight ownership verified")
PY
```

Expected:

```text
Phase 6C Hindsight ownership verified
```

- [ ] **Step 12: Commit Task 3**

```bash
git add   bridge/state_integrity.py   bridge/runtime_loader.py   tests/test_state_integrity.py   tests/test_runtime_loader.py   tests/test_memory_native_backend.py
git commit -m "refactor: retire hindsight state-integrity overrides"
```

---

### Task 4: Exact-Head Verification and PR Readiness

**Files:**
- Modify: `docs/superpowers/plans/2026-09-20-phase-6c-hindsight-stale-guard.md`
- No production file changes unless verification finds a defect. Any defect returns to the owning task's RED -> GREEN cycle.

**Interfaces:**
- Consumes: completed Phase 6C branch.
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

If local `pip-audit` is unavailable, do not install unrelated tooling just for this command; the repository's GitHub Actions dependency audit is authoritative.

- [ ] **Step 6: Verify Live Sync non-scope equivalence**

Compare the final `apply_sync_snapshot` function body in `bridge/state_integrity.py` with baseline commit:

```text
9d623ee2edaba8e38169dde0f508a86a21eb9afd
```

The function body must be byte-for-byte identical.

Also confirm:

```text
bridge/sync_safety.py
```

has no Phase 6C diff.

If either condition fails, revert the unrelated change before proceeding.

- [ ] **Step 7: Run final architecture verification**

Run:

```bash
python - <<'PY'
from pathlib import Path
import bridge.runtime as rt
from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

assert (
    Path(
        rt.retain_session_memory.__code__.co_filename
    ).name
    == "memory.py"
)
assert (
    Path(
        rt.purge_hindsight_session.__code__.co_filename
    ).name
    == "memory.py"
)

state = Path("bridge/state_integrity.py").read_text(
    encoding="utf-8"
)
for forbidden in (
    "_ORIGINAL_RETAIN_SESSION_MEMORY_WORKER",
    "_ORIGINAL_PURGE_HINDSIGHT_SESSION",
    "def retain_session_memory(",
    "def purge_hindsight_session(",
    "def _retain_session_memory(",
):
    assert forbidden not in state

stage = next(
    item
    for item in DEFAULT_RUNTIME_STAGES
    if item.name == "safety_overrides"
)
assert (
    stage.allowed_overrides_for(
        "state_integrity.py"
    )
    == frozenset({"apply_sync_snapshot"})
)

entry = next(
    item
    for item in rt.RUNTIME_LOAD_REPORT
    if item["module"] == "state_integrity.py"
)
assert (
    entry["public_callable_overrides"]
    == ("apply_sync_snapshot",)
)

print("Phase 6C final architecture verified")
PY
```

Expected:

```text
Phase 6C final architecture verified
```

- [ ] **Step 8: Record execution evidence in this plan**

Append an `## Execution Evidence` section containing:

- Task-level RED commit SHAs and exact expected failures;
- matching GREEN commit SHAs;
- final implementation head SHA;
- unittest result;
- pytest result;
- `pip check` result;
- dependency audit result;
- architecture scan result;
- Live Sync non-scope comparison result;
- current upstream `main` SHA;
- upstream drift/reconciliation decision.

Do not claim exact-head CI success until the workflow has completed on that exact head.

- [ ] **Step 9: Commit verification documentation**

Run:

```bash
git add   docs/superpowers/plans/2026-09-20-phase-6c-hindsight-stale-guard.md
git commit -m "docs: record Phase 6C verification"
```

- [ ] **Step 10: Open or update a Draft PR**

Target:

```text
base: cepeter/SillyTavern-Telegram-Bridge:main
head: punzer4-code:refactor/phase-6c-hindsight-stale-guard
```

Title:

```text
refactor: retire hindsight integrity runtime overrides
```

The PR body must state:

- `HindsightStaleGuard` now owns stale-snapshot and purge-invalidation policy;
- `memory.py` is the canonical retain/purge owner;
- stale transcript, post-purge, and deleted-session retains remain rejected;
- failed remote purge leaves epoch/mappings untouched;
- state-integrity Hindsight captures/replacements are removed;
- Live Sync is intentionally unchanged;
- RED -> GREEN evidence and exact verification results.

Keep the PR Draft until exact-head CI succeeds and review is clear.

- [ ] **Step 11: Check upstream drift**

Compare the feature branch merge base with current upstream `main`.

If upstream has not changed, continue.

If upstream changed only unrelated files, document that result.

If upstream changed any Phase 6C implementation or test file, inspect and reconcile it, then rerun affected focused tests and full verification.

- [ ] **Step 12: Inspect GitHub Actions on the exact final head**

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

- [ ] **Step 13: Review the whole branch and PR feedback**

Before Ready-for-review, verify:

- no Critical or Important code-review findings remain;
- no unresolved review threads;
- no unaddressed review comments;
- PR is mergeable;
- exact PR head equals the green CI SHA.

If no independent reviewer/subagent capability exists in the harness, perform the Superpowers fallback whole-branch self-review and explicitly record that limitation in the PR body.

- [ ] **Step 14: Mark the PR Ready for review**

Only after Steps 12-13 pass.

Do not merge the PR. Merge is a separate user decision.

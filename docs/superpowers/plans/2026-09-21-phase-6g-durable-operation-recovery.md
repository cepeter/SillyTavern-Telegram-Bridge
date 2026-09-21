# Phase 6G — Durable Operation Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the five durable-operation public runtime overrides from `bridge/recovery.py` while preserving the currently effective crash-recovery behavior exactly.

**Architecture:** Keep domain orchestration in its canonical owners: `database.py` owns operation persistence, `generation.py` owns regen/continue, `commands.py` owns edited-turn regeneration, and `message_commands.py` owns message recovery routing/reset. Add one ordinary-import `OperationRecovery` helper for shared recovery mechanics, configured under unique private names in each shared-runtime consumer so later `exec()` stages cannot overwrite an earlier consumer's adapter.

**Tech Stack:** Python 3.11, stdlib `sqlite3`/`json`/`dataclasses`, existing shared runtime loader, `unittest`, `pytest`, GitHub Actions CI.

**Spec:** `docs/superpowers/specs/2026-09-21-phase-6g-durable-operation-recovery-design.md`

## Global Constraints

- Baseline upstream `main` is `49c2796f66581ad4ecd1cb85c89b70c6c2b618bf`.
- Preserve JobService's `queued -> scheduled -> running -> done/failed` lifecycle unchanged.
- Preserve operation phases and their current differences: regen/continue/edit finish directly as `applied` after successful delivery; generic message recovery persists `external_delivered` before `applied`; reset uses `memory_purged -> local_committed -> applied`.
- No provider generation or duplicate local mutation after `local_committed`.
- Telegram cleanup remains best-effort; Telegram delivery failure after `local_committed` must propagate and remain retryable.
- No schema migration, new operation phase, ContextVar, `_ORIGINAL_*` capture, global service locator, or `OperationService`.
- `bridge/operation_recovery.py` is an ordinary import and must never be added to `DEFAULT_RUNTIME_STAGES`.
- Each shared-runtime consumer uses a unique adapter global: `_GENERATION_OPERATION_RECOVERY`, `_COMMAND_OPERATION_RECOVERY`, or another file-specific name. Do not reuse one generic `_OPERATION_RECOVERY` name across exec-loaded files.
- Sync UI functions `sync_status_text`, `send_sync_menu`, and `handle_sync_callback` remain in `recovery.py` unchanged for Phase 6H.
- Do not merge the implementation PR automatically.

## Review Focus

1. **Malformed or non-object operation payload** — recovery must treat it as `{}` and continue from committed local rows rather than fail or mark an operation applied prematurely. Task 2 pins this.
2. **Telegram cleanup succeeds/fails independently from delivery** — cleanup failures are logged and tolerated, but a final `send_reply` failure leaves `local_committed` and its payload intact for retry. Tasks 1, 2, 4, and 5 pin this.
3. **Missing committed rows during recovery** — regen/continue/edit must raise their existing explicit `RuntimeError` messages and must not finish the operation. Tasks 1, 4, and 5 pin this.
4. **Bot-addressed and mention-prefixed commands** — local-committed `/regen`, `/continue`, and `/edit` variants must route to command-specific recovery before generic response redelivery. Task 6 pins this.
5. **Reset resumed after remote purge** — `memory_purged` must skip a second Hindsight purge, while `local_committed` must skip local deletion and only mark the operation applied. Task 1 pins this.

## File Structure

- Create `bridge/operation_recovery.py` — ordinary, dependency-injected mechanics only: phase guard, payload persistence, Telegram ID decoding/cleanup, latest-row/variant queries, finish.
- Create `tests/test_operation_recovery.py` — characterization tests, helper unit tests, canonical ownership/source-boundary tests, routing and reset regressions.
- Modify `bridge/database.py` — make hardened `begin_operation` canonical.
- Modify `bridge/generation.py` — canonical regen/continue with `_GENERATION_OPERATION_RECOVERY`.
- Modify `bridge/commands.py` — canonical edited-turn regeneration with `_COMMAND_OPERATION_RECOVERY`.
- Modify `bridge/message_commands.py` — command-specific local-committed recovery before generic recovery; retain reset behavior.
- Modify `bridge/recovery.py` — progressively retire durable public overrides/private helpers until only the three Sync UI functions remain.
- Modify `bridge/runtime_loader.py` — progressively shrink `recovery.py` allowlist to exactly the three Sync UI names.
- Modify `tests/test_runtime_loader.py` — final runtime report/allowlist assertions.

---

### Task 1: Characterize the effective durable-recovery behavior

**Files:**
- Create: `tests/test_operation_recovery.py`
- Read only: `bridge/recovery.py`
- Read only: `bridge/message_commands.py`

**Interfaces:**
- Consumes: current public runtime callables from `bridge.runtime`: `regenerate_last`, `continue_last`, `regenerate_edited_turn`, `process_message`, `reset_session`, `operation_phase`, `set_meta`, `get_meta`.
- Produces: a behavior contract that later cutover tasks must keep green without relying on private `recovery.py` helper names.

- [ ] **Step 1: Add shared test fixture helpers**

Create `tests/test_operation_recovery.py` with real SQLite-backed runtime fixtures. Do not patch private recovery helpers.

```python
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

import bridge.runtime as rt


class DurableRecoveryCharacterizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "recovery.sqlite3"
        self.db = rt.db_connect(self.path)
        self.session = rt.ensure_session(
            self.db,
            "chat",
            "provider::model",
        )
        self.fields = {"name": "Mira"}

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _operation(self, operation_id, state, kind="command"):
        now = rt.time.time()
        self.db.execute(
            "INSERT OR REPLACE INTO operations("
            "operation_id,kind,state,created_at,updated_at"
            ") VALUES(?,?,?,?,?)",
            (str(operation_id), kind, state, now, now),
        )
        self.db.commit()

    def _turns(self, user="question", assistant="answer"):
        now = rt.time.time()
        user_cursor = self.db.execute(
            "INSERT INTO messages("
            "chat_id,session_id,role,content,created_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                "user",
                user,
                now,
            ),
        )
        assistant_cursor = self.db.execute(
            "INSERT INTO messages("
            "chat_id,session_id,role,content,created_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                "assistant",
                assistant,
                now + 0.001,
            ),
        )
        self.db.commit()
        return int(user_cursor.lastrowid), int(assistant_cursor.lastrowid)

    def _payload(self, operation_id, payload):
        rt.set_meta(
            self.db,
            f"operation_payload:{operation_id}",
            json.dumps(payload, separators=(",", ":")),
        )
```

- [ ] **Step 2: Characterize regen local-commit redelivery and retryability**

Add these tests. The first pins no provider re-entry, selected variant reuse, cleanup, finish, and payload deletion. The second pins failed delivery remaining retryable.

```python
    def test_regen_local_committed_redelivers_selected_variant_without_generation(self):
        operation_id = 601
        user_rowid, assistant_rowid = self._turns(
            "question",
            "committed regen",
        )
        for response in ("v1", "v2", "committed regen"):
            rt.save_response_variant(
                self.db,
                "chat",
                self.session["session_id"],
                "question",
                response,
                user_rowid=user_rowid,
            )
        self._operation(operation_id, "local_committed", "regen")
        self._payload(
            operation_id,
            {"old_message_ids": ["41", "42"], "user_rowid": user_rowid},
        )

        with patch.object(
            rt,
            "generate_text",
            side_effect=AssertionError("provider must not run"),
        ), patch.object(
            rt,
            "delete_outgoing_message_row",
        ) as delete_current, patch.object(
            rt,
            "telegram_request",
            return_value={},
        ) as telegram, patch.object(
            rt,
            "send_reply",
        ) as send_reply:
            rt.regenerate_last(
                self.db,
                "token",
                "key",
                self.session,
                self.fields,
                "chat",
                operation_id=operation_id,
            )

        delete_current.assert_called_once_with(
            self.db,
            "token",
            "chat",
            assistant_rowid,
        )
        deleted = [
            call.args[2]["message_id"]
            for call in telegram.call_args_list
            if call.args[1] == "deleteMessage"
        ]
        self.assertEqual(deleted, [41, 42])
        self.assertIn(
            "Regenerated response (variant 3)",
            send_reply.call_args.args[2],
        )
        self.assertEqual(rt.operation_phase(self.db, operation_id), "applied")
        self.assertEqual(
            rt.get_meta(self.db, f"operation_payload:{operation_id}", ""),
            "",
        )

    def test_regen_delivery_failure_keeps_local_committed_and_payload(self):
        operation_id = 602
        user_rowid, _assistant_rowid = self._turns(
            "question",
            "committed regen",
        )
        rt.save_response_variant(
            self.db,
            "chat",
            self.session["session_id"],
            "question",
            "committed regen",
            user_rowid=user_rowid,
        )
        self._operation(operation_id, "local_committed", "regen")
        self._payload(
            operation_id,
            {"old_message_ids": [], "user_rowid": user_rowid},
        )

        with patch.object(
            rt,
            "generate_text",
            side_effect=AssertionError("provider must not run"),
        ), patch.object(
            rt,
            "delete_outgoing_message_row",
        ), patch.object(
            rt,
            "send_reply",
            side_effect=RuntimeError("Telegram sendMessage failed"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Telegram sendMessage failed",
            ):
                rt.regenerate_last(
                    self.db,
                    "token",
                    "key",
                    self.session,
                    self.fields,
                    "chat",
                    operation_id=operation_id,
                )

        self.assertEqual(
            rt.operation_phase(self.db, operation_id),
            "local_committed",
        )
        self.assertNotEqual(
            rt.get_meta(self.db, f"operation_payload:{operation_id}", ""),
            "",
        )
```

- [ ] **Step 3: Characterize incomplete command recovery**

```python
    def test_regen_incomplete_recovery_raises_without_finishing(self):
        operation_id = 603
        self._operation(operation_id, "local_committed", "regen")

        with self.assertRaisesRegex(
            RuntimeError,
            "regen recovery state is incomplete",
        ):
            rt.regenerate_last(
                self.db,
                "token",
                "key",
                self.session,
                self.fields,
                "chat",
                operation_id=operation_id,
            )

        self.assertEqual(
            rt.operation_phase(self.db, operation_id),
            "local_committed",
        )
```

Add equivalent focused tests for continue and edited-turn recovery using persisted assistant rows, with `generate_text` patched to raise if called. Assert the visible prefixes remain `↪️ Continued response` and `✏️ Edited message regenerated.` and that successful delivery ends at `applied`.

- [ ] **Step 4: Characterize command-specific routing before generic recovery**

Use mocks only at the public runtime boundary.

```python
    def test_process_message_routes_local_committed_regen_before_generic_delivery(self):
        operation_id = 604
        self._operation(operation_id, "local_committed", "regen")

        with patch.object(
            rt,
            "load_session",
            return_value=self.session,
        ), patch.object(
            rt,
            "card_fields_from_file",
            return_value=self.fields,
        ), patch.object(
            rt,
            "regenerate_last",
        ) as regen, patch.object(
            rt,
            "send_reply",
            side_effect=AssertionError("generic recovery ran first"),
        ):
            rt.process_message(
                self.db,
                "token",
                "key",
                "provider::model",
                self.fields,
                "chat",
                "/regen",
                queued_session_id=self.session["session_id"],
                operation_id=operation_id,
                services=SimpleNamespace(memory=None),
            )

        regen.assert_called_once()
```

Also add subtests for `/continue` and `/edit replacement`. These should assert `continue_last`/`edit_last_user` is called and generic `send_reply` is not.

- [ ] **Step 5: Characterize reset resume points**

```python
    def test_reset_memory_purged_resume_skips_second_remote_purge(self):
        operation_id = 605
        self._turns()
        self._operation(operation_id, "memory_purged", "reset")
        memory = Mock()
        memory.purge_session.side_effect = AssertionError(
            "remote purge repeated"
        )

        rt.reset_session(
            self.db,
            "token",
            "chat",
            self.session,
            operation_id=operation_id,
            memory_service=memory,
        )

        count = self.db.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE chat_id=? AND session_id=?",
            ("chat", self.session["session_id"]),
        ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(rt.operation_phase(self.db, operation_id), "applied")

    def test_reset_local_committed_resume_only_marks_applied(self):
        operation_id = 606
        self._turns()
        self._operation(operation_id, "local_committed", "reset")
        memory = Mock()

        rt.reset_session(
            self.db,
            "token",
            "chat",
            self.session,
            operation_id=operation_id,
            memory_service=memory,
        )

        count = self.db.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE chat_id=? AND session_id=?",
            ("chat", self.session["session_id"]),
        ).fetchone()[0]
        self.assertEqual(count, 2)
        memory.purge_session.assert_not_called()
        self.assertEqual(rt.operation_phase(self.db, operation_id), "applied")
```

- [ ] **Step 6: Run characterization tests**

Run:

```bash
python -m pytest tests/test_operation_recovery.py -q
```

Expected: PASS on the pre-cutover runtime. If a characterization test fails because the current effective behavior differs from the approved spec, stop implementation and reconcile the spec instead of changing production code to satisfy the test.

- [ ] **Step 7: Commit the characterization contract**

```bash
git add tests/test_operation_recovery.py
git commit -m "test: pin durable operation recovery semantics"
```

---

### Task 2: Add the ordinary `OperationRecovery` mechanics

**Files:**
- Create: `bridge/operation_recovery.py`
- Modify: `tests/test_operation_recovery.py`

**Interfaces:**
- Consumes: injected callables `operation_phase`, `begin_operation`, `record_operation`, `run_write_txn`, `get_meta`, `telegram_request`, `delete_outgoing_message_row`, `log_info`.
- Produces: `OperationRecovery.begin_or_recover`, `set_payload`, `get_payload`, `finish`, `outgoing_ids_after`, `delete_stored_telegram_ids`, `prepare_delivery`, `selected_variant_index`, `latest_user_row`, `latest_assistant_row`.

- [ ] **Step 1: Add direct helper tests that fail because the module does not exist**

Append:

```python
from bridge.operation_recovery import OperationRecovery


class OperationRecoveryUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "adapter.sqlite3"
        self.db = rt.db_connect(self.path)
        self.telegram = Mock(return_value={})
        self.log_info = Mock()
        self.adapter = OperationRecovery(
            operation_phase=rt.operation_phase,
            begin_operation=rt.begin_operation,
            record_operation=rt.record_operation,
            run_write_txn=rt.run_write_txn,
            get_meta=rt.get_meta,
            telegram_request=self.telegram,
            delete_outgoing_message_row=Mock(),
            log_info=self.log_info,
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_payload_missing_malformed_and_non_object_are_empty(self):
        self.assertEqual(self.adapter.get_payload(self.db, 701), {})
        rt.set_meta(self.db, "operation_payload:701", "{bad")
        self.assertEqual(self.adapter.get_payload(self.db, 701), {})
        rt.set_meta(self.db, "operation_payload:701", "[1,2]")
        self.assertEqual(self.adapter.get_payload(self.db, 701), {})

    def test_message_id_decoder_preserves_order_and_deduplicates(self):
        rows = [
            ("10", '["11","10",null,""]'),
            (None, '["12","11"]'),
            ("", "not-json"),
        ]
        self.assertEqual(
            self.adapter.message_ids_from_rows(rows),
            ["10", "11", "12"],
        )
```

- [ ] **Step 2: Run the helper tests and verify RED**

Run:

```bash
python -m pytest tests/test_operation_recovery.py::OperationRecoveryUnitTests -q
```

Expected: collection/import FAIL with `ModuleNotFoundError: No module named 'bridge.operation_recovery'`.

- [ ] **Step 3: Create the minimal ordinary helper**

Create `bridge/operation_recovery.py`:

```python
"""Ordinary crash-recovery mechanics for durable bridge operations."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import sqlite3


@dataclass(frozen=True)
class OperationRecovery:
    operation_phase: Callable[..., str]
    begin_operation: Callable[..., bool]
    record_operation: Callable[..., None]
    run_write_txn: Callable[..., object]
    get_meta: Callable[..., str]
    telegram_request: Callable[..., object]
    delete_outgoing_message_row: Callable[..., None]
    log_info: Callable[..., None]

    @staticmethod
    def _payload_key(operation_id) -> str:
        return f"operation_payload:{operation_id}"

    def set_payload(self, db, operation_id, payload) -> None:
        if operation_id is None:
            return

        def write():
            db.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                (
                    self._payload_key(operation_id),
                    json.dumps(payload, separators=(",", ":")),
                ),
            )
            db.commit()

        self.run_write_txn(db, write)

    def get_payload(self, db, operation_id) -> dict:
        if operation_id is None:
            return {}
        raw = self.get_meta(
            db,
            self._payload_key(operation_id),
            "",
        )
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def finish(self, db, operation_id, kind) -> None:
        if operation_id is None:
            return

        def write():
            self.record_operation(db, operation_id, kind)
            db.execute(
                "DELETE FROM meta WHERE key=?",
                (self._payload_key(operation_id),),
            )
            db.commit()

        self.run_write_txn(db, write)

    def begin_or_recover(
        self,
        db,
        operation_id,
        kind,
        deliver_recovered,
    ) -> bool:
        if operation_id is None:
            return True
        phase = self.operation_phase(db, operation_id)
        if phase == "applied":
            return False
        if phase == "local_committed":
            deliver_recovered()
            return False
        return bool(
            self.begin_operation(db, operation_id, kind)
        )

    @staticmethod
    def message_ids_from_rows(rows) -> list[str]:
        result = []
        for legacy_id, encoded_ids in rows:
            if legacy_id not in {None, ""}:
                result.append(str(legacy_id))
            try:
                decoded = json.loads(encoded_ids or "[]")
            except (TypeError, json.JSONDecodeError):
                decoded = []
            if isinstance(decoded, list):
                result.extend(
                    str(item)
                    for item in decoded
                    if item not in {None, ""}
                )
        return list(dict.fromkeys(result))

    def outgoing_ids_after(
        self,
        db,
        chat_id,
        session_id,
        rowid,
    ) -> list[str]:
        rows = db.execute(
            "SELECT telegram_message_id,telegram_message_ids "
            "FROM messages WHERE chat_id=? AND session_id=? "
            "AND role='assistant' AND rowid>? ORDER BY rowid",
            (chat_id, session_id, int(rowid)),
        ).fetchall()
        return self.message_ids_from_rows(rows)

    def delete_stored_telegram_ids(
        self,
        token,
        chat_id,
        message_ids,
    ) -> None:
        for message_id in message_ids or []:
            try:
                self.telegram_request(
                    token,
                    "deleteMessage",
                    {
                        "chat_id": chat_id,
                        "message_id": int(message_id),
                    },
                )
            except Exception:
                self.log_info(
                    "Recovery cleanup could not delete Telegram message %s",
                    message_id,
                    exc_info=True,
                )

    def prepare_delivery(
        self,
        db,
        token,
        chat_id,
        assistant_rowid,
        operation_id,
    ) -> None:
        try:
            self.delete_outgoing_message_row(
                db,
                token,
                chat_id,
                int(assistant_rowid),
            )
        except Exception:
            self.log_info(
                "Recovery could not clear the current assistant delivery",
                exc_info=True,
            )
        payload = self.get_payload(db, operation_id)
        self.delete_stored_telegram_ids(
            token,
            chat_id,
            payload.get("old_message_ids") or [],
        )

    @staticmethod
    def selected_variant_index(
        db,
        chat_id,
        session_id,
        user_rowid,
    ) -> int:
        row = db.execute(
            "SELECT variant_index FROM response_variants "
            "WHERE chat_id=? AND session_id=? AND user_rowid=? "
            "AND selected=1 ORDER BY id DESC LIMIT 1",
            (chat_id, session_id, int(user_rowid)),
        ).fetchone()
        return int(row[0]) if row else 1

    @staticmethod
    def latest_user_row(db, chat_id, session_id):
        return db.execute(
            "SELECT rowid,content FROM messages "
            "WHERE chat_id=? AND session_id=? AND role='user' "
            "ORDER BY rowid DESC LIMIT 1",
            (chat_id, session_id),
        ).fetchone()

    @staticmethod
    def latest_assistant_row(db, chat_id, session_id):
        return db.execute(
            "SELECT rowid,content FROM messages "
            "WHERE chat_id=? AND session_id=? AND role='assistant' "
            "ORDER BY rowid DESC LIMIT 1",
            (chat_id, session_id),
        ).fetchone()
```

`delete_stored_telegram_ids` is intentionally part of the mechanical adapter surface because normal regen/edit need best-effort deletion without also deleting the newly inserted assistant row.

- [ ] **Step 4: Add phase/finish/cleanup unit tests**

Append tests that directly prove the review-focus failures:

```python
    def test_local_committed_delivery_failure_does_not_finish(self):
        now = rt.time.time()
        self.db.execute(
            "INSERT INTO operations("
            "operation_id,kind,state,created_at,updated_at"
            ") VALUES(?,?,?,?,?)",
            ("702", "regen", "local_committed", now, now),
        )
        self.db.commit()

        def fail_delivery():
            raise RuntimeError("delivery failed")

        with self.assertRaisesRegex(RuntimeError, "delivery failed"):
            self.adapter.begin_or_recover(
                self.db,
                702,
                "regen",
                fail_delivery,
            )
        self.assertEqual(
            rt.operation_phase(self.db, 702),
            "local_committed",
        )

    def test_finish_marks_applied_and_removes_payload(self):
        now = rt.time.time()
        self.db.execute(
            "INSERT INTO operations("
            "operation_id,kind,state,created_at,updated_at"
            ") VALUES(?,?,?,?,?)",
            ("703", "regen", "local_committed", now, now),
        )
        self.db.commit()
        self.adapter.set_payload(
            self.db,
            703,
            {"old_message_ids": ["9"]},
        )

        self.adapter.finish(self.db, 703, "regen")

        self.assertEqual(rt.operation_phase(self.db, 703), "applied")
        self.assertEqual(
            rt.get_meta(self.db, "operation_payload:703", ""),
            "",
        )

    def test_cleanup_failure_is_logged_and_next_id_is_attempted(self):
        self.telegram.side_effect = [
            RuntimeError("delete failed"),
            {},
        ]

        self.adapter.delete_stored_telegram_ids(
            "token",
            "chat",
            ["9", "10"],
        )

        self.assertEqual(self.telegram.call_count, 2)
        self.log_info.assert_called_once_with(
            "Recovery cleanup could not delete Telegram message %s",
            "9",
            exc_info=True,
        )
```

- [ ] **Step 5: Run helper tests and characterization tests**

Run:

```bash
python -m pytest tests/test_operation_recovery.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/operation_recovery.py tests/test_operation_recovery.py
git commit -m "refactor: add durable operation recovery adapter"
```

---

### Task 3: Make `database.py::begin_operation` canonical and retire its override

**Files:**
- Modify: `bridge/database.py:310-345`
- Modify: `bridge/recovery.py:1-35`
- Modify: `bridge/runtime_loader.py:30-50`
- Modify: `tests/test_operation_recovery.py`

**Interfaces:**
- Consumes: `run_write_txn(db, operation)` and `operation_was_applied(db, operation_id)`.
- Produces: canonical `begin_operation(db, operation_id, kind) -> bool` used by all later configured `OperationRecovery` adapters.

- [ ] **Step 1: Add source-ownership and serialized-write tests**

```python
class DurableRecoveryOwnershipTests(unittest.TestCase):
    def test_database_begin_operation_uses_serialized_short_write(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "database.py"
        ).read_text(encoding="utf-8")
        start = source.index("def begin_operation")
        end = source.index(
            "\ndef operation_was_applied",
            start,
        )
        chunk = source[start:end]
        self.assertIn("run_write_txn(db, write)", chunk)

    def test_recovery_no_longer_defines_begin_operation(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "recovery.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("\ndef begin_operation(", source)
```

- [ ] **Step 2: Run the ownership tests and verify RED**

Run:

```bash
python -m pytest \
  tests/test_operation_recovery.py::DurableRecoveryOwnershipTests::test_database_begin_operation_uses_serialized_short_write \
  tests/test_operation_recovery.py::DurableRecoveryOwnershipTests::test_recovery_no_longer_defines_begin_operation \
  -q
```

Expected: FAIL because canonical `database.py` still performs the write directly and `recovery.py` still defines the public override.

- [ ] **Step 3: Replace canonical `begin_operation` with the hardened implementation**

In `bridge/database.py`:

```python
def begin_operation(
    db: sqlite3.Connection,
    operation_id: int | str | None,
    kind: str,
) -> bool:
    if operation_id is None:
        return True

    def write():
        now = time.time()
        cursor = db.execute(
            "INSERT OR IGNORE INTO operations("
            "operation_id,kind,state,created_at,updated_at"
            ") VALUES(?,?, 'in_progress',?,?)",
            (str(operation_id), kind, now, now),
        )
        if cursor.rowcount == 1:
            db.commit()
            return True
        return False

    inserted = run_write_txn(db, write)
    if inserted:
        return True
    return not operation_was_applied(db, operation_id)
```

- [ ] **Step 4: Delete the public `begin_operation` definition from `recovery.py` and its allowlist entry**

Do not remove private recovery helpers yet; later tasks still rely on them until their canonical owners cut over.

In `runtime_loader.py` the `recovery.py` allowlist should now omit only `begin_operation` while keeping the other seven current names.

- [ ] **Step 5: Run targeted and full recovery tests**

Run:

```bash
python -m pytest tests/test_operation_recovery.py -q
python -m pytest tests/test_runtime_loader.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add \
  bridge/database.py \
  bridge/recovery.py \
  bridge/runtime_loader.py \
  tests/test_operation_recovery.py
git commit -m "refactor: canonicalize durable operation begin"
```

---

### Task 4: Move regen and continue recovery into `generation.py`

**Files:**
- Modify: `bridge/generation.py:1-20,520-700`
- Modify: `bridge/recovery.py:100-320`
- Modify: `bridge/runtime_loader.py:30-50`
- Modify: `tests/test_operation_recovery.py`
- Verify: `tests/test_memory_service.py`

**Interfaces:**
- Consumes: `OperationRecovery` and canonical `begin_operation` from Task 3.
- Produces: canonical `regenerate_last(..., memory_service=None, persona_service=None)` and `continue_last(..., memory_service=None, persona_service=None)`.

- [ ] **Step 1: Add source-boundary tests requiring canonical generation ownership**

```python
    def test_generation_owns_regen_and_continue_recovery_adapter(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "generation.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "from bridge.operation_recovery import",
            source,
        )
        self.assertIn(
            "_GENERATION_OPERATION_RECOVERY",
            source,
        )
        self.assertIn(
            "def regenerate_last(",
            source,
        )
        self.assertIn(
            "def continue_last(",
            source,
        )

    def test_recovery_no_longer_defines_regen_or_continue(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "recovery.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("\ndef regenerate_last(", source)
        self.assertNotIn("\ndef continue_last(", source)
```

- [ ] **Step 2: Run the new ownership tests and verify RED**

Run:

```bash
python -m pytest \
  tests/test_operation_recovery.py::DurableRecoveryOwnershipTests::test_generation_owns_regen_and_continue_recovery_adapter \
  tests/test_operation_recovery.py::DurableRecoveryOwnershipTests::test_recovery_no_longer_defines_regen_or_continue \
  -q
```

Expected: FAIL because the effective implementations still live in `recovery.py`.

- [ ] **Step 3: Configure a uniquely named generation adapter**

Near the top of `generation.py`:

```python
from bridge.operation_recovery import (
    OperationRecovery as _OperationRecovery,
)


_GENERATION_OPERATION_RECOVERY = _OperationRecovery(
    operation_phase=operation_phase,
    begin_operation=begin_operation,
    record_operation=record_operation,
    run_write_txn=run_write_txn,
    get_meta=get_meta,
    telegram_request=telegram_request,
    delete_outgoing_message_row=delete_outgoing_message_row,
    log_info=logging.info,
)
```

Do not name this `_OPERATION_RECOVERY`. The runtime executes source files into one namespace; a generic private name would be overwritten by later files and earlier functions would resolve the wrong adapter at call time.

- [ ] **Step 4: Add the private generation/render helper**

```python
def _generate_rendered_reply(
    db,
    token,
    api_key,
    session,
    chat_id,
    messages,
    query,
    rag_bundle,
):
    session_id = session["session_id"]
    send_typing(token, chat_id)
    settings = get_generation_settings(
        db,
        chat_id,
        session_id,
    )
    reply = generate_text(
        api_key,
        session["model_id"],
        messages,
        session_id=f"telegram:{chat_id}:{session_id}",
        settings=settings,
    )
    reply += rag_citation_footer(
        db,
        chat_id,
        query,
        rag_bundle,
    )
    return render_session_response(
        api_key,
        session,
        reply,
        chat_id,
        settings,
    )
```

- [ ] **Step 5: Replace canonical `regenerate_last` with the effective recovery-aware flow**

Use the same public signature as the effective runtime, including `persona_service`:

```python
def regenerate_last(
    db,
    token,
    api_key,
    session,
    fields,
    chat_id,
    operation_id=None,
    *,
    memory_service=None,
    persona_service=None,
):
    memory_service = resolve_memory_service(memory_service)
    session_id = session["session_id"]

    def deliver_recovered_regen():
        user_row = _GENERATION_OPERATION_RECOVERY.latest_user_row(
            db,
            chat_id,
            session_id,
        )
        assistant_row = (
            _GENERATION_OPERATION_RECOVERY.latest_assistant_row(
                db,
                chat_id,
                session_id,
            )
        )
        if not user_row or not assistant_row:
            raise RuntimeError(
                "regen recovery state is incomplete"
            )
        _GENERATION_OPERATION_RECOVERY.prepare_delivery(
            db,
            token,
            chat_id,
            assistant_row[0],
            operation_id,
        )
        variant = (
            _GENERATION_OPERATION_RECOVERY.selected_variant_index(
                db,
                chat_id,
                session_id,
                user_row[0],
            )
        )
        send_reply(
            token,
            chat_id,
            f"♻️ Regenerated response (variant {variant})\n\n"
            f"{assistant_row[1]}",
            db,
            session_id,
            int(assistant_row[0]),
        )
        _GENERATION_OPERATION_RECOVERY.finish(
            db,
            operation_id,
            "regen",
        )

    if not _GENERATION_OPERATION_RECOVERY.begin_or_recover(
        db,
        operation_id,
        "regen",
        deliver_recovered_regen,
    ):
        return
```

Continue with the current effective body from `recovery.py`, not the older canonical body. Specifically:

- pass `persona_service=persona_service` to `build_chat_messages`;
- collect `old_message_ids` before local mutation;
- call `set_payload` before the local write;
- persist message deletion/insertion, response variant, and `local_committed` in one `run_write_txn` callback with `save_response_variant(..., commit=False)`;
- call `delete_stored_telegram_ids` after local commit;
- retain memory;
- deliver;
- call `finish` last.

- [ ] **Step 6: Replace canonical `continue_last` with the effective recovery-aware flow**

Use the same adapter and public signature with `persona_service=None`.

The local write must update the selected response variant by `user_rowid`, not by matching `user_content`:

```python
db.execute(
    "UPDATE response_variants SET response=? "
    "WHERE chat_id=? AND session_id=? "
    "AND user_rowid=? AND selected=1",
    (
        combined,
        chat_id,
        session_id,
        int(user_row[0]),
    ),
)
```

Before local mutation, persist:

```python
old_message_ids = (
    _GENERATION_OPERATION_RECOVERY.message_ids_from_rows(
        db.execute(
            "SELECT telegram_message_id,telegram_message_ids "
            "FROM messages WHERE rowid=?",
            (int(assistant_row[0]),),
        ).fetchall()
    )
)
_GENERATION_OPERATION_RECOVERY.set_payload(
    db,
    operation_id,
    {
        "old_message_ids": old_message_ids,
        "assistant_rowid": int(assistant_row[0]),
    },
)
```

After local commit use `prepare_delivery`, then retain, send, and `finish`.

- [ ] **Step 7: Delete `regenerate_last` and `continue_last` from `recovery.py` and shrink the allowlist**

Remove exactly those public definitions. Keep any private helpers still needed by edited-turn/process-message compatibility until their tasks.

Remove `regenerate_last` and `continue_last` from the `recovery.py` allowed override tuple.

- [ ] **Step 8: Run targeted behavior, memory-service, and loader tests**

Run:

```bash
python -m pytest tests/test_operation_recovery.py -q
python -m pytest tests/test_memory_service.py -q
python -m pytest tests/test_runtime_loader.py -q
```

Expected: PASS. The characterization tests now exercise the canonical `generation.py` functions because the late overrides are gone.

- [ ] **Step 9: Commit**

```bash
git add \
  bridge/generation.py \
  bridge/recovery.py \
  bridge/runtime_loader.py \
  tests/test_operation_recovery.py
git commit -m "refactor: canonicalize generation recovery"
```

---

### Task 5: Move edited-turn recovery into `commands.py`

**Files:**
- Modify: `bridge/commands.py:1-115`
- Modify: `bridge/recovery.py:300-390`
- Modify: `bridge/runtime_loader.py:30-50`
- Modify: `tests/test_operation_recovery.py`
- Verify: `tests/test_memory_service.py`

**Interfaces:**
- Consumes: `OperationRecovery` and canonical operation persistence.
- Produces: canonical `regenerate_edited_turn(..., memory_service=None, persona_service=None)` used by `edit_last_user` and `edit_telegram_user_message`.

- [ ] **Step 1: Add source-boundary tests requiring commands ownership**

```python
    def test_commands_owns_edited_turn_recovery_adapter(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "commands.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "from bridge.operation_recovery import",
            source,
        )
        self.assertIn(
            "_COMMAND_OPERATION_RECOVERY",
            source,
        )
        self.assertIn(
            "def regenerate_edited_turn(",
            source,
        )

    def test_recovery_no_longer_defines_edited_turn(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "recovery.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            "\ndef regenerate_edited_turn(",
            source,
        )
```

- [ ] **Step 2: Run and verify RED**

```bash
python -m pytest \
  tests/test_operation_recovery.py::DurableRecoveryOwnershipTests::test_commands_owns_edited_turn_recovery_adapter \
  tests/test_operation_recovery.py::DurableRecoveryOwnershipTests::test_recovery_no_longer_defines_edited_turn \
  -q
```

Expected: FAIL.

- [ ] **Step 3: Configure the uniquely named commands adapter**

At the top of `commands.py`:

```python
from bridge.operation_recovery import (
    OperationRecovery as _OperationRecovery,
)


_COMMAND_OPERATION_RECOVERY = _OperationRecovery(
    operation_phase=operation_phase,
    begin_operation=begin_operation,
    record_operation=record_operation,
    run_write_txn=run_write_txn,
    get_meta=get_meta,
    telegram_request=telegram_request,
    delete_outgoing_message_row=delete_outgoing_message_row,
    log_info=logging.info,
)
```

- [ ] **Step 4: Replace `regenerate_edited_turn` with the effective implementation**

Use the recovery hook:

```python
def deliver_recovered_edit():
    user_row = _COMMAND_OPERATION_RECOVERY.latest_user_row(
        db,
        chat_id,
        session_id,
    )
    assistant_row = (
        _COMMAND_OPERATION_RECOVERY.latest_assistant_row(
            db,
            chat_id,
            session_id,
        )
    )
    if not user_row or not assistant_row:
        raise RuntimeError(
            "edit recovery state is incomplete"
        )
    _COMMAND_OPERATION_RECOVERY.prepare_delivery(
        db,
        token,
        chat_id,
        assistant_row[0],
        operation_id,
    )
    send_reply(
        token,
        chat_id,
        "✏️ Edited message regenerated.\n\n"
        + assistant_row[1],
        db,
        session_id,
        int(assistant_row[0]),
    )
    _COMMAND_OPERATION_RECOVERY.finish(
        db,
        operation_id,
        "edit",
    )
```

Then use `begin_or_recover` before any transcript/prompt work.

The normal prompt path must pass both injected contexts:

```python
messages = build_chat_messages(
    session,
    fields,
    new_text,
    history_rows,
    memory_context=memory_prompt.recall,
    session_summary=memory_prompt.summary,
    persona_service=persona_service,
    rag_context=rag_context_for_prompt(
        db,
        chat_id,
        new_text,
        rag_bundle,
    ),
)
```

Persist the payload before mutation:

```python
old_message_ids = (
    _COMMAND_OPERATION_RECOVERY.outgoing_ids_after(
        db,
        chat_id,
        session_id,
        int(user_rowid),
    )
)
_COMMAND_OPERATION_RECOVERY.set_payload(
    db,
    operation_id,
    {
        "old_message_ids": old_message_ids,
        "user_rowid": int(user_rowid),
    },
)
```

The local transaction must contain summary deletion, user update, later-message deletion, assistant insertion, `save_response_variant(..., commit=False)`, `set_operation_phase(..., "local_committed")`, and the commit.

After local commit: delete old stored IDs best-effort, retain memory, send, finish.

- [ ] **Step 5: Ensure callers retain the effective public signature**

`edit_last_user` and `edit_telegram_user_message` already accept `persona_service`; keep forwarding it to canonical `regenerate_edited_turn`.

- [ ] **Step 6: Delete the edited-turn public override and allowlist entry**

Remove `regenerate_edited_turn` from `recovery.py` and from `runtime_loader.py`'s `recovery.py` allowlist.

- [ ] **Step 7: Run tests**

```bash
python -m pytest tests/test_operation_recovery.py -q
python -m pytest tests/test_memory_service.py -q
python -m pytest tests/test_runtime_loader.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add \
  bridge/commands.py \
  bridge/recovery.py \
  bridge/runtime_loader.py \
  tests/test_operation_recovery.py
git commit -m "refactor: canonicalize edited turn recovery"
```

---

### Task 6: Move command-specific recovery ordering into canonical `process_message`

**Files:**
- Modify: `bridge/message_commands.py:80-200`
- Modify: `bridge/recovery.py:390-490`
- Modify: `bridge/runtime_loader.py:30-50`
- Modify: `tests/test_operation_recovery.py`

**Interfaces:**
- Consumes: canonical `regenerate_last`, `continue_last`, `edit_last_user`, `operation_phase`.
- Produces: canonical `process_message` with command-specific local-commit recovery before generic redelivery; no ContextVar and no original-function capture.

- [ ] **Step 1: Add command-normalization regressions and source guards**

Add explicit cases for the current wrapper behavior:

```python
    def test_operation_command_normalizes_bot_addressed_command(self):
        cases = {
            "/regen@BridgeBot": "/regen",
            "/continue@BridgeBot": "/continue",
            "@someone /regen": "/regen",
            "@someone /continue": "/continue",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    rt._operation_command(raw),
                    expected,
                )

    def test_recovery_no_longer_wraps_process_message(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "recovery.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("\ndef process_message(", source)
        self.assertNotIn("_ORIGINAL_PROCESS_MESSAGE", source)
        self.assertNotIn("_OPERATION_CONTEXT", source)
```

The normalization test intentionally uses the runtime private helper during RED. After GREEN, `_operation_command` is owned by `message_commands.py` and remains available in the shared namespace with the same behavior.

- [ ] **Step 2: Run and verify RED on the source guard**

```bash
python -m pytest \
  tests/test_operation_recovery.py::DurableRecoveryOwnershipTests::test_recovery_no_longer_wraps_process_message \
  -q
```

Expected: FAIL.

- [ ] **Step 3: Move `_operation_command` into `message_commands.py`**

```python
def _operation_command(text):
    parts = str(text or "").strip().split(None, 1)
    if not parts:
        return ""
    command = parts[0].casefold()
    if command.startswith("@") and len(parts) > 1:
        command = parts[1].split(None, 1)[0].casefold()
    if command.startswith("/") and "@" in command:
        command = command.split("@", 1)[0]
    return command
```

- [ ] **Step 4: Insert command-specific local-commit routing before generic recovery**

Inside canonical `process_message`, after loading `session` and resolving injected memory/persona collaborators but before the existing generic `local_committed` block:

```python
if (
    operation_id is not None
    and operation_phase(db, operation_id) == "local_committed"
):
    recovery_command = _operation_command(text)
    recovery_fields = card_fields_from_file(
        session["character_file"]
    )
    if recovery_command == "/regen":
        return regenerate_last(
            db,
            token,
            api_key,
            session,
            recovery_fields,
            chat_id,
            operation_id=operation_id,
            memory_service=memory_service,
        )
    if recovery_command == "/continue":
        return continue_last(
            db,
            token,
            api_key,
            session,
            recovery_fields,
            chat_id,
            operation_id=operation_id,
            memory_service=memory_service,
        )
    if recovery_command == "/edit":
        edited = str(text or "").strip().split(None, 1)
        edited_text = (
            edited[1].strip()
            if len(edited) > 1
            else ""
        )
        return edit_last_user(
            db,
            token,
            api_key,
            session,
            recovery_fields,
            chat_id,
            edited_text,
            operation_id=operation_id,
            memory_service=memory_service,
        )
```

Do not delete or rewrite the generic local-committed block. It remains the fallback for all non-special operations and still persists `external_delivered` before `applied`.

- [ ] **Step 5: Delete the late `process_message` wrapper and context machinery**

From `recovery.py` remove:

- `import contextvars`,
- `_OPERATION_CONTEXT`,
- `_ORIGINAL_PROCESS_MESSAGE`,
- `_operation_command`,
- late `process_message`.

Remove `process_message` from the runtime override allowlist.

- [ ] **Step 6: Run routing, reset, and runtime-loader tests**

```bash
python -m pytest tests/test_operation_recovery.py -q
python -m pytest tests/test_runtime_loader.py -q
```

Expected: PASS, including bot-addressed/mention-prefixed command cases and reset resume tests.

- [ ] **Step 7: Commit**

```bash
git add \
  bridge/message_commands.py \
  bridge/recovery.py \
  bridge/runtime_loader.py \
  tests/test_operation_recovery.py
git commit -m "refactor: canonicalize message recovery routing"
```

---

### Task 7: Retire all durable private residue from `recovery.py` and lock the runtime boundary

**Files:**
- Modify: `bridge/recovery.py:entire file`
- Modify: `bridge/runtime_loader.py:20-60`
- Modify: `tests/test_operation_recovery.py`
- Modify: `tests/test_runtime_loader.py`

**Interfaces:**
- Consumes: all canonical ownership established in Tasks 3–6.
- Produces: `recovery.py` as Sync-UI-only compatibility; runtime allowlist exactly three names.

- [ ] **Step 1: Add final source-boundary tests**

In `tests/test_operation_recovery.py`:

```python
    def test_recovery_contains_no_durable_recovery_residue(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "recovery.py"
        ).read_text(encoding="utf-8")
        forbidden = (
            "begin_operation",
            "regenerate_last",
            "continue_last",
            "regenerate_edited_turn",
            "process_message",
            "_operation_payload_key",
            "_set_operation_payload",
            "_get_operation_payload",
            "_finish_operation",
            "_begin_durable_operation",
            "_OPERATION_CONTEXT",
            "_ORIGINAL_PROCESS_MESSAGE",
        )
        for name in forbidden:
            with self.subTest(name=name):
                self.assertNotIn(name, source)

    def test_operation_recovery_is_not_a_runtime_stage(self):
        modules = {
            module
            for stage in rt.DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        }
        self.assertNotIn("operation_recovery.py", modules)
```

In `tests/test_runtime_loader.py` add:

```python
    def test_recovery_allowlist_is_sync_ui_only(self):
        recovery_stage = next(
            stage
            for stage in DEFAULT_RUNTIME_STAGES
            if stage.name == "recovery_overrides"
        )
        self.assertEqual(
            recovery_stage.allowed_overrides_for("recovery.py"),
            frozenset(
                {
                    "sync_status_text",
                    "send_sync_menu",
                    "handle_sync_callback",
                }
            ),
        )

    def test_runtime_report_recovery_overrides_are_sync_ui_only(self):
        report = next(
            entry
            for entry in rt.RUNTIME_LOAD_REPORT
            if entry["module"] == "recovery.py"
        )
        self.assertEqual(
            frozenset(report["public_callable_overrides"]),
            frozenset(
                {
                    "sync_status_text",
                    "send_sync_menu",
                    "handle_sync_callback",
                }
            ),
        )
```

- [ ] **Step 2: Run final boundary tests and verify RED**

Run:

```bash
python -m pytest \
  tests/test_operation_recovery.py::DurableRecoveryOwnershipTests::test_recovery_contains_no_durable_recovery_residue \
  tests/test_runtime_loader.py::RuntimeLoaderTests::test_recovery_allowlist_is_sync_ui_only \
  -q
```

Expected: at least the recovery-residue test FAILS until dead private helpers are removed. If the allowlist is already at three names from prior atomic cutovers, that assertion may already pass.

- [ ] **Step 3: Reduce `recovery.py` to the three Sync UI functions**

Delete all durable-operation private helpers and stale crash-recovery module description. Keep the bodies of these functions byte-for-byte unchanged:

```text
sync_status_text
send_sync_menu
handle_sync_callback
```

Use a narrow module docstring describing temporary Live Sync UI compatibility. Do not alter Sync copy, callback behavior, or service resolution.

- [ ] **Step 4: Make the runtime allowlist exactly Sync UI**

`bridge/runtime_loader.py`:

```python
RuntimeStage(
    "recovery_overrides",
    ("recovery.py",),
    ((
        "recovery.py",
        (
            "sync_status_text",
            "send_sync_menu",
            "handle_sync_callback",
        ),
    ),),
),
```

- [ ] **Step 5: Run boundary and behavior suites**

```bash
python -m pytest tests/test_operation_recovery.py -q
python -m pytest tests/test_runtime_loader.py -q
python -m pytest tests/test_memory_service.py -q
```

Expected: PASS.

- [ ] **Step 6: Inspect Sync UI diff for accidental behavior changes**

Run:

```bash
git diff 49c2796f66581ad4ecd1cb85c89b70c6c2b618bf -- bridge/recovery.py
```

Expected: durable-operation sections are deleted; the three Sync UI function bodies are unchanged apart from line movement caused by deletion.

- [ ] **Step 7: Commit**

```bash
git add \
  bridge/recovery.py \
  bridge/runtime_loader.py \
  tests/test_operation_recovery.py \
  tests/test_runtime_loader.py
git commit -m "refactor: retire durable recovery runtime overrides"
```

---

### Task 8: Exact-head verification and branch review

**Files:**
- No production change expected.
- Inspect all Phase 6G changed files and the approved spec.

**Interfaces:**
- Consumes: completed Tasks 1–7.
- Produces: exact-head evidence suitable for PR review; no merge.

- [ ] **Step 1: Verify branch drift before final testing**

Run:

```bash
git fetch upstream main
git rev-list --left-right --count upstream/main...HEAD
```

Expected: `0 <N>` for behind/ahead when upstream has not moved. If behind is nonzero, inspect `git diff HEAD..upstream/main -- bridge tests` before deciding whether to rebase; do not silently ignore overlapping drift.

- [ ] **Step 2: Compile exactly as CI does**

```bash
python -m compileall -q bridge tests sillytavern_telegram_bridge.py
```

Expected: exit 0.

- [ ] **Step 3: Run the full unittest suite exactly as CI does**

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 4: Run the full pytest suite exactly as CI does**

```bash
python -m pytest -q
```

Expected: all tests PASS, including subtests.

- [ ] **Step 5: Validate installed dependencies**

```bash
python -m pip check
```

Expected: `No broken requirements found.`

- [ ] **Step 6: Audit the locked dependencies**

```bash
python -m pip_audit -r requirements.lock
```

Expected: no known vulnerabilities.

- [ ] **Step 7: Run explicit source scans**

```bash
grep -R "_OPERATION_CONTEXT\|_ORIGINAL_PROCESS_MESSAGE" -n bridge tests || true
grep -n \
  "def begin_operation\|def regenerate_last\|def continue_last\|def regenerate_edited_turn\|def process_message" \
  bridge/recovery.py || true
```

Expected: first command prints nothing; second prints nothing.

- [ ] **Step 8: Confirm runtime recovery report**

```bash
python - <<'PY'
import bridge.runtime as rt

entry = next(
    item
    for item in rt.RUNTIME_LOAD_REPORT
    if item["module"] == "recovery.py"
)
print(entry)
assert set(entry["public_callable_overrides"]) == {
    "sync_status_text",
    "send_sync_menu",
    "handle_sync_callback",
}
PY
```

Expected: assertion succeeds and only the three Sync UI names are printed as overrides.

- [ ] **Step 9: Review the whole branch against the approved baseline**

Run:

```bash
git diff --stat 49c2796f66581ad4ecd1cb85c89b70c6c2b618bf...HEAD
git diff 49c2796f66581ad4ecd1cb85c89b70c6c2b618bf...HEAD -- \
  bridge/database.py \
  bridge/operation_recovery.py \
  bridge/generation.py \
  bridge/commands.py \
  bridge/message_commands.py \
  bridge/recovery.py \
  bridge/runtime_loader.py \
  tests/test_operation_recovery.py \
  tests/test_runtime_loader.py
```

Review specifically for:

- no JobService changes;
- no Sync UI body changes;
- no schema/migration changes;
- no new runtime stage;
- no shared generic private adapter name that can be overwritten by later exec-loaded modules;
- no provider call reachable from `local_committed` command recovery;
- no finish call before successful external delivery.

- [ ] **Step 10: Verify exact branch head in CI**

If the execution harness has no local checkout/runner, open or update the Phase 6G draft PR so GitHub Actions runs the authoritative `.github/workflows/ci.yml` workflow against the exact branch head.

Record the exact head SHA and confirm:

- compile: success;
- unittest: success;
- pytest: success;
- `pip check`: success;
- `pip-audit`: success;
- PR mergeability: mergeable;
- unresolved review threads: none or explicitly addressed.

Do not merge. Hand off the verified PR for separate user approval.

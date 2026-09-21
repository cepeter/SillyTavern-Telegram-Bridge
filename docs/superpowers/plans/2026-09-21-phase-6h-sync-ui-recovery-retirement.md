# Phase 6H — Sync UI Recovery Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the final three Sync UI callables into canonical panel modules, delete `bridge/recovery.py`, and remove the `recovery_overrides` runtime stage without changing Sync behavior.

**Architecture:** `status_panels.py` becomes the canonical owner of Sync status/menu rendering, while `panel_callback_routes.py` owns Sync callback interaction. `sync_api.py` remains the SyncService/backend owner; the shared runtime continues to resolve `resolve_sync_service` at call time, so no replacement adapter or compatibility stage is introduced.

**Tech Stack:** Python 3.11, existing shared runtime loader, `unittest`, `pytest`, stdlib `unittest.mock`, GitHub Actions CI.

**Spec:** `docs/superpowers/specs/2026-09-21-phase-6h-sync-ui-recovery-retirement-design.md`

## Global Constraints

- Baseline upstream `main` is `d5be29779fe0da0f681c9da22f1d675cb196e38a`.
- Preserve `/sync` visible text, button labels, callback data, and message-edit behavior exactly.
- Preserve `handle_sync_callback` behavior and answer text exactly.
- Preserve optional `sync_service=None` injection on all three migrated functions.
- Keep SyncService construction, fallback resolution, realtime polling, conflict handling, and worker lifecycle in `sync_api.py` unchanged.
- Keep panel ownership, expiration, and authorization behavior in `callbacks.py` unchanged.
- Do not change JobService, callback job durability, schema, migrations, or Sync backend transport.
- Do not add a new `sync_ui.py` module, runtime stage, compatibility shim, adapter, or service locator.
- Delete `bridge/recovery.py` completely in the final cutover.
- Delete the complete `recovery_overrides` stage from `DEFAULT_RUNTIME_STAGES`.
- Do not merge the implementation PR automatically.

## Review Focus

1. **Injected SyncService vs fallback resolution** — an explicitly supplied service must be used for status/menu/callback operations; the migration must not accidentally construct a compatibility service instead.
2. **Sync backend/status exception** — exceptions from `status()` must still propagate rather than be converted into a fake status or swallowed by the UI layer.
3. **Long callback result** — `sync:realtime` and `sync:now` callback answers must remain capped at 200 characters while the full backend action still executes.
4. **Unknown/non-Sync callback data** — unrelated callbacks must return `False` without resolving/mutating Sync state; unknown `sync:*` actions must return `True` and answer `Unknown sync action`.
5. **Panel message identity** — an explicit `message_id` must be forwarded unchanged when refreshing/editing the Sync menu so the existing panel is updated rather than duplicated.

## File Structure

- Create `tests/test_sync_ui.py` — focused behavior characterization and canonical source-ownership guards for the three Sync UI callables.
- Modify `bridge/status_panels.py` — add canonical `sync_status_text` and `send_sync_menu`.
- Modify `bridge/panel_callback_routes.py` — add canonical `handle_sync_callback`.
- Modify `bridge/recovery.py` during intermediate tasks, then delete it in the final cutover.
- Modify `bridge/runtime_loader.py` — progressively shrink and finally remove `recovery_overrides`; update its descriptive docstring.
- Modify `tests/test_sync_service.py` — point the existing raw-backend source-boundary test at the canonical callback owner.
- Modify `tests/test_runtime_loader.py` — remove recovery-stage expectations and assert final absence.

---

### Task 1: Characterize the current Sync UI behavior

**Files:**
- Create: `tests/test_sync_ui.py`
- Read only: `bridge/recovery.py`

**Interfaces:**
- Consumes: current public runtime functions `sync_status_text(db, chat_id, session, *, sync_service=None) -> str`, `send_sync_menu(token, chat_id, db, session, message_id=None, *, sync_service=None) -> None`, and `handle_sync_callback(..., *, sync_service=None) -> bool`.
- Produces: behavior tests that must stay green when ownership moves out of `recovery.py`.

- [ ] **Step 1: Create focused Sync UI characterization tests**

Create `tests/test_sync_ui.py`:

```python
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import bridge.runtime as rt


class SyncUiBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.db = object()
        self.session = {"session_id": "session"}
        self.service = Mock()
        self.service.status.return_value = SimpleNamespace(
            session_id="session",
            message_count=7,
            sync_id="stb-test",
            last_synced_at=0.0,
            last_direction="",
            realtime_enabled=False,
            api_configured=False,
        )

    def test_status_renders_never_synced_disabled_unconfigured(self):
        text = rt.sync_status_text(
            self.db,
            "chat",
            self.session,
            sync_service=self.service,
        )

        self.service.status.assert_called_once_with(
            self.db,
            "chat",
            "session",
        )
        self.assertEqual(
            text,
            "Live Sync\\n\\n"
            "Live Sync uses the local SillyTavern API.\\n"
            "Session: session\\n"
            "Messages: 7\\n"
            "Sync ID: stb-test\\n"
            "Last sync: never\\n\\n"
            "Live API sync: off (not configured)",
        )

    def test_status_renders_last_direction_timestamp_and_enabled_state(self):
        self.service.status.return_value = SimpleNamespace(
            session_id="session",
            message_count=9,
            sync_id="stb-test",
            last_synced_at=123.0,
            last_direction="bridge_to_sillytavern_api",
            realtime_enabled=True,
            api_configured=True,
        )

        with patch.object(
            rt.time,
            "localtime",
            return_value="LOCAL",
        ) as localtime, patch.object(
            rt.time,
            "strftime",
            return_value="2026-09-21 09:00:00 WIB",
        ) as strftime:
            text = rt.sync_status_text(
                self.db,
                "chat",
                self.session,
                sync_service=self.service,
            )

        localtime.assert_called_once_with(123.0)
        strftime.assert_called_once_with(
            "%Y-%m-%d %H:%M:%S %Z",
            "LOCAL",
        )
        self.assertIn(
            "Last sync: bridge_to_sillytavern_api "
            "at 2026-09-21 09:00:00 WIB",
            text,
        )
        self.assertIn(
            "Live API sync: on (configured)",
            text,
        )

    def test_status_error_propagates(self):
        self.service.status.side_effect = RuntimeError("status failed")

        with self.assertRaisesRegex(
            RuntimeError,
            "status failed",
        ):
            rt.sync_status_text(
                self.db,
                "chat",
                self.session,
                sync_service=self.service,
            )

    def test_send_sync_menu_preserves_keyboard_and_message_id(self):
        with patch.object(
            rt,
            "send_panel_message",
        ) as send_panel:
            rt.send_sync_menu(
                "token",
                "chat",
                self.db,
                self.session,
                91,
                sync_service=self.service,
            )

        send_panel.assert_called_once()
        token, chat_id, text, markup, message_id = (
            send_panel.call_args.args
        )
        self.assertEqual(token, "token")
        self.assertEqual(chat_id, "chat")
        self.assertEqual(message_id, 91)
        self.assertEqual(
            markup,
            {
                "inline_keyboard": [
                    [
                        {
                            "text": "🚀 Realtime API: toggle",
                            "callback_data": "sync:realtime",
                        }
                    ],
                    [
                        {
                            "text": "🔁 Sync now",
                            "callback_data": "sync:now",
                        }
                    ],
                    [
                        {
                            "text": "🔄 Refresh status",
                            "callback_data": "sync:status",
                        }
                    ],
                    [
                        {
                            "text": "❌ Close",
                            "callback_data": "sync:close",
                        }
                    ],
                ]
            },
        )
        self.assertIn("Live Sync", text)
        self.service.status.assert_called_once_with(
            self.db,
            "chat",
            "session",
        )

    def test_non_sync_callback_returns_false_without_service_resolution(self):
        with patch.object(
            rt,
            "resolve_sync_service",
            side_effect=AssertionError(
                "non-sync callback resolved SyncService"
            ),
        ):
            handled = rt.handle_sync_callback(
                self.db,
                "token",
                {"id": "cb"},
                Mock(),
                "prompt:status",
                "chat",
                {"message_id": 91},
                self.session,
                "session",
                123,
            )

        self.assertFalse(handled)

    def test_sync_close_answers_and_closes_panel(self):
        answer = Mock()
        callback = {"id": "cb"}
        message = {"message_id": 91}

        with patch.object(
            rt,
            "close_panel_message",
        ) as close:
            handled = rt.handle_sync_callback(
                self.db,
                "token",
                callback,
                answer,
                "sync:close",
                "chat",
                message,
                self.session,
                "session",
                123,
                sync_service=self.service,
            )

        self.assertTrue(handled)
        answer.assert_called_once_with(
            "token",
            "cb",
            "Closed",
        )
        close.assert_called_once_with(
            "token",
            "chat",
            callback,
        )
        self.service.toggle_realtime.assert_not_called()
        self.service.sync_now.assert_not_called()

    def test_sync_status_and_menu_refresh_existing_panel(self):
        for data in ("sync:menu", "sync:status"):
            with self.subTest(data=data):
                answer = Mock()
                with patch.object(
                    rt,
                    "send_sync_menu",
                ) as send_menu:
                    handled = rt.handle_sync_callback(
                        self.db,
                        "token",
                        {"id": "cb"},
                        answer,
                        data,
                        "chat",
                        {"message_id": 91},
                        self.session,
                        "session",
                        123,
                        sync_service=self.service,
                    )

                self.assertTrue(handled)
                answer.assert_called_once_with(
                    "token",
                    "cb",
                    "Sync status",
                )
                send_menu.assert_called_once_with(
                    "token",
                    "chat",
                    self.db,
                    self.session,
                    91,
                    sync_service=self.service,
                )

    def test_sync_realtime_truncates_answer_and_refreshes(self):
        self.service.toggle_realtime.return_value = "x" * 250
        answer = Mock()

        with patch.object(
            rt,
            "send_sync_menu",
        ) as send_menu:
            handled = rt.handle_sync_callback(
                self.db,
                "token",
                {"id": "cb"},
                answer,
                "sync:realtime",
                "chat",
                {"message_id": 91},
                self.session,
                "session",
                123,
                sync_service=self.service,
            )

        self.assertTrue(handled)
        self.service.toggle_realtime.assert_called_once_with(
            self.db,
            "chat",
            "session",
        )
        answer.assert_called_once_with(
            "token",
            "cb",
            "x" * 200,
        )
        send_menu.assert_called_once_with(
            "token",
            "chat",
            self.db,
            self.session,
            91,
            sync_service=self.service,
        )

    def test_sync_now_truncates_answer_and_refreshes(self):
        self.service.sync_now.return_value = "y" * 250
        answer = Mock()

        with patch.object(
            rt,
            "send_sync_menu",
        ) as send_menu:
            handled = rt.handle_sync_callback(
                self.db,
                "token",
                {"id": "cb"},
                answer,
                "sync:now",
                "chat",
                {"message_id": 91},
                self.session,
                "session",
                123,
                sync_service=self.service,
            )

        self.assertTrue(handled)
        self.service.sync_now.assert_called_once_with(
            self.db,
            "chat",
            "session",
        )
        answer.assert_called_once_with(
            "token",
            "cb",
            "y" * 200,
        )
        send_menu.assert_called_once_with(
            "token",
            "chat",
            self.db,
            self.session,
            91,
            sync_service=self.service,
        )

    def test_unknown_sync_action_is_handled_without_mutation(self):
        answer = Mock()

        handled = rt.handle_sync_callback(
            self.db,
            "token",
            {"id": "cb"},
            answer,
            "sync:unknown",
            "chat",
            {"message_id": 91},
            self.session,
            "session",
            123,
            sync_service=self.service,
        )

        self.assertTrue(handled)
        answer.assert_called_once_with(
            "token",
            "cb",
            "Unknown sync action",
        )
        self.service.toggle_realtime.assert_not_called()
        self.service.sync_now.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run characterization tests**

Run:

```bash
python -m pytest tests/test_sync_ui.py -q
```

Expected: PASS against the merged Phase 6G runtime. If a behavior test fails because current behavior differs from the approved spec, stop and reconcile the spec before moving production code.

- [ ] **Step 3: Commit the characterization contract**

```bash
git add tests/test_sync_ui.py
git commit -m "test: pin sync ui compatibility behavior"
```

---

### Task 2: Make `status_panels.py` the canonical Sync status/menu owner

**Files:**
- Modify: `tests/test_sync_ui.py`
- Modify: `bridge/status_panels.py`
- Modify: `bridge/recovery.py`
- Modify: `bridge/runtime_loader.py`

**Interfaces:**
- Consumes: late-bound `resolve_sync_service(sync_service=None)` and existing `send_panel_message(token, chat_id, text, markup, message_id)`.
- Produces: canonical `sync_status_text(...) -> str` and `send_sync_menu(...) -> None` from `status_panels.py`.

- [ ] **Step 1: Add failing source-ownership tests**

Append before the `if __name__` block in `tests/test_sync_ui.py`:

```python
from pathlib import Path


class SyncUiOwnershipTests(unittest.TestCase):
    def test_status_panels_owns_sync_status_and_menu(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "status_panels.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "\\ndef sync_status_text(",
            source,
        )
        self.assertIn(
            "\\ndef send_sync_menu(",
            source,
        )

    def test_recovery_no_longer_defines_sync_status_or_menu(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "recovery.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            "\\ndef sync_status_text(",
            source,
        )
        self.assertNotIn(
            "\\ndef send_sync_menu(",
            source,
        )
```

- [ ] **Step 2: Run ownership tests and verify RED**

Run:

```bash
python -m pytest \
  tests/test_sync_ui.py::SyncUiOwnershipTests::test_status_panels_owns_sync_status_and_menu \
  tests/test_sync_ui.py::SyncUiOwnershipTests::test_recovery_no_longer_defines_sync_status_or_menu \
  -q
```

Expected: both FAIL because the functions still live in `recovery.py`.

- [ ] **Step 3: Add the canonical Sync status/menu functions to `status_panels.py`**

Insert after `status_text` and before `prompt_panel_text`:

```python
def sync_status_text(
    db,
    chat_id,
    session,
    *,
    sync_service=None,
):
    """Render Live API Sync status for the active session."""
    sync_service = resolve_sync_service(sync_service)
    status = sync_service.status(
        db,
        chat_id,
        session["session_id"],
    )
    if status.last_synced_at:
        timestamp = time.strftime(
            "%Y-%m-%d %H:%M:%S %Z",
            time.localtime(status.last_synced_at),
        )
        last = f"{status.last_direction} at {timestamp}"
    else:
        last = "never"
    enabled = "on" if status.realtime_enabled else "off"
    configured = (
        "configured"
        if status.api_configured
        else "not configured"
    )
    return (
        "Live Sync\\n\\n"
        "Live Sync uses the local SillyTavern API.\\n"
        f"Session: {status.session_id}\\n"
        f"Messages: {status.message_count}\\n"
        f"Sync ID: {status.sync_id}\\n"
        f"Last sync: {last}\\n\\n"
        f"Live API sync: {enabled} ({configured})"
    )


def send_sync_menu(
    token,
    chat_id,
    db,
    session,
    message_id=None,
    *,
    sync_service=None,
):
    """Send or edit the Live API Sync panel."""
    sync_service = resolve_sync_service(sync_service)
    payload = {
        "chat_id": chat_id,
        "text": sync_status_text(
            db,
            chat_id,
            session,
            sync_service=sync_service,
        ),
        "reply_markup": {
            "inline_keyboard": [
                [
                    {
                        "text": "🚀 Realtime API: toggle",
                        "callback_data": "sync:realtime",
                    }
                ],
                [
                    {
                        "text": "🔁 Sync now",
                        "callback_data": "sync:now",
                    }
                ],
                [
                    {
                        "text": "🔄 Refresh status",
                        "callback_data": "sync:status",
                    }
                ],
                [
                    {
                        "text": "❌ Close",
                        "callback_data": "sync:close",
                    }
                ],
            ]
        },
    }
    send_panel_message(
        token,
        chat_id,
        payload["text"],
        payload["reply_markup"],
        message_id,
    )
```

This is a formatting-only relocation of the current behavior; do not change copy, callbacks, or service resolution.

- [ ] **Step 4: Remove only the two migrated functions from `recovery.py`**

After this step `bridge/recovery.py` must contain the module docstring plus the unchanged `handle_sync_callback` definition only:

```python
"""Temporary Live Sync UI compatibility loaded before Phase 6H cleanup."""


def handle_sync_callback(
    db,
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    message,
    session,
    session_id,
    operation_id,
    *,
    sync_service=None,
):
    """Handle Live API Sync callbacks."""
    if not data.startswith("sync:"):
        return False
    sync_service = resolve_sync_service(sync_service)
    action = data.split(":", 1)[1]
    message_id = message.get("message_id")
    if action == "close":
        answer_callback(
            token,
            str(callback.get("id", "")),
            "Closed",
        )
        close_panel_message(token, chat_id, callback)
    elif action in {"menu", "status"}:
        answer_callback(
            token,
            str(callback.get("id", "")),
            "Sync status",
        )
        send_sync_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            sync_service=sync_service,
        )
    elif action == "realtime":
        result = sync_service.toggle_realtime(
            db,
            chat_id,
            session_id,
        )
        answer_callback(
            token,
            str(callback.get("id", "")),
            result[:200],
        )
        send_sync_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            sync_service=sync_service,
        )
    elif action == "now":
        result = sync_service.sync_now(
            db,
            chat_id,
            session_id,
        )
        answer_callback(
            token,
            str(callback.get("id", "")),
            result[:200],
        )
        send_sync_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            sync_service=sync_service,
        )
    else:
        answer_callback(
            token,
            str(callback.get("id", "")),
            "Unknown sync action",
        )
    return True
```

- [ ] **Step 5: Shrink the intermediate recovery allowlist**

In `bridge/runtime_loader.py` make the temporary recovery stage:

```python
RuntimeStage(
    "recovery_overrides",
    ("recovery.py",),
    ((
        "recovery.py",
        (
            "handle_sync_callback",
        ),
    ),),
),
```

- [ ] **Step 6: Run Sync UI and loader tests**

Run:

```bash
python -m pytest tests/test_sync_ui.py -q
python -m pytest tests/test_runtime_loader.py -q
```

Expected: PASS. The public runtime now obtains `sync_status_text` and `send_sync_menu` from `status_panels.py` while `handle_sync_callback` remains temporarily compatible.

- [ ] **Step 7: Commit**

```bash
git add \
  bridge/status_panels.py \
  bridge/recovery.py \
  bridge/runtime_loader.py \
  tests/test_sync_ui.py
git commit -m "refactor: canonicalize sync status panel ui"
```

---

### Task 3: Make `panel_callback_routes.py` the canonical Sync callback owner

**Files:**
- Modify: `tests/test_sync_ui.py`
- Modify: `tests/test_sync_service.py`
- Modify: `bridge/panel_callback_routes.py`
- Modify: `bridge/recovery.py`
- Modify: `bridge/runtime_loader.py`

**Interfaces:**
- Consumes: late-bound `resolve_sync_service`, canonical `send_sync_menu` from Task 2, and existing `close_panel_message`.
- Produces: canonical `handle_sync_callback(..., *, sync_service=None) -> bool` from `panel_callback_routes.py`.

- [ ] **Step 1: Add failing callback ownership tests**

Add to `SyncUiOwnershipTests`:

```python
    def test_panel_callback_routes_owns_sync_callback(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "panel_callback_routes.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "\\ndef handle_sync_callback(",
            source,
        )

    def test_recovery_no_longer_defines_sync_callback(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "recovery.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            "\\ndef handle_sync_callback(",
            source,
        )
```

- [ ] **Step 2: Run ownership tests and verify RED**

Run:

```bash
python -m pytest \
  tests/test_sync_ui.py::SyncUiOwnershipTests::test_panel_callback_routes_owns_sync_callback \
  tests/test_sync_ui.py::SyncUiOwnershipTests::test_recovery_no_longer_defines_sync_callback \
  -q
```

Expected: FAIL because callback ownership is still in `recovery.py`.

- [ ] **Step 3: Add canonical `handle_sync_callback` to `panel_callback_routes.py`**

Insert immediately before `handle_primary_panel_callback`:

```python
def handle_sync_callback(
    db,
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    message,
    session,
    session_id,
    operation_id,
    *,
    sync_service=None,
):
    """Handle Live API Sync callbacks."""
    if not data.startswith("sync:"):
        return False
    sync_service = resolve_sync_service(sync_service)
    action = data.split(":", 1)[1]
    message_id = message.get("message_id")
    if action == "close":
        answer_callback(
            token,
            str(callback.get("id", "")),
            "Closed",
        )
        close_panel_message(token, chat_id, callback)
    elif action in {"menu", "status"}:
        answer_callback(
            token,
            str(callback.get("id", "")),
            "Sync status",
        )
        send_sync_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            sync_service=sync_service,
        )
    elif action == "realtime":
        result = sync_service.toggle_realtime(
            db,
            chat_id,
            session_id,
        )
        answer_callback(
            token,
            str(callback.get("id", "")),
            result[:200],
        )
        send_sync_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            sync_service=sync_service,
        )
    elif action == "now":
        result = sync_service.sync_now(
            db,
            chat_id,
            session_id,
        )
        answer_callback(
            token,
            str(callback.get("id", "")),
            result[:200],
        )
        send_sync_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            sync_service=sync_service,
        )
    else:
        answer_callback(
            token,
            str(callback.get("id", "")),
            "Unknown sync action",
        )
    return True
```

- [ ] **Step 4: Retarget the existing raw-backend source-boundary test**

In `tests/test_sync_service.py` replace only the source file for `test_sync_callback_does_not_call_raw_execution_backends`:

```python
    def test_sync_callback_does_not_call_raw_execution_backends(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "panel_callback_routes.py"
        ).read_text(encoding="utf-8")
        chunk = self._function_chunk(
            source,
            "def handle_sync_callback",
        )
        for forbidden in (
            "phase3_sync_now(",
            "phase3_toggle_realtime(",
            "_phase3_disable(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, chunk)
```

- [ ] **Step 5: Reduce `recovery.py` to a temporary empty compatibility file**

The complete file becomes:

```python
"""Temporary empty recovery compatibility stage pending Phase 6H deletion."""
```

- [ ] **Step 6: Remove the last recovery allowlist entry**

Keep the stage temporarily, but with no allowed replacements:

```python
RuntimeStage(
    "recovery_overrides",
    ("recovery.py",),
),
```

This intermediate state proves the canonical functions work before the file/stage is deleted.

- [ ] **Step 7: Run UI, service-boundary, and loader suites**

Run:

```bash
python -m pytest tests/test_sync_ui.py -q
python -m pytest tests/test_sync_service.py -q
python -m pytest tests/test_runtime_loader.py -q
```

Expected: PASS. `recovery.py` is now behavior-free.

- [ ] **Step 8: Commit**

```bash
git add \
  bridge/panel_callback_routes.py \
  bridge/recovery.py \
  bridge/runtime_loader.py \
  tests/test_sync_ui.py \
  tests/test_sync_service.py
git commit -m "refactor: canonicalize sync callback routing"
```

---

### Task 4: Delete the recovery compatibility layer and runtime stage

**Files:**
- Delete: `bridge/recovery.py`
- Modify: `bridge/runtime_loader.py`
- Modify: `tests/test_runtime_loader.py`
- Modify: `tests/test_sync_ui.py`

**Interfaces:**
- Consumes: canonical Sync UI ownership established in Tasks 2–3.
- Produces: no recovery compatibility file/stage; `DEFAULT_RUNTIME_STAGES` transitions directly from `core` to `sync_extensions`.

- [ ] **Step 1: Replace intermediate ownership assumptions with final absence tests**

In `tests/test_sync_ui.py` replace the two tests that read `recovery.py` with one final test:

```python
    def test_recovery_compatibility_file_is_absent(self):
        recovery = (
            Path(__file__).parents[1]
            / "bridge"
            / "recovery.py"
        )
        self.assertFalse(recovery.exists())
```

Keep the positive canonical-owner tests for `status_panels.py` and `panel_callback_routes.py`.

- [ ] **Step 2: Update the runtime report stage expectation**

In `tests/test_runtime_loader.py::test_runtime_exposes_structured_load_report` make the expected stages exactly:

```python
        self.assertEqual(
            stages,
            {
                "core",
                "sync_extensions",
                "native_adapter_overrides",
                "identity_extensions",
                "safety_overrides",
            },
        )
```

Delete the obsolete:

```python
        self.assertEqual(by_module["recovery.py"], ())
```

Keep all non-recovery module assertions unchanged.

- [ ] **Step 3: Replace recovery allowlist/report tests with final absence guards**

Delete:

- `test_recovery_allowlist_is_sync_ui_only`
- `test_runtime_report_recovery_has_no_actual_overrides`

Add:

```python
    def test_recovery_stage_and_module_are_absent(self):
        self.assertNotIn(
            "recovery_overrides",
            {
                stage.name
                for stage in DEFAULT_RUNTIME_STAGES
            },
        )
        self.assertNotIn(
            "recovery.py",
            {
                module
                for stage in DEFAULT_RUNTIME_STAGES
                for module in stage.modules
            },
        )

    def test_runtime_report_has_no_recovery_entry(self):
        self.assertNotIn(
            "recovery_overrides",
            {
                entry["stage"]
                for entry in rt.RUNTIME_LOAD_REPORT
            },
        )
        self.assertNotIn(
            "recovery.py",
            {
                entry["module"]
                for entry in rt.RUNTIME_LOAD_REPORT
            },
        )
```

- [ ] **Step 4: Run the final absence tests and verify RED before deleting the layer**

Run:

```bash
python -m pytest \
  tests/test_sync_ui.py::SyncUiOwnershipTests::test_recovery_compatibility_file_is_absent \
  tests/test_runtime_loader.py::RuntimeLoaderTests::test_recovery_stage_and_module_are_absent \
  tests/test_runtime_loader.py::RuntimeLoaderTests::test_runtime_report_has_no_recovery_entry \
  -q
```

Expected: FAIL because `recovery.py` and `recovery_overrides` still exist.

- [ ] **Step 5: Delete `bridge/recovery.py`**

```bash
git rm bridge/recovery.py
```

Do not replace it with a shim.

- [ ] **Step 6: Remove the complete recovery stage from `runtime_loader.py`**

The beginning of `DEFAULT_RUNTIME_STAGES` becomes:

```python
DEFAULT_RUNTIME_STAGES = (
    RuntimeStage(
        "core",
        (
            "common.py", "performance.py", "native_cache.py",
            "cards.py", "schema.py", "database.py", "memory.py",
            "rag.py", "groups.py", "telegram.py",
            "persona_delete_panel.py",
            "language.py", "greetings.py", "help_details.py",
            "help.py", "input_flows.py", "catalog.py",
            "update.py", "image_generation.py",
            "expressions.py", "media.py", "generation.py",
            "commands.py", "status_panels.py",
            "command_routes.py", "message_commands.py",
            "callbacks.py", "panel_callback_routes.py",
            "main.py",
        ),
    ),
    RuntimeStage(
        "sync_extensions",
        ("sync_core.py", "sync_api.py"),
    ),
```

Leave later stages unchanged.

- [ ] **Step 7: Update the runtime-loader module description**

Replace the stale recovery wording with:

```python
"""Validated loader for the legacy shared runtime namespace.

The bridge still exposes one compatibility namespace, but load order and
intentional extension overrides are described explicitly here instead of
being an implicit property of a flat exec() loop. Core/extension modules are
not allowed to silently replace public callables. Only explicitly declared
adapter/safety stages may replace public callables.
"""
```

- [ ] **Step 8: Run final focused suites**

```bash
python -m pytest tests/test_sync_ui.py -q
python -m pytest tests/test_sync_service.py -q
python -m pytest tests/test_runtime_loader.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add \
  bridge/runtime_loader.py \
  tests/test_sync_ui.py \
  tests/test_runtime_loader.py
git add -u bridge/recovery.py
git commit -m "refactor: retire recovery runtime stage"
```

---

### Task 5: Exact-head verification and PR readiness

**Files:**
- No production changes expected.
- Review all Phase 6H changed files plus the approved spec and this plan.

**Interfaces:**
- Consumes: completed Tasks 1–4.
- Produces: exact-head verification evidence and an unmerged Phase 6H PR ready for user review.

- [ ] **Step 1: Verify upstream drift**

Run:

```bash
git fetch upstream main
git rev-list --left-right --count upstream/main...HEAD
```

Expected: `0 <N>` for behind/ahead when upstream has not moved. If behind is nonzero, inspect overlapping changes before rebasing.

- [ ] **Step 2: Compile exactly as repository CI does**

```bash
python -m compileall -q bridge tests sillytavern_telegram_bridge.py
```

Expected: exit 0.

- [ ] **Step 3: Run full unittest**

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 4: Run full pytest**

```bash
python -m pytest -q
```

Expected: all tests and subtests PASS.

- [ ] **Step 5: Validate installed dependencies**

```bash
python -m pip check
```

Expected: `No broken requirements found.`

- [ ] **Step 6: Audit locked dependencies**

```bash
python -m pip_audit -r requirements.lock
```

Expected: no known vulnerabilities.

- [ ] **Step 7: Run explicit final source/composition guards**

```bash
test ! -e bridge/recovery.py
grep -R "recovery_overrides" -n bridge tests || true
grep -R '"recovery.py"' -n bridge/runtime_loader.py tests/test_runtime_loader.py || true
```

Expected: `test` succeeds and both `grep` commands print nothing.

- [ ] **Step 8: Verify canonical callable filenames and runtime report**

```bash
python - <<'PY'
from pathlib import Path
import bridge.runtime as rt

assert Path(rt.sync_status_text.__code__.co_filename).name == "status_panels.py"
assert Path(rt.send_sync_menu.__code__.co_filename).name == "status_panels.py"
assert Path(rt.handle_sync_callback.__code__.co_filename).name == "panel_callback_routes.py"

assert "recovery_overrides" not in {
    entry["stage"]
    for entry in rt.RUNTIME_LOAD_REPORT
}
assert "recovery.py" not in {
    entry["module"]
    for entry in rt.RUNTIME_LOAD_REPORT
}
print("Phase 6H ownership/runtime boundary verified")
PY
```

Expected: prints `Phase 6H ownership/runtime boundary verified`.

- [ ] **Step 9: Review whole branch against approved scope**

Run:

```bash
git diff --stat d5be29779fe0da0f681c9da22f1d675cb196e38a...HEAD
git diff d5be29779fe0da0f681c9da22f1d675cb196e38a...HEAD -- \
  bridge/status_panels.py \
  bridge/panel_callback_routes.py \
  bridge/runtime_loader.py \
  bridge/recovery.py \
  tests/test_sync_ui.py \
  tests/test_sync_service.py \
  tests/test_runtime_loader.py
```

Review specifically for:

- Sync UI copy and callback data unchanged;
- no `sync_api.py` or Sync backend changes;
- no callback authorization/session-binding changes;
- no JobService/schema/migration changes;
- no replacement compatibility module or runtime stage;
- explicit `sync_service` injection retained;
- no hidden early-bound SyncService instance.

- [ ] **Step 10: Run exact-head GitHub Actions and prepare PR**

Open a draft Phase 6H PR if needed so the repository's authoritative CI runs against the exact branch head. Confirm:

- compile: success;
- unittest: success;
- pytest: success;
- `pip check`: success;
- `pip-audit`: success;
- branch: 0 behind current `main`;
- PR: mergeable;
- review threads/comments: none unresolved;
- whole-branch review: no Critical or Important findings.

Then mark the PR ready for review. Do not merge it.

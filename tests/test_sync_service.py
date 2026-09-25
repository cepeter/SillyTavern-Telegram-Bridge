from application_test_setup import ensure_application_extensions
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge.sync_api as _m_sync_api
from bridge.sync_service import SyncService, SyncStatus


class ExpectedSyncError(RuntimeError):
    pass


class SyncServiceTests(SettingsTestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.binding = {
            "sync_id": "stb-test",
            "last_synced_at": 123.0,
            "last_direction": "bridge_to_sillytavern_api",
            "realtime_enabled": 1,
        }
        self.disabled = []
        self.polls = []
        self.sync_result = "unchanged"
        self.toggle_result = "realtime API sync disabled"
        self.api_ready = True

        self.service = SyncService(
            load_binding=lambda _db, _chat, _session: dict(self.binding),
            count_messages=lambda _db, _chat, _session: 7,
            sync_now_backend=self._sync_now,
            toggle_realtime_backend=lambda *_args: self.toggle_result,
            poll_backend=lambda db: self.polls.append(db),
            disable_realtime=lambda _db, chat, session, error: self.disabled.append((chat, session, error)),
            api_configured=lambda: self.api_ready,
            expected_errors=(ExpectedSyncError, ValueError),
        )

    def tearDown(self):
        self.db.close()

    def _sync_now(self, *_args):
        if isinstance(self.sync_result, BaseException):
            raise self.sync_result
        return self.sync_result

    def test_status_is_structured_and_read_only(self):
        status = self.service.status(self.db, "chat", "session")
        self.assertEqual(
            status,
            SyncStatus(
                session_id="session",
                message_count=7,
                sync_id="stb-test",
                last_synced_at=123.0,
                last_direction="bridge_to_sillytavern_api",
                realtime_enabled=True,
                api_configured=True,
            ),
        )
        self.assertFalse(self.db.in_transaction)

    def test_status_handles_unconfigured_api_and_never_synced_binding(self):
        self.binding.update(
            {
                "last_synced_at": 0,
                "last_direction": "",
                "realtime_enabled": 0,
            }
        )
        self.api_ready = False

        status = self.service.status(self.db, "chat", "session")

        self.assertEqual(status.last_synced_at, 0.0)
        self.assertEqual(status.last_direction, "")
        self.assertFalse(status.realtime_enabled)
        self.assertFalse(status.api_configured)

    def test_manual_sync_delegates_success(self):
        self.assertEqual(
            self.service.sync_now(self.db, "chat", "session"),
            "unchanged",
        )
        self.assertEqual(self.disabled, [])

    def test_expected_manual_failure_disables_realtime_and_returns_feedback(self):
        self.sync_result = ExpectedSyncError("API unavailable")

        self.assertEqual(
            self.service.sync_now(self.db, "chat", "session"),
            "Live API unavailable: API unavailable",
        )
        self.assertEqual(
            self.disabled,
            [("chat", "session", "API unavailable")],
        )

    def test_value_error_manual_failure_uses_same_existing_feedback(self):
        self.sync_result = ValueError("bad snapshot")

        self.assertEqual(
            self.service.sync_now(self.db, "chat", "session"),
            "Live API unavailable: bad snapshot",
        )
        self.assertEqual(
            self.disabled,
            [("chat", "session", "bad snapshot")],
        )

    def test_unexpected_manual_failure_propagates(self):
        self.sync_result = RuntimeError("bug")

        with self.assertRaisesRegex(RuntimeError, "bug"):
            self.service.sync_now(self.db, "chat", "session")
        self.assertEqual(self.disabled, [])

    def test_toggle_preserves_backend_result(self):
        self.assertEqual(
            self.service.toggle_realtime(self.db, "chat", "session"),
            "realtime API sync disabled",
        )

    def test_poll_delegates_to_injected_backend(self):
        self.service.poll(self.db)
        self.assertEqual(self.polls, [self.db])


class SyncSourceBoundaryTests(SettingsTestCase):
    def _function_chunk(self, source, marker):
        start = source.index(marker)
        next_def = source.find("\ndef ", start + len(marker))
        return source[start : next_def if next_def >= 0 else None]

    def test_sync_callback_does_not_call_raw_execution_backends(self):
        source = (Path(__file__).parents[1] / "bridge" / "sync_callbacks.py").read_text(encoding="utf-8")
        chunk = self._function_chunk(
            source,
            "def handle_sync_callback",
        )
        for forbidden in (
            "live_sync_now(",
            "live_sync_toggle_realtime(",
            "_live_sync_disable(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, chunk)

    def test_realtime_worker_does_not_call_raw_poll_backend(self):
        source = (Path(__file__).parents[1] / "bridge" / "sync_api.py").read_text(encoding="utf-8")
        chunk = self._function_chunk(
            source,
            "def _live_sync_worker_loop",
        )
        self.assertNotIn("live_sync_poll(", chunk)
        self.assertNotIn("resolve_sync_service", chunk)
        self.assertIn("sync_service.poll(", chunk)

    def test_realtime_worker_uses_supplied_sync_service_without_resolution(self):
        class FakeDb:
            in_transaction = False

            def close(self):
                pass

        class FakeSyncService:
            def __init__(self):
                self.polls = 0

            def poll(self, _db):
                self.polls += 1

        service = FakeSyncService()
        with (
            patch.object(
                _m_sync_api._LIVE_SYNC_STOP_EVENT,
                "wait",
                side_effect=[False, True],
            ),
            patch.object(
                _m_sync_api,
                "db_connect",
                return_value=FakeDb(),
            ),
        ):
            _m_sync_api._live_sync_worker_loop(service, app_settings=self.app_settings_builder.build())

        self.assertEqual(service.polls, 1)

    def test_sync_service_has_no_runtime_or_telegram_dependency(self):
        source = (Path(__file__).parents[1] / "bridge" / "sync_service.py").read_text(encoding="utf-8")
        self.assertNotIn("bridge.runtime", source)
        self.assertNotIn("bridge.telegram", source)
        self.assertNotIn("telegram_request", source)


if __name__ == "__main__":
    unittest.main()

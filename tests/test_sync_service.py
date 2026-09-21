from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from dependency_patch import dependency_module

_m_sync_api = dependency_module("bridge.sync_api")
_m_sync_core = dependency_module("bridge.sync_core")
from bridge.sync_service import SyncService, SyncStatus


class ExpectedSyncError(RuntimeError):
    pass


class SyncServiceTests(unittest.TestCase):
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
            disable_realtime=lambda _db, chat, session, error:
                self.disabled.append((chat, session, error)),
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


class SyncSourceBoundaryTests(unittest.TestCase):
    def _function_chunk(self, source, marker):
        start = source.index(marker)
        next_def = source.find("\ndef ", start + len(marker))
        return source[start: next_def if next_def >= 0 else None]

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

    def test_realtime_worker_does_not_call_raw_poll_backend(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "sync_api.py"
        ).read_text(encoding="utf-8")
        chunk = self._function_chunk(
            source,
            "def _phase3_worker_loop",
        )
        self.assertNotIn("phase3_sync_poll(", chunk)
        self.assertIn("sync_service.poll(", chunk)

    def test_sync_service_has_no_runtime_or_telegram_dependency(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "sync_service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("bridge.runtime", source)
        self.assertNotIn("bridge.telegram", source)
        self.assertNotIn("telegram_request", source)

    def test_compatibility_service_uses_canonical_hardened_poll(self):
        service = _m_sync_api.compatibility_sync_service()
        self.assertIs(service.poll_backend, _m_sync_api.phase3_sync_poll)
        self.assertEqual(
            Path(
                _m_sync_api.phase3_sync_poll.__code__.co_filename
            ).name,
            "sync_api.py",
        )
        self.assertFalse(
            (Path(__file__).parents[1] / "bridge" / "runtime_loader.py").exists()
        )


class SyncCompatibilityServiceTests(unittest.TestCase):
    def test_compatibility_service_late_binds_final_poll_backend(self):
        def final_poll(_db):
            return None

        with patch.object(
            _m_sync_api, "phase3_sync_poll", final_poll
        ), patch.object(
            _m_sync_core, "sync_binding"
        ) as binding, patch.object(
            _m_sync_api, "phase3_sync_now"
        ) as sync_now, patch.object(
            _m_sync_api, "phase3_toggle_realtime"
        ) as toggle, patch.object(
            _m_sync_api, "_phase3_disable"
        ) as disable, patch.object(
            _m_sync_api, "phase3_api_configured", return_value=True
        ):
            service = _m_sync_api.compatibility_sync_service()

        self.assertIs(service.poll_backend, final_poll)
        self.assertIs(service.load_binding, binding)
        self.assertIs(service.sync_now_backend, sync_now)
        self.assertIs(service.toggle_realtime_backend, toggle)
        self.assertIs(service.disable_realtime, disable)

    def test_resolve_sync_service_prefers_injected_service(self):
        sentinel = object()
        self.assertIs(_m_sync_api.resolve_sync_service(sentinel), sentinel)


if __name__ == "__main__":
    unittest.main()

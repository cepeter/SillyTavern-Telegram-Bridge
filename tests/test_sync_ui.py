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
            "Live Sync\n\n"
            "Live Sync uses the local SillyTavern API.\n"
            "Session: session\n"
            "Messages: 7\n"
            "Sync ID: stb-test\n"
            "Last sync: never\n\n"
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

    def test_status_uses_fallback_resolver_when_service_is_omitted(self):
        with patch.object(
            rt,
            "resolve_sync_service",
            return_value=self.service,
        ) as resolve:
            text = rt.sync_status_text(
                self.db,
                "chat",
                self.session,
            )

        resolve.assert_called_once_with(None)
        self.service.status.assert_called_once_with(
            self.db,
            "chat",
            "session",
        )
        self.assertIn("Live Sync", text)

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

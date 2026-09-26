import unittest
from unittest.mock import patch

from settings_test_support import SettingsTestCase

import bridge.response_delivery as response_delivery


class ResponseDeliveryTests(SettingsTestCase):
    def test_send_reply_sanitizes_stored_html_at_delivery_boundary(self):
        sent = []
        with patch.object(
            response_delivery,
            "send_text",
            side_effect=lambda _token, _chat, text: sent.append(text) or [72],
        ):
            response_delivery.send_reply(
                "token",
                "chat",
                "<div>Recovered<br>reply</div>",
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(sent, ["Recovered\nreply"])

    def test_send_reply_reuses_streaming_preview_as_final_message(self):
        requests = []
        sent = []
        persisted = []

        def edit_existing(_token, method, payload):
            requests.append((method, payload))
            raise RuntimeError("Telegram editMessageText failed: Bad Request: message is not modified")

        with (
            patch.object(
                response_delivery,
                "telegram_request",
                side_effect=edit_existing,
            ),
            patch.object(
                response_delivery,
                "send_text",
                side_effect=lambda _token, _chat, text: sent.append(text) or [72],
            ),
            patch.object(
                response_delivery,
                "persist_assistant_delivery_ids",
                side_effect=lambda _db, rowid, ids: persisted.append((rowid, ids)) or True,
            ),
        ):
            response_delivery.send_reply(
                "token",
                "chat",
                "Final response",
                object(),
                None,
                42,
                replace_message_id=71,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(
            requests,
            [
                (
                    "editMessageText",
                    {
                        "chat_id": "chat",
                        "message_id": 71,
                        "text": "Final response",
                        "disable_web_page_preview": True,
                    },
                )
            ],
        )
        self.assertEqual(sent, [])
        self.assertEqual(persisted, [(42, [71])])

    def test_send_reply_falls_back_when_streaming_preview_is_gone(self):
        sent = []
        persisted = []
        with (
            patch.object(
                response_delivery,
                "telegram_request",
                side_effect=RuntimeError("Telegram editMessageText failed: Bad Request: message to edit not found"),
            ),
            patch.object(
                response_delivery,
                "send_text",
                side_effect=lambda _token, _chat, text: sent.append(text) or [72],
            ),
            patch.object(
                response_delivery,
                "persist_assistant_delivery_ids",
                side_effect=lambda _db, rowid, ids: persisted.append((rowid, ids)) or True,
            ),
        ):
            response_delivery.send_reply(
                "token",
                "chat",
                "Final response",
                object(),
                None,
                42,
                replace_message_id=71,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(sent, ["Final response"])
        self.assertEqual(persisted, [(42, [72])])

    def test_send_reply_reuses_preview_then_sends_only_remaining_chunks(self):
        requests = []
        sent = []
        persisted = []
        reply = "A" * 4100

        with (
            patch.object(
                response_delivery,
                "telegram_request",
                side_effect=lambda _token, method, payload: requests.append((method, payload)) or {},
            ),
            patch.object(
                response_delivery,
                "send_text",
                side_effect=lambda _token, _chat, text: sent.append(text) or [72],
            ),
            patch.object(
                response_delivery,
                "persist_assistant_delivery_ids",
                side_effect=lambda _db, rowid, ids: persisted.append((rowid, ids)) or True,
            ),
        ):
            response_delivery.send_reply(
                "token",
                "chat",
                reply,
                object(),
                None,
                42,
                replace_message_id=71,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(len(requests[0][1]["text"]), 4000)
        self.assertEqual(sent, ["A" * 100])
        self.assertEqual(persisted, [(42, [71, 72])])


if __name__ == "__main__":
    unittest.main()

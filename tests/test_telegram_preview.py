import unittest

from settings_test_support import SettingsTestCase

import bridge.telegram as telegram


class TelegramPreviewTests(SettingsTestCase):
    def test_send_text_disables_link_previews(self):
        calls = []
        original_request = telegram.telegram_request
        telegram.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {"message_id": 1}
        try:
            self.assertEqual(
                telegram.send_text(
                    "token",
                    "chat",
                    "https://example.com/image.jpg",
                ),
                [1],
            )
        finally:
            telegram.telegram_request = original_request

        self.assertEqual(len(calls), 1)
        method, payload = calls[0]
        self.assertEqual(method, "sendMessage")
        self.assertTrue(payload["disable_web_page_preview"])


if __name__ == "__main__":
    unittest.main()

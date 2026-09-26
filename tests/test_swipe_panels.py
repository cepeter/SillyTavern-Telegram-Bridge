import unittest
from unittest.mock import Mock, patch

from settings_test_support import SettingsTestCase

import bridge.swipe_panels as swipe_panels


class SwipePanelOutputTests(SettingsTestCase):
    def test_swipe_panels_sanitize_stored_html_variants(self):
        delivery = Mock()
        delivery.send_panel_request.return_value = {"message_id": 91}
        variants = [(1, "<div>Recovered<br>variant</div>", 1)]

        with (
            patch.object(swipe_panels, "last_user_variants", return_value=((1, "prompt"), variants)),
            patch.object(swipe_panels, "set_meta"),
        ):
            swipe_panels.send_swipe_menu(
                "token",
                Mock(),
                "chat",
                "session",
                delivery_port=delivery,
                request_context=object(),
            )
            swipe_panels.edit_swipe_menu(
                "token",
                Mock(),
                {"message": {"chat": {"id": "chat"}, "message_id": 91}},
                "session",
                1,
                variants,
                delivery_port=delivery,
                request_context=object(),
            )

        payloads = [call.args[2] for call in delivery.send_panel_request.call_args_list]
        self.assertEqual(len(payloads), 2)
        for payload in payloads:
            self.assertNotIn("<div", payload["text"])
            self.assertNotIn("<br>", payload["text"])
            self.assertIn("Recovered\nvariant", payload["text"])


if __name__ == "__main__":
    unittest.main()

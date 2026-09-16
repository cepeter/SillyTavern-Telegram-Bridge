import unittest

import bridge.runtime as rt


class CatalogPanelTests(unittest.TestCase):
    def test_model_panel_treats_not_modified_as_success(self):
        original_groups = rt.get_model_groups
        original_request = rt.telegram_request
        rt.get_model_groups = lambda: {"provider": ("Provider", [("model", "provider::model")], True)}
        rt.telegram_request = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Telegram editMessageText failed: Bad Request: message is not modified"))
        try:
            rt.send_model_menu("token", "chat", "provider::model", message_id=10)
        finally:
            rt.get_model_groups = original_groups
            rt.telegram_request = original_request


if __name__ == "__main__":
    unittest.main()

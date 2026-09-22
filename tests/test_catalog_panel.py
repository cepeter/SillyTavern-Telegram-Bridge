from application_test_setup import ensure_application_extensions

ensure_application_extensions()

import json
import tempfile
import unittest
from pathlib import Path

import os
import bridge.catalog as _m_catalog
import bridge.cards as _m_cards
import bridge.generation as _m_generation
import bridge.panel_callback_routes as _m_panel_callback_routes
class CatalogPanelTests(unittest.TestCase):
    def test_hive_health_uses_streaming_chat_completion_not_models(self):
        old_config = _m_catalog.PROVIDER_CONFIG_FILE
        old_request = _m_catalog.strict_urlopen
        old_hosts = os.environ.get("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS")
        calls = []

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, *_args):
                return b"data: {}\n"

        def fake_urlopen(request, timeout):
            calls.append((request, timeout))
            return Response()

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "providers.yaml"
            config.write_text(
                "providers:\n  hive:\n    name: Hive\n    api_endpoint: https://api-cdn.thehive.ai/api/v3\n"
                "    api_key_env: TEST_HIVE_KEY\n    model: zai-org/glm-5.3-flash\n"
                "    transport: chat_completions\n    health_check: chat_completion\n    models:\n      - zai-org/glm-5.3-flash\n",
                encoding="utf-8",
            )
            _m_catalog.PROVIDER_CONFIG_FILE = config
            _m_catalog.strict_urlopen = fake_urlopen
            os.environ["TEST_HIVE_KEY"] = "test-only"
            os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = "api-cdn.thehive.ai"
            try:
                result = _m_catalog.provider_health_checks("hive")
            finally:
                _m_catalog.PROVIDER_CONFIG_FILE = old_config
                _m_catalog.strict_urlopen = old_request
                os.environ.pop("TEST_HIVE_KEY", None)
                if old_hosts is None:
                    os.environ.pop("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", None)
                else:
                    os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = old_hosts

        self.assertEqual(result[0][2], "healthy (chat completion)")
        self.assertEqual(len(calls), 1)
        request, _timeout = calls[0]
        self.assertTrue(request.full_url.endswith("/api/v3/chat/completions"))
        body = json.loads(request.data.decode("utf-8"))
        self.assertTrue(body["stream"])
        self.assertEqual(request.headers["Content-type"], "application/json")
        self.assertEqual(body["model"], "zai-org/glm-5.3-flash")

    def test_model_selection_offers_story_or_utility_target(self):
        calls = []
        original_request = _m_cards.telegram_request
        _m_cards.telegram_request = lambda _token, method, payload: calls.append((method, payload)) or {"message_id": 1}
        try:
            _m_panel_callback_routes.send_model_target_menu("token", "chat", "main::model", "utility::model")
        finally:
            _m_cards.telegram_request = original_request
        callbacks = [button["callback_data"] for row in calls[0][1]["reply_markup"]["inline_keyboard"] for button in row]
        self.assertIn("modeltarget:story", callbacks)
        self.assertIn("modeltarget:utility", callbacks)
        self.assertIn("Where should the next selected model be used?", calls[0][1]["text"])

    def test_model_panel_treats_not_modified_as_success(self):
        original_groups = _m_catalog.get_model_groups
        original_request = _m_cards.telegram_request
        _m_catalog.get_model_groups = lambda: {"provider": ("Provider", [("model", "provider::model")], True)}
        _m_cards.telegram_request = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Telegram editMessageText failed: Bad Request: message is not modified"))
        try:
            _m_panel_callback_routes.send_model_menu("token", "chat", "provider::model", message_id=10)
        finally:
            _m_catalog.get_model_groups = original_groups
            _m_cards.telegram_request = original_request


if __name__ == "__main__":
    unittest.main()

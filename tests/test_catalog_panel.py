from application_test_setup import ensure_application_extensions, make_test_request_context
from settings_test_support import SettingsTestCase

import bridge.provider_discovery as _owner_provider_discovery
import bridge.provider_panels as _owner_provider_panels

ensure_application_extensions()

import json
import os
import sqlite3
import unittest

import bridge.cards as _m_cards


class CatalogPanelTests(SettingsTestCase):
    def test_hive_health_uses_streaming_chat_completion_not_models(self):
        old_catalog = _owner_provider_discovery.load_provider_catalog
        old_request = _owner_provider_discovery.strict_urlopen
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

        def fake_urlopen(request, timeout, *, environ=None):
            calls.append((request, timeout))
            return Response()

        _owner_provider_discovery.load_provider_catalog = lambda *, app_settings=None: {
            "hive": {
                "name": "Hive",
                "api_endpoint": "https://api-cdn.thehive.ai/api/v3",
                "api_key_env": "TEST_HIVE_KEY",
                "model": "zai-org/glm-5.3-flash",
                "transport": "chat_completions",
                "health_check": "chat_completion",
                "models": ["zai-org/glm-5.3-flash"],
            }
        }
        _owner_provider_discovery.strict_urlopen = fake_urlopen
        os.environ["TEST_HIVE_KEY"] = "test-only"
        os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = "api-cdn.thehive.ai"
        try:
            result = _owner_provider_discovery.provider_health_checks(
                "hive", app_settings=self.app_settings_builder.build()
            )
        finally:
            _owner_provider_discovery.load_provider_catalog = old_catalog
            _owner_provider_discovery.strict_urlopen = old_request
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
        original_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: (
            calls.append((method, payload)) or {"message_id": 1}
        )
        try:
            _owner_provider_panels.send_model_target_menu(
                "token",
                "chat",
                "main::model",
                "utility::model",
                request_context=make_test_request_context(app_settings=self.app_settings_builder.build()),
            )
        finally:
            _m_cards.send_panel_request = original_request
        callbacks = [
            button["callback_data"] for row in calls[0][1]["reply_markup"]["inline_keyboard"] for button in row
        ]
        self.assertIn("modeltarget:story", callbacks)
        self.assertIn("modeltarget:utility", callbacks)
        self.assertIn("Where should the next selected model be used?", calls[0][1]["text"])

    def test_model_panel_treats_not_modified_as_success(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute(
            "CREATE TABLE callback_tokens(token TEXT PRIMARY KEY, kind TEXT, value TEXT, chat_id TEXT, expires_at REAL)"
        )
        original_groups = _owner_provider_panels.get_model_groups
        original_request = _m_cards.send_panel_request
        _owner_provider_panels.get_model_groups = lambda *, app_settings=None: {
            "provider": ("Provider", [("model", "provider::model")], True)
        }
        _m_cards.send_panel_request = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Telegram editMessageText failed: Bad Request: message is not modified")
        )
        try:
            _owner_provider_panels.send_model_menu(
                "token",
                "chat",
                "provider::model",
                message_id=10,
                request_context=make_test_request_context(db, app_settings=self.app_settings_builder.build()),
            )
        finally:
            _owner_provider_panels.get_model_groups = original_groups
            _m_cards.send_panel_request = original_request


if __name__ == "__main__":
    unittest.main()

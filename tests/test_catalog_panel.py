import json
import tempfile
import unittest
from pathlib import Path

import bridge.runtime as rt


class CatalogPanelTests(unittest.TestCase):
    def test_hive_health_uses_streaming_chat_completion_not_models(self):
        old_config = rt.PROVIDER_CONFIG_FILE
        old_request = rt.strict_urlopen
        old_hosts = rt.os.environ.get("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS")
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
            rt.PROVIDER_CONFIG_FILE = config
            rt.strict_urlopen = fake_urlopen
            rt.os.environ["TEST_HIVE_KEY"] = "test-only"
            rt.os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = "api-cdn.thehive.ai"
            try:
                result = rt.provider_health_checks("hive")
            finally:
                rt.PROVIDER_CONFIG_FILE = old_config
                rt.strict_urlopen = old_request
                rt.os.environ.pop("TEST_HIVE_KEY", None)
                if old_hosts is None:
                    rt.os.environ.pop("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", None)
                else:
                    rt.os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = old_hosts

        self.assertEqual(result[0][2], "healthy (chat completion)")
        self.assertEqual(len(calls), 1)
        request, _timeout = calls[0]
        self.assertTrue(request.full_url.endswith("/api/v3/chat/completions"))
        body = json.loads(request.data.decode("utf-8"))
        self.assertTrue(body["stream"])
        self.assertEqual(request.headers["Content-type"], "application/json")
        self.assertEqual(body["model"], "zai-org/glm-5.3-flash")

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

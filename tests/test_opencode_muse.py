from application_test_setup import ensure_application_extensions
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import json
import os
import unittest

import bridge.main as _m_main
import bridge.provider_transport as _m_provider_transport
from bridge.model_router import ModelRouter


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class OpenCodeMuseTests(SettingsTestCase):
    def setUp(self):
        self.old_urlopen = _m_provider_transport.strict_urlopen
        self.old_hosts = os.environ.get("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS")
        self.spec = {
            "transport": "opencode_muse",
            "api_endpoint": "https://opencode.ai/zen/v1",
        }
        self.router = ModelRouter(load_catalog=lambda: {"opencode-free": self.spec})
        os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = "opencode.ai"

    def tearDown(self):
        _m_provider_transport.strict_urlopen = self.old_urlopen
        if self.old_hosts is None:
            os.environ.pop("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", None)
        else:
            os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = self.old_hosts

    def test_startup_allows_keyless_muse_transport(self):
        old_key = os.environ.pop("LLM_API_KEY", None)
        try:
            _m_main.validate_startup_credential(
                "opencode-free::muse-spark-1.3-contributor-free",
                self.router,
                app_settings=self.app_settings_builder.build(),
            )
        finally:
            if old_key is not None:
                os.environ["LLM_API_KEY"] = old_key

    def test_startup_still_requires_credentials_for_keyed_transport(self):
        old_key = os.environ.pop("LLM_API_KEY", None)
        router = ModelRouter(load_catalog=lambda: {"provider": {"transport": "openai_compatible"}})
        try:
            with self.assertRaisesRegex(RuntimeError, "required provider credential"):
                _m_main.validate_startup_credential(
                    "provider::model", router, app_settings=self.app_settings_builder.build()
                )
        finally:
            if old_key is not None:
                os.environ["LLM_API_KEY"] = old_key

    def test_muse_uses_responses_and_canonical_keyless_headers(self):
        captured = []

        def fake_urlopen(request, timeout, *, environ=None):
            captured.append((request, timeout))
            return _Response({"output_text": "OPENCODE_BRIDGE_OK"})

        _m_provider_transport.strict_urlopen = fake_urlopen
        result = _m_provider_transport.generate_provider_text(
            self.router,
            "",
            "opencode-free::muse-spark-1.3-contributor-free",
            [{"role": "user", "content": "hello"}],
            session_id="telegram:chat:session",
            settings={"max_tokens": 128},
            app_settings=self.app_settings_builder.build(),
        )
        request, timeout = captured[0]
        body = json.loads(request.data.decode())
        self.assertEqual(result, "OPENCODE_BRIDGE_OK")
        self.assertTrue(request.full_url.endswith("/responses"))
        self.assertEqual(body["stream"], True)
        self.assertEqual(body["store"], False)
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual({tool["name"] for tool in body["tools"]}, {"bash", "glob", "grep", "read"})
        self.assertEqual(body["max_output_tokens"], 3000)
        self.assertEqual(body["reasoning"], {"effort": "low"})
        self.assertNotIn(
            "Authorization",
            {key: value for key, value in request.header_items() if key.lower() == "authorization" and value},
        )
        self.assertEqual(request.headers["Accept"], "text/event-stream")
        self.assertRegex(request.headers["X-opencode-session"], r"^ses_[0-9a-f]{12}[0-9A-Za-z]{14}$")
        self.assertRegex(request.headers["X-opencode-request"], r"^msg_[0-9a-f]{12}[0-9A-Za-z]{14}$")
        self.assertEqual(request.headers["X-opencode-client"], "cli")
        self.assertRegex(request.headers["User-agent"], r"^opencode/1\.\d+\.\d+$")
        self.assertEqual(timeout, 240)

    def test_muse_reads_required_responses_sse(self):
        captured = []

        def fake_urlopen(request, timeout, *, environ=None):
            captured.append((request, timeout))
            response = _Response({})
            response.read = lambda: (
                chr(10)
                .join(
                    "data: " + item
                    for item in [
                        '{"type":"response.output_text.delta","delta":"streamed"}',
                        '{"type":"response.output_text.delta","delta":" ok"}',
                        "[DONE]",
                    ]
                )
                .encode()
            )
            return response

        _m_provider_transport.strict_urlopen = fake_urlopen
        result = _m_provider_transport.opencode_muse_generate(
            "muse-spark-1.3-contributor-free",
            [{"role": "user", "content": "hello"}],
            {"max_tokens": 128},
            {"api_endpoint": "https://opencode.ai/zen/v1"},
            "telegram:chat:session",
            app_settings=self.app_settings_builder.build(),
        )
        self.assertEqual(result, "streamed ok")
        self.assertEqual(captured[0][1], 240)


if __name__ == "__main__":
    unittest.main()

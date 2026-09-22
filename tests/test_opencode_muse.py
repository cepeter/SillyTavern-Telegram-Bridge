from application_test_setup import ensure_application_extensions

ensure_application_extensions()

import json
import re
import unittest

import os
import bridge.generation as _m_generation
import bridge.main as _m_main
import bridge.memory_curator as _m_memory_curator
class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class OpenCodeMuseTests(unittest.TestCase):
    def setUp(self):
        self.old_resolve = _m_generation.resolve_provider_model
        self.old_spec = _m_generation.get_provider_spec
        self.old_urlopen = _m_generation.strict_urlopen
        self.old_hosts = os.environ.get("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS")
        _m_generation.resolve_provider_model = lambda _model: ("opencode-free", "muse-spark-1.3-contributor-free")
        _m_generation.get_provider_spec = lambda _provider: {"transport": "opencode_muse", "api_endpoint": "https://opencode.ai/zen/v1"}
        self.old_main_resolve = _m_main.resolve_provider_model
        self.old_main_spec = _m_main.get_provider_spec
        _m_main.resolve_provider_model = _m_generation.resolve_provider_model
        _m_main.get_provider_spec = _m_generation.get_provider_spec
        os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = "opencode.ai"

    def tearDown(self):
        _m_generation.resolve_provider_model = self.old_resolve
        _m_generation.get_provider_spec = self.old_spec
        _m_generation.strict_urlopen = self.old_urlopen
        _m_main.resolve_provider_model = self.old_main_resolve
        _m_main.get_provider_spec = self.old_main_spec
        if self.old_hosts is None:
            os.environ.pop("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", None)
        else:
            os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = self.old_hosts

    def test_startup_allows_keyless_muse_transport(self):
        old_key = os.environ.pop("LLM_API_KEY", None)
        try:
            _m_main.validate_startup_credential("opencode-free::muse-spark-1.3-contributor-free")
        finally:
            if old_key is not None:
                os.environ["LLM_API_KEY"] = old_key

    def test_startup_still_requires_credentials_for_keyed_transport(self):
        old_key = os.environ.pop("LLM_API_KEY", None)
        old_spec = _m_main.get_provider_spec
        _m_main.get_provider_spec = lambda _provider: {"transport": "openai_chat"}
        try:
            with self.assertRaisesRegex(RuntimeError, "required provider credential"):
                _m_main.validate_startup_credential("provider::model")
        finally:
            _m_main.get_provider_spec = old_spec
            if old_key is not None:
                os.environ["LLM_API_KEY"] = old_key

    def test_muse_uses_responses_and_canonical_keyless_headers(self):
        captured = []

        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return _Response({"output_text": "OPENCODE_BRIDGE_OK"})

        _m_generation.strict_urlopen = fake_urlopen
        result = _m_memory_curator.generate_text("", "opencode-free::muse-spark-1.3-contributor-free", [{"role": "user", "content": "hello"}], session_id="telegram:chat:session", settings={"max_tokens": 128})
        request, timeout = captured[0]
        body = json.loads(request.data.decode())
        self.assertEqual(result, "OPENCODE_BRIDGE_OK")
        self.assertTrue(request.full_url.endswith("/responses"))
        self.assertEqual(body["stream"], False)
        self.assertEqual(body["store"], False)
        self.assertEqual(body["max_output_tokens"], 3000)
        self.assertEqual(body["reasoning"], {"effort": "low"})
        self.assertNotIn("Authorization", {key: value for key, value in request.header_items() if key.lower() == "authorization" and value})
        self.assertRegex(request.headers["X-opencode-session"], r"^ses_[0-9a-f]{12}[0-9A-Za-z]{14}$")
        self.assertRegex(request.headers["X-opencode-request"], r"^msg_[0-9a-f]{12}[0-9A-Za-z]{14}$")
        self.assertEqual(request.headers["X-opencode-client"], "cli")
        self.assertRegex(request.headers["User-agent"], r"^opencode/1\.\d+\.\d+$")
        self.assertEqual(timeout, 180)


if __name__ == "__main__":
    unittest.main()

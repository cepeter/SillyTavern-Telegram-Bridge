import json
import re
import unittest

import bridge.runtime as rt


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
        self.old_resolve = rt.resolve_provider_model
        self.old_spec = rt.get_provider_spec
        self.old_urlopen = rt.strict_urlopen
        self.old_hosts = rt.os.environ.get("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS")
        rt.resolve_provider_model = lambda _model: ("opencode-free", "muse-spark-1.3-contributor-free")
        rt.get_provider_spec = lambda _provider: {"transport": "opencode_muse", "api_endpoint": "https://opencode.ai/zen/v1"}
        rt.os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = "opencode.ai"

    def tearDown(self):
        rt.resolve_provider_model = self.old_resolve
        rt.get_provider_spec = self.old_spec
        rt.strict_urlopen = self.old_urlopen
        if self.old_hosts is None:
            rt.os.environ.pop("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", None)
        else:
            rt.os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = self.old_hosts

    def test_muse_uses_responses_and_canonical_keyless_headers(self):
        captured = []

        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return _Response({"output_text": "OPENCODE_BRIDGE_OK"})

        rt.strict_urlopen = fake_urlopen
        result = rt.generate_text("", "opencode-free::muse-spark-1.3-contributor-free", [{"role": "user", "content": "hello"}], session_id="telegram:chat:session", settings={"max_tokens": 128})
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

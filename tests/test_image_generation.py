import base64
import json
import tempfile
import unittest
from pathlib import Path

import os
from dependency_patch import dependency_module

_m_generation = dependency_module("bridge.generation")
_m_image_generation = dependency_module("bridge.image_generation")


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode()


class ImageGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.catalog = Path(self.temp.name) / "providers.yaml"
        self.catalog.write_text(
            "providers:\n  test-image:\n    name: Test Image\n    api_endpoint: https://images.example/v1\n    api_key_env: TEST_IMAGE_KEY\n    image_enabled: true\n    image_models: [test-model]\n",
            encoding="utf-8",
        )
        self.old_catalog = _m_generation.PROVIDER_CONFIG_FILE
        self.old_urlopen = _m_generation.strict_urlopen
        self.old_key = os.environ.get("TEST_IMAGE_KEY")
        _m_generation.PROVIDER_CONFIG_FILE = self.catalog
        os.environ["TEST_IMAGE_KEY"] = "test-key"

    def tearDown(self):
        _m_generation.PROVIDER_CONFIG_FILE = self.old_catalog
        _m_generation.strict_urlopen = self.old_urlopen
        if self.old_key is None:
            os.environ.pop("TEST_IMAGE_KEY", None)
        else:
            os.environ["TEST_IMAGE_KEY"] = self.old_key
        self.temp.cleanup()

    def test_base64_contract(self):
        captured = []
        raw = b"PNG-DATA"

        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return _Response({"data": [{"b64_json": base64.b64encode(raw).decode(), "revised_prompt": "revised"}]})

        _m_generation.strict_urlopen = fake_urlopen
        result = _m_image_generation.generate_image("test-image::test-model", "a small moon", "1024x1024")
        body = json.loads(captured[0][0].data.decode())
        self.assertEqual(result, (raw, "revised", "test-image::test-model"))
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["response_format"], "b64_json")
        self.assertEqual(captured[0][1], 180)

    def test_prompt_and_size_are_validated(self):
        with self.assertRaises(ValueError):
            _m_image_generation.generate_image("test-image::test-model", "", "1024x1024")
        with self.assertRaises(ValueError):
            _m_image_generation.generate_image("test-image::test-model", "a prompt", "999x999")

    def test_disabled_provider_fails_closed(self):
        self.catalog.write_text("providers: {}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "No image provider"):
            _m_image_generation.generate_image("", "a prompt")

    def test_command_is_registered(self):
        source = Path(_m_image_generation.__file__).parent / "help.py"
        self.assertIn('"command": "imagine"', source.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

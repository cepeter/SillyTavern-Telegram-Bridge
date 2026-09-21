import json
import unittest

import os
from dependency_patch import dependency_module

_m_generation = dependency_module("bridge.generation")
_m_memory_curator = dependency_module("bridge.memory_curator")
_m_message_commands = dependency_module("bridge.message_commands")
_m_sync_core = dependency_module("bridge.sync_core")


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class _FakeStreamResponse(_FakeResponse):
    def __init__(self, lines):
        super().__init__(None)
        self.lines = [line.encode("utf-8") for line in lines]

    def __iter__(self):
        return iter(self.lines)


class GenerationContinuationTests(unittest.TestCase):
    def setUp(self):
        self.original_resolve = _m_generation.resolve_provider_model
        self.original_spec = _m_generation.get_provider_spec
        self.original_urlopen = _m_generation.strict_urlopen
        self.old_key = os.environ.get("TEST_OPENROUTER_KEY")
        self.old_hosts = os.environ.get("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS")
        _m_generation.resolve_provider_model = lambda _model: ("openrouter", "test/model")
        _m_generation.get_provider_spec = lambda _provider: {
            "transport": "openai_compatible",
            "api_endpoint": "https://openrouter.ai/api/v1",
            "api_key_env": "TEST_OPENROUTER_KEY",
        }
        os.environ["TEST_OPENROUTER_KEY"] = "test-only"
        os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = "openrouter.ai"

    def tearDown(self):
        _m_generation.resolve_provider_model = self.original_resolve
        _m_generation.get_provider_spec = self.original_spec
        _m_generation.strict_urlopen = self.original_urlopen
        if self.old_key is None:
            os.environ.pop("TEST_OPENROUTER_KEY", None)
        else:
            os.environ["TEST_OPENROUTER_KEY"] = self.old_key
        if self.old_hosts is None:
            os.environ.pop("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", None)
        else:
            os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = self.old_hosts

    def test_empty_stream_length_retries_with_larger_non_stream_budget(self):
        self.original_spec = _m_generation.get_provider_spec
        _m_generation.get_provider_spec = lambda _provider: {
            "transport": "openai_compatible",
            "api_endpoint": "https://openrouter.ai/api/v1",
            "api_key_env": "TEST_OPENROUTER_KEY",
            "streaming": True,
        }
        responses = [
            _FakeStreamResponse([
                'data: {"choices":[{"delta":{"reasoning":"thinking"},"finish_reason":null}]}\n',
                'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
                "data: [DONE]\n",
            ]),
            _FakeStreamResponse([
                'data: {"choices":[{"delta":{"content":"Recovered answer."},"finish_reason":"stop"}]}\n',
                "data: [DONE]\n",
            ]),
        ]
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(json.loads(request.data.decode()))
            return responses.pop(0)

        _m_generation.strict_urlopen = fake_urlopen
        result = _m_memory_curator.generate_text(
            "",
            "test",
            [{"role": "user", "content": "Write a complete answer."}],
            settings={**_m_sync_core.GENERATION_DEFAULTS, "max_tokens": 1800},
        )

        self.assertEqual(result, "Recovered answer.")
        self.assertEqual(len(requests), 2)
        self.assertTrue(requests[0]["stream"])
        self.assertTrue(requests[1]["stream"])
        self.assertEqual(requests[1]["max_tokens"], 4096)

    def test_streaming_length_continuation_stays_streaming(self):
        self.original_spec = _m_generation.get_provider_spec
        _m_generation.get_provider_spec = lambda _provider: {
            "transport": "openai_compatible",
            "api_endpoint": "https://openrouter.ai/api/v1",
            "api_key_env": "TEST_OPENROUTER_KEY",
            "streaming": True,
        }
        responses = [
            _FakeStreamResponse([
                'data: {"choices":[{"delta":{"content":"Part one."},"finish_reason":null}]}\n',
                'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
                "data: [DONE]\n",
            ]),
            _FakeStreamResponse([
                'data: {"choices":[{"delta":{"content":"Part two."},"finish_reason":"stop"}]}\n',
                "data: [DONE]\n",
            ]),
        ]
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(json.loads(request.data.decode()))
            return responses.pop(0)

        _m_generation.strict_urlopen = fake_urlopen
        result = _m_memory_curator.generate_text(
            "",
            "test",
            [{"role": "user", "content": "Write a complete answer."}],
            settings={**_m_sync_core.GENERATION_DEFAULTS, "max_tokens": 1800},
        )

        self.assertEqual(result, "Part one. Part two.")
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request["stream"] for request in requests))
        self.assertEqual([item["role"] for item in requests[1]["messages"][-2:]], ["assistant", "user"])

    def test_length_finish_adds_one_bounded_continuation(self):
        payloads = [
            {"choices": [{"message": {"content": "Part one."}, "finish_reason": "length"}]},
            {"choices": [{"message": {"content": "Part two."}, "finish_reason": "stop"}]},
        ]
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(json.loads(request.data.decode()))
            return _FakeResponse(payloads.pop(0))

        _m_generation.strict_urlopen = fake_urlopen
        result = _m_memory_curator.generate_text(
            "",
            "test",
            [{"role": "user", "content": "Write a complete answer."}],
            settings=dict(_m_sync_core.GENERATION_DEFAULTS),
        )

        self.assertEqual(result, "Part one. Part two.")
        self.assertEqual(len(requests), 2)
        self.assertEqual([item["role"] for item in requests[1]["messages"][-2:]], ["assistant", "user"])
        self.assertEqual(requests[1]["messages"][-2]["content"], "Part one.")
        self.assertIn("exact ending", requests[1]["messages"][-1]["content"])

    def test_failed_automatic_continuation_keeps_first_segment(self):
        calls = 0

        def fake_urlopen(_request, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                return _FakeResponse(
                    {"choices": [{"message": {"content": "Partial but usable."}, "finish_reason": "length"}]}
                )
            raise RuntimeError("temporary provider failure")

        _m_generation.strict_urlopen = fake_urlopen
        result = _m_memory_curator.generate_text(
            "",
            "test",
            [{"role": "user", "content": "Write a complete answer."}],
            settings=dict(_m_sync_core.GENERATION_DEFAULTS),
        )

        self.assertEqual(result, "Partial but usable.")
        self.assertEqual(calls, 2)

    def test_auto_language_render_returns_original_without_backend_call(self):
        original_generate = _m_memory_curator.generate_text
        _m_memory_curator.generate_text = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("auto must not render"))
        try:
            result = _m_message_commands.render_response_language("", "model", "original", "auto", "session", {})
        finally:
            _m_memory_curator.generate_text = original_generate
        self.assertEqual(result, "original")

    def test_fixed_language_render_uses_minimal_indonesian_rewrite(self):
        captured = {}
        original_generate = _m_memory_curator.generate_text

        def fake_generate(api_key, model, messages, session_id="telegram", settings=None, **_kwargs):
            captured.update({"messages": messages, "session_id": session_id, "settings": settings})
            return "hasil Indonesia"

        _m_memory_curator.generate_text = fake_generate
        try:
            result = _m_message_commands.render_response_language(
                "", "model", "English source", "id", "telegram:chat:session", dict(_m_sync_core.GENERATION_DEFAULTS)
            )
        finally:
            _m_memory_curator.generate_text = original_generate
        self.assertEqual(result, "hasil Indonesia")
        self.assertEqual(captured["session_id"], "telegram:chat:session:language-render")
        self.assertIn("Bahasa Indonesia (id)", captured["messages"][0]["content"])
        self.assertEqual(captured["messages"][1]["content"], "<source_text>\nEnglish source\n</source_text>")
        self.assertEqual(captured["settings"]["temperature"], 0.2)
        self.assertEqual(captured["settings"]["reasoning_budget"], 0)


if __name__ == "__main__":
    unittest.main()

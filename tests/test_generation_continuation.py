from application_test_setup import ensure_application_extensions
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import json
import os
import threading
import unittest

import bridge.generation as _m_generation
import bridge.provider_transport as _m_provider_transport
import bridge.sync_core as _m_sync_core
from bridge.model_router import ModelRoute
from bridge.provider_port import ProviderPort


class _FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

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


class _CancelAfterLineStreamResponse(_FakeStreamResponse):
    def __init__(self, lines, cancel_event, cancel_after_index):
        super().__init__(lines)
        self.cancel_event = cancel_event
        self.cancel_after_index = cancel_after_index

    def __iter__(self):
        for index, line in enumerate(self.lines):
            yield line
            if index == self.cancel_after_index:
                self.cancel_event.set()


class GenerationContinuationTests(SettingsTestCase):
    def setUp(self):
        self.original_urlopen = _m_provider_transport.strict_urlopen
        self.old_key = os.environ.get("TEST_OPENROUTER_KEY")
        self.old_hosts = os.environ.get("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS")
        self.spec = {
            "transport": "openai_compatible",
            "api_endpoint": "https://openrouter.ai/api/v1",
            "api_key_env": "TEST_OPENROUTER_KEY",
        }
        self.router = type(
            "_Router",
            (),
            {
                "route": lambda _self, _model: ModelRoute(
                    "openrouter",
                    "test/model",
                    self.spec,
                )
            },
        )()
        os.environ["TEST_OPENROUTER_KEY"] = "test-only"
        os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = "openrouter.ai"

    def tearDown(self):
        _m_provider_transport.strict_urlopen = self.original_urlopen
        if self.old_key is None:
            os.environ.pop("TEST_OPENROUTER_KEY", None)
        else:
            os.environ["TEST_OPENROUTER_KEY"] = self.old_key
        if self.old_hosts is None:
            os.environ.pop("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", None)
        else:
            os.environ["SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"] = self.old_hosts

    def test_missing_assistant_content_logs_redacted_response_diagnostics(self):
        _m_provider_transport.strict_urlopen = lambda _request, **_kwargs: _FakeResponse(
            {
                "id": "response-id",
                "object": "chat.completion",
                "model": "test/model",
                "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 0},
            },
            status=502,
        )
        with self.assertLogs("bridge.provider_transport", level="WARNING") as captured:
            with self.assertRaisesRegex(RuntimeError, "backend returned no assistant content"):
                _m_provider_transport.generate_provider_text(
                    self.router,
                    "",
                    "test",
                    [{"role": "user", "content": "Write a complete answer."}],
                    settings=dict(_m_sync_core.GENERATION_DEFAULTS),
                    app_settings=self.app_settings_builder.build(),
                )

        logs = "\\n".join(captured.output)
        self.assertIn("provider=openrouter", logs)
        self.assertIn("model=test/model", logs)
        self.assertIn("http_status=502", logs)
        self.assertIn("choice_count=1", logs)
        self.assertIn("finish_reason=stop", logs)
        self.assertIn("response_keys=", logs)
        self.assertNotIn("Write a complete answer", logs)
        self.assertNotIn("test-only", logs)

    def test_nested_data_choices_returns_assistant_content(self):
        _m_provider_transport.strict_urlopen = lambda _request, **_kwargs: _FakeResponse(
            {
                "data": {
                    "choices": [
                        {
                            "message": {"content": "OK"},
                            "finish_reason": "stop",
                        }
                    ]
                },
                "success": True,
            }
        )

        result = _m_provider_transport.generate_provider_text(
            self.router,
            "",
            "test",
            [{"role": "user", "content": "Reply OK."}],
            settings=dict(_m_sync_core.GENERATION_DEFAULTS),
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(result, "OK")

    def test_standard_top_level_choices_still_returns_assistant_content(self):
        _m_provider_transport.strict_urlopen = lambda _request, **_kwargs: _FakeResponse(
            {
                "choices": [
                    {
                        "message": {"content": "TOP"},
                        "finish_reason": "stop",
                    }
                ]
            }
        )

        result = _m_provider_transport.generate_provider_text(
            self.router,
            "",
            "test",
            [{"role": "user", "content": "Reply TOP."}],
            settings=dict(_m_sync_core.GENERATION_DEFAULTS),
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(result, "TOP")

    def test_nested_empty_choices_raises_existing_error_with_sanitized_diagnostics(self):
        _m_provider_transport.strict_urlopen = lambda _request, **_kwargs: _FakeResponse(
            {
                "data": {"choices": []},
                "success": True,
            },
            status=200,
        )

        with self.assertLogs(
            "bridge.provider_transport",
            level="WARNING",
        ) as captured:
            with self.assertRaisesRegex(
                RuntimeError,
                "backend returned no assistant content",
            ):
                _m_provider_transport.generate_provider_text(
                    self.router,
                    "",
                    "test",
                    [
                        {
                            "role": "user",
                            "content": "SENSITIVE REQUEST CONTENT",
                        }
                    ],
                    settings=dict(_m_sync_core.GENERATION_DEFAULTS),
                    app_settings=self.app_settings_builder.build(),
                )

        logs = "\n".join(captured.output)
        self.assertIn("http_status=200", logs)
        self.assertIn("choice_count=0", logs)
        self.assertIn("response_keys=['data', 'success']", logs)
        self.assertNotIn("SENSITIVE REQUEST CONTENT", logs)
        self.assertNotIn("test-only", logs)

    def test_nested_data_choices_is_used_for_non_stream_continuation(self):
        payloads = [
            {
                "choices": [
                    {
                        "message": {"content": "Part one."},
                        "finish_reason": "length",
                    }
                ]
            },
            {
                "data": {
                    "choices": [
                        {
                            "message": {"content": "Part two."},
                            "finish_reason": "stop",
                        }
                    ]
                },
                "success": True,
            },
        ]

        def fake_urlopen(_request, *, environ=None, **_kwargs):
            return _FakeResponse(payloads.pop(0))

        _m_provider_transport.strict_urlopen = fake_urlopen
        result = _m_provider_transport.generate_provider_text(
            self.router,
            "",
            "test",
            [{"role": "user", "content": "Write a complete answer."}],
            settings=dict(_m_sync_core.GENERATION_DEFAULTS),
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(result, "Part one. Part two.")
        self.assertEqual(payloads, [])

    def test_generate_text_honors_explicit_request_timeout(self):
        seen = []

        def fake_urlopen(_request, timeout, *, environ=None):
            seen.append(timeout)
            return _FakeResponse({"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]})

        _m_provider_transport.strict_urlopen = fake_urlopen
        result = _m_provider_transport.generate_provider_text(
            self.router,
            "",
            "test",
            [{"role": "user", "content": "Reply OK."}],
            settings=dict(_m_sync_core.GENERATION_DEFAULTS),
            request_timeout=30,
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(result, "OK")
        self.assertEqual(seen, [30])

    def test_empty_stream_length_retries_with_larger_non_stream_budget(self):
        self.spec["streaming"] = True
        responses = [
            _FakeStreamResponse(
                [
                    'data: {"choices":[{"delta":{"reasoning":"thinking"},"finish_reason":null}]}\n',
                    'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
                    "data: [DONE]\n",
                ]
            ),
            _FakeStreamResponse(
                [
                    'data: {"choices":[{"delta":{"content":"Recovered answer."},"finish_reason":"stop"}]}\n',
                    "data: [DONE]\n",
                ]
            ),
        ]
        requests = []

        def fake_urlopen(request, timeout, *, environ=None):
            requests.append(json.loads(request.data.decode()))
            return responses.pop(0)

        _m_provider_transport.strict_urlopen = fake_urlopen
        result = _m_provider_transport.generate_provider_text(
            self.router,
            "",
            "test",
            [{"role": "user", "content": "Write a complete answer."}],
            settings={**_m_sync_core.GENERATION_DEFAULTS, "max_tokens": 1800},
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(result, "Recovered answer.")
        self.assertEqual(len(requests), 2)
        self.assertTrue(requests[0]["stream"])
        self.assertTrue(requests[1]["stream"])
        self.assertEqual(requests[1]["max_tokens"], 4096)

    def test_streaming_length_continuation_stays_streaming(self):
        self.spec["streaming"] = True
        responses = [
            _FakeStreamResponse(
                [
                    'data: {"choices":[{"delta":{"content":"Part one."},"finish_reason":null}]}\n',
                    'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
                    "data: [DONE]\n",
                ]
            ),
            _FakeStreamResponse(
                [
                    'data: {"choices":[{"delta":{"content":"Part two."},"finish_reason":"stop"}]}\n',
                    "data: [DONE]\n",
                ]
            ),
        ]
        requests = []

        def fake_urlopen(request, timeout, *, environ=None):
            requests.append(json.loads(request.data.decode()))
            return responses.pop(0)

        _m_provider_transport.strict_urlopen = fake_urlopen
        result = _m_provider_transport.generate_provider_text(
            self.router,
            "",
            "test",
            [{"role": "user", "content": "Write a complete answer."}],
            settings={**_m_sync_core.GENERATION_DEFAULTS, "max_tokens": 1800},
            app_settings=self.app_settings_builder.build(),
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

        def fake_urlopen(request, timeout, *, environ=None):
            requests.append(json.loads(request.data.decode()))
            return _FakeResponse(payloads.pop(0))

        _m_provider_transport.strict_urlopen = fake_urlopen
        result = _m_provider_transport.generate_provider_text(
            self.router,
            "",
            "test",
            [{"role": "user", "content": "Write a complete answer."}],
            settings=dict(_m_sync_core.GENERATION_DEFAULTS),
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(result, "Part one. Part two.")
        self.assertEqual(len(requests), 2)
        self.assertEqual([item["role"] for item in requests[1]["messages"][-2:]], ["assistant", "user"])
        self.assertEqual(requests[1]["messages"][-2]["content"], "Part one.")
        self.assertIn("exact ending", requests[1]["messages"][-1]["content"])

    def test_failed_automatic_continuation_keeps_first_segment(self):
        calls = 0

        def fake_urlopen(_request, timeout, *, environ=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                return _FakeResponse(
                    {"choices": [{"message": {"content": "Partial but usable."}, "finish_reason": "length"}]}
                )
            raise RuntimeError("temporary provider failure")

        _m_provider_transport.strict_urlopen = fake_urlopen
        result = _m_provider_transport.generate_provider_text(
            self.router,
            "",
            "test",
            [{"role": "user", "content": "Write a complete answer."}],
            settings=dict(_m_sync_core.GENERATION_DEFAULTS),
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(result, "Partial but usable.")
        self.assertEqual(calls, 2)

    def test_streaming_length_continuation_callback_is_cumulative(self):
        self.spec["streaming"] = True
        responses = [
            _FakeStreamResponse(
                [
                    'data: {"choices":[{"delta":{"content":"Part one."},"finish_reason":null}]}\n',
                    'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
                    "data: [DONE]\n",
                ]
            ),
            _FakeStreamResponse(
                [
                    'data: {"choices":[{"delta":{"content":"Part two."},"finish_reason":"stop"}]}\n',
                    "data: [DONE]\n",
                ]
            ),
        ]
        callbacks = []

        def fake_urlopen(_request, timeout, *, environ=None):
            return responses.pop(0)

        _m_provider_transport.strict_urlopen = fake_urlopen
        result = _m_provider_transport.generate_provider_text(
            self.router,
            "",
            "test",
            [{"role": "user", "content": "Write a complete answer."}],
            settings={**_m_sync_core.GENERATION_DEFAULTS, "max_tokens": 1800},
            stream_callback=callbacks.append,
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(result, "Part one. Part two.")
        self.assertEqual(callbacks[0], "Part one.")
        self.assertEqual(callbacks[-1], "Part one. Part two.")

    def test_streaming_length_cancellation_after_first_segment_skips_continuation(self):
        self.spec["streaming"] = True
        cancel_event = threading.Event()
        calls = 0

        def fake_urlopen(_request, timeout, *, environ=None):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise AssertionError("continuation request must not start after cancellation")
            return _FakeStreamResponse(
                [
                    'data: {"choices":[{"delta":{"content":"Part one."},"finish_reason":null}]}\n',
                    'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
                    "data: [DONE]\n",
                ]
            )

        def callback(partial):
            if partial == "Part one.":
                cancel_event.set()

        _m_provider_transport.strict_urlopen = fake_urlopen
        result = _m_provider_transport.generate_provider_text(
            self.router,
            "",
            "test",
            [{"role": "user", "content": "Write a complete answer."}],
            settings={**_m_sync_core.GENERATION_DEFAULTS, "max_tokens": 1800},
            stream_callback=callback,
            cancel_event=cancel_event,
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(result, "Part one.")
        self.assertEqual(calls, 1)

    def test_streaming_cancellation_during_continuation_preserves_visible_text(self):
        self.spec["streaming"] = True
        cancel_event = threading.Event()
        responses = [
            _FakeStreamResponse(
                [
                    'data: {"choices":[{"delta":{"content":"Part one."},"finish_reason":null}]}\n',
                    'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
                    "data: [DONE]\n",
                ]
            ),
            _CancelAfterLineStreamResponse(
                [
                    'data: {"choices":[{"delta":{"content":"Part two."},"finish_reason":null}]}\n',
                    'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
                    "data: [DONE]\n",
                ],
                cancel_event,
                1,
            ),
        ]
        calls = 0
        callbacks = []

        def fake_urlopen(_request, timeout, *, environ=None):
            nonlocal calls
            calls += 1
            if not responses:
                raise AssertionError("no third request expected after cancellation")
            return responses.pop(0)

        _m_provider_transport.strict_urlopen = fake_urlopen
        result = _m_provider_transport.generate_provider_text(
            self.router,
            "",
            "test",
            [{"role": "user", "content": "Write a complete answer."}],
            settings={**_m_sync_core.GENERATION_DEFAULTS, "max_tokens": 1800},
            stream_callback=callbacks.append,
            cancel_event=cancel_event,
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(result, "Part one. Part two.")
        self.assertEqual(calls, 2)
        self.assertEqual(callbacks[-1], "Part one. Part two.")

    def test_empty_stream_length_recovery_forwards_stream_callback(self):
        self.spec["streaming"] = True
        responses = [
            _FakeStreamResponse(
                [
                    'data: {"choices":[{"delta":{"reasoning":"thinking"},"finish_reason":null}]}\n',
                    'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
                    "data: [DONE]\n",
                ]
            ),
            _FakeStreamResponse(
                [
                    'data: {"choices":[{"delta":{"content":"Recovered answer."},"finish_reason":"stop"}]}\n',
                    "data: [DONE]\n",
                ]
            ),
        ]
        callbacks = []

        def fake_urlopen(_request, timeout, *, environ=None):
            return responses.pop(0)

        _m_provider_transport.strict_urlopen = fake_urlopen
        result = _m_provider_transport.generate_provider_text(
            self.router,
            "",
            "test",
            [{"role": "user", "content": "Write a complete answer."}],
            settings={**_m_sync_core.GENERATION_DEFAULTS, "max_tokens": 1800},
            stream_callback=callbacks.append,
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(result, "Recovered answer.")
        self.assertEqual(callbacks[-1], "Recovered answer.")

    def test_empty_stream_length_cancelled_before_recovery_opens_no_retry(self):
        self.spec["streaming"] = True
        cancel_event = threading.Event()
        calls = 0

        def fake_urlopen(_request, timeout, *, environ=None):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise AssertionError("recovery request must not start after cancellation")
            return _CancelAfterLineStreamResponse(
                [
                    'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
                    "data: [DONE]\n",
                ],
                cancel_event,
                0,
            )

        _m_provider_transport.strict_urlopen = fake_urlopen
        with self.assertRaisesRegex(RuntimeError, "returned no visible content"):
            _m_provider_transport.generate_provider_text(
                self.router,
                "",
                "test",
                [{"role": "user", "content": "Write a complete answer."}],
                settings={**_m_sync_core.GENERATION_DEFAULTS, "max_tokens": 1800},
                cancel_event=cancel_event,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(calls, 1)

    def test_streaming_visible_continuation_is_bounded_to_three_requests(self):
        self.spec["streaming"] = True
        segments = ["One.", "Two.", "Three.", "Four."]
        requests = []
        responses = [
            _FakeStreamResponse(
                [
                    f"""data: {{"choices":[{{"delta":{{"content":"{segment}"}},"finish_reason":null}}]}}\n""",
                    'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
                    "data: [DONE]\n",
                ]
            )
            for segment in segments
        ]

        def fake_urlopen(request, timeout, *, environ=None):
            requests.append(json.loads(request.data.decode()))
            if not responses:
                raise AssertionError("visible continuation exceeded three requests")
            return responses.pop(0)

        _m_provider_transport.strict_urlopen = fake_urlopen
        result = _m_provider_transport.generate_provider_text(
            self.router,
            "",
            "test",
            [{"role": "user", "content": "Write a complete answer."}],
            settings={**_m_sync_core.GENERATION_DEFAULTS, "max_tokens": 1800},
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(result, "One. Two. Three. Four.")
        self.assertEqual(len(requests), 4)
        self.assertTrue(all(request["stream"] for request in requests))
        self.assertEqual(requests[1]["messages"][-2]["content"], "One.")
        self.assertEqual(requests[2]["messages"][-2]["content"], "Two.")
        self.assertEqual(requests[3]["messages"][-2]["content"], "Three.")
        for request in requests[1:]:
            self.assertIn("exact ending", request["messages"][-1]["content"])

    def test_auto_language_render_returns_original_without_backend_call(self):
        provider = ProviderPort(
            generate_backend=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("auto must not render"))
        )
        result = _m_generation.render_response_language(
            "",
            "model",
            "original",
            "auto",
            "session",
            {},
            provider_port=provider,
        )
        self.assertEqual(result, "original")

    def test_fixed_language_render_uses_minimal_indonesian_rewrite(self):
        captured = {}

        def fake_generate(api_key, model, messages, session_id="telegram", settings=None, **_kwargs):
            captured.update({"messages": messages, "session_id": session_id, "settings": settings})
            return "hasil Indonesia"

        provider = ProviderPort(generate_backend=fake_generate)
        result = _m_generation.render_response_language(
            "",
            "model",
            "English source",
            "id",
            "telegram:chat:session",
            dict(_m_sync_core.GENERATION_DEFAULTS),
            provider_port=provider,
        )
        self.assertEqual(result, "hasil Indonesia")
        self.assertEqual(captured["session_id"], "telegram:chat:session:language-render")
        self.assertIn("Bahasa Indonesia (id)", captured["messages"][0]["content"])
        self.assertEqual(captured["messages"][1]["content"], "<source_text>\nEnglish source\n</source_text>")
        self.assertEqual(captured["settings"]["temperature"], 0.2)
        self.assertEqual(captured["settings"]["reasoning_budget"], 0)


if __name__ == "__main__":
    unittest.main()

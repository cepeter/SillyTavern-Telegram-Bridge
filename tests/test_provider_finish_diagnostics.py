"""Transport completion metadata is factual, allowlisted and independent of usage."""

import logging

import pytest
from test_memory_json_transport import FakeResponse, routed_provider


@pytest.mark.parametrize(
    "reason,want,cap",
    [("length", "length", True), ("stop", "stop", False), ("PRIVATE_CANARY", "unknown", None), (None, None, None)],
)
def test_actual_finish_metadata_only(tmp_path, monkeypatch, caplog, reason, want, cap):
    choice = {"message": {"content": "{}"}}
    if reason is not None:
        choice["finish_reason"] = reason
    port, sent = routed_provider(tmp_path, monkeypatch, [{"choices": [choice], "usage": {"completion_tokens": 1000}}])
    caplog.set_level(logging.INFO, logger="bridge.events")
    assert (
        port.generate(
            "",
            "openrouter::test/model",
            [{"role": "user", "content": "PRIVATE_SOURCE"}],
            session_id="s",
            settings={"max_tokens": 1000, "json_once": True},
            force_non_stream=True,
        )
        == "{}"
    )
    records = [
        r.diagnostic_fields
        for r in caplog.records
        if getattr(r, "diagnostic_fields", {}).get("event") == "provider.output_finished"
    ]
    assert records, "transport must report available completion metadata"
    fields = records[0]
    assert fields.get("finish_reason") == want
    assert fields.get("output_cap_reached") is cap
    assert len(sent) == 1
    assert "PRIVATE" not in repr(fields)


@pytest.mark.parametrize(
    "reason,want,cap",
    [("length", "length", True), ("stop", "stop", False), ({"PRIVATE_CANARY": 1}, "unknown", None), (None, None, None)],
)
def test_stream_finish_metadata_remains_private(reason, want, cap, caplog):
    import io
    import json

    from bridge.provider_streaming import read_openai_stream_segment

    response = io.BytesIO(
        (
            "data: "
            + json.dumps({"choices": [{"delta": {"content": "{}"}, "finish_reason": reason}]})
            + "\n\ndata: [DONE]\n"
        ).encode()
    )
    caplog.set_level(logging.INFO, logger="bridge.events")
    content, _, cancelled = read_openai_stream_segment(response, prefix="", stream_callback=None, cancel_event=None)
    assert content == "{}" and not cancelled
    fields = next(
        r.diagnostic_fields
        for r in caplog.records
        if getattr(r, "diagnostic_fields", {}).get("event") == "provider.output_finished"
    )
    assert fields.get("finish_reason") == want
    assert fields.get("output_cap_reached") is cap
    assert "PRIVATE" not in repr(fields)


@pytest.mark.parametrize(
    "reason,want,cap",
    [
        ("max_output_tokens", "max_output_tokens", True),
        ("content_filter", "content_filter", False),
        ("PRIVATE_CANARY", "unknown", None),
        (None, None, None),
    ],
)
def test_responses_api_incomplete_details_are_actual_metadata(reason, want, cap, caplog):
    import json

    from bridge.provider_transport import _opencode_responses_text

    payload = {"output_text": "{}", "status": "incomplete", "usage": {"output_tokens": 1000}}
    if reason is not None:
        payload["incomplete_details"] = {"reason": reason}
    caplog.set_level(logging.INFO, logger="bridge.events")
    assert _opencode_responses_text(json.dumps(payload)) == "{}"
    fields = [
        r.diagnostic_fields
        for r in caplog.records
        if getattr(r, "diagnostic_fields", {}).get("event") == "provider.output_finished"
    ]
    if reason is None:
        assert fields == []
    else:
        assert fields[0].get("finish_reason") == want
        assert fields[0].get("output_cap_reached") is cap
    assert "PRIVATE" not in repr(fields)


@pytest.mark.parametrize("reason", ["PRIVATE_FINISH_REASON", {"PRIVATE_FINISH_REASON": 1}, ["PRIVATE_FINISH_REASON"]])
def test_missing_content_warning_allowlists_response_keys_and_finish_reason(tmp_path, monkeypatch, caplog, reason):
    payload = {
        "id": "PRIVATE_RESPONSE_ID",
        "object": "PRIVATE_OBJECT",
        "model": "PRIVATE_RESPONSE_MODEL",
        "choices": [
            {"message": {"content": "", "reasoning": "PRIVATE_CONTENT"}, "finish_reason": reason},
            {"message": {"content": "PRIVATE_OTHER_CHOICE"}},
        ],
        "usage": {"prompt_tokens": 10, "PRIVATE_USAGE_KEY": "PRIVATE_USAGE_VALUE"},
        "data": {"PRIVATE_NESTED_KEY": "PRIVATE_NESTED_VALUE"},
        "success": False,
        "PRIVATE_RESPONSE_KEY\ncredential=PRIVATE_CREDENTIAL": "PRIVATE_RESPONSE_VALUE",
        "PRIVATE_" + "x" * 1000: "PRIVATE_LARGE_KEY_VALUE",
    }
    port, sent = routed_provider(tmp_path, monkeypatch, [payload])
    caplog.set_level(logging.INFO, logger="bridge.events")
    with pytest.raises(RuntimeError, match=r"^backend returned no assistant content$"):
        port.generate(
            "",
            "openrouter::test/model",
            [{"role": "user", "content": "PRIVATE_REQUEST"}],
            settings={"json_once": True},
            force_non_stream=True,
        )
    assert len(sent) == 1
    assert "PRIVATE" not in caplog.text
    assert "synthetic-key" not in caplog.text
    assert "PRIVATE" not in repr([getattr(record, "diagnostic_fields", {}) for record in caplog.records])
    warnings = [record.getMessage() for record in caplog.records if record.name == "bridge.provider_transport"]
    assert len(warnings) == 1
    message = warnings[0]
    assert "provider=openrouter model=test/model http_status=200 choice_count=2 finish_reason=unknown" in message
    assert "response_keys=['choices', 'data', 'id', 'model', 'object', 'success', 'usage']" in message


@pytest.mark.parametrize(
    "status,want",
    [
        (200, "200"),
        (599, "599"),
        (99, "unknown"),
        (600, "unknown"),
        (True, "unknown"),
        (200.0, "unknown"),
        ("PRIVATE_HTTP_STATUS", "unknown"),
        (None, "unknown"),
    ],
)
def test_missing_content_warning_bounds_http_status(tmp_path, monkeypatch, caplog, status, want):
    monkeypatch.setattr(FakeResponse, "status", status)
    port, sent = routed_provider(tmp_path, monkeypatch, [{"choices": []}])
    with pytest.raises(RuntimeError, match=r"^backend returned no assistant content$"):
        port.generate("", "test/model", [{"role": "user", "content": "PRIVATE_REQUEST"}])
    assert len(sent) == 1
    assert "PRIVATE" not in caplog.text
    assert f"http_status={want} choice_count=0" in caplog.text


def test_missing_content_warning_uses_getcode_when_status_is_absent(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(FakeResponse, "status", None)
    monkeypatch.setattr(FakeResponse, "getcode", lambda _self: 503, raising=False)
    port, sent = routed_provider(tmp_path, monkeypatch, [{"choices": []}])
    with pytest.raises(RuntimeError, match=r"^backend returned no assistant content$"):
        port.generate("", "test/model", [{"role": "user", "content": "PRIVATE_REQUEST"}])
    assert len(sent) == 1
    assert "PRIVATE" not in caplog.text
    assert "http_status=503 choice_count=0" in caplog.text


def test_missing_content_warning_preserves_error_when_status_lookup_fails(tmp_path, monkeypatch, caplog):
    def getcode(_self):
        raise RuntimeError("PRIVATE_EXCEPTION_TEXT")

    monkeypatch.setattr(FakeResponse, "status", None)
    monkeypatch.setattr(FakeResponse, "getcode", getcode, raising=False)
    port, sent = routed_provider(tmp_path, monkeypatch, [{"choices": []}])
    with pytest.raises(RuntimeError, match=r"^backend returned no assistant content$"):
        port.generate("", "test/model", [{"role": "user", "content": "PRIVATE_REQUEST"}])
    assert len(sent) == 1
    assert "PRIVATE" not in caplog.text
    assert "http_status=unknown choice_count=0" in caplog.text


def test_missing_content_warning_rejects_unsafe_provider_and_model_metadata(tmp_path, monkeypatch, caplog):
    port, sent = routed_provider(
        tmp_path,
        monkeypatch,
        [{"choices": []}],
        provider="PRIVATE_PROVIDER\ninjection",
        model_id="PRIVATE_MODEL " * 100,
    )
    with pytest.raises(RuntimeError, match=r"^backend returned no assistant content$"):
        port.generate("", "test/model", [{"role": "user", "content": "PRIVATE_REQUEST"}])
    assert len(sent) == 1
    assert "PRIVATE" not in caplog.text
    assert "provider=unknown model=unknown" in caplog.text

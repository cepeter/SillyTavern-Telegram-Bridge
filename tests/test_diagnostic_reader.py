"""Exercise on-disk timelines, not mocked reader internals."""

import json

from bridge.diagnostic_events import scope_reference


def record(name="provider.finish", *, chat="11", session="a", **fields):
    return {
        "schema": 1,
        "timestamp": "2026-10-08T06:00:00.000Z",
        "level": "INFO",
        "event": name,
        "chat_ref": scope_reference("chat", chat),
        "session_ref": scope_reference("session", session),
        "request_id": "tg-1",
        **fields,
    }


def write(path, *items):
    path.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")


def test_reader_filters_ownership_before_trace_and_usage_aggregation(tmp_path, monkeypatch):
    import bridge.diagnostic_events as diagnostics
    from bridge.diagnostic_reader import read_events

    # This synthetic key yields a valid session HMAC containing the digits 777.
    monkeypatch.setattr(diagnostics, "_IDENTITY_KEY", b"synthetic-reader-key-208")
    path = tmp_path / "runtime.log"
    accepted = record(input_tokens=10, output_tokens=2, usage_reported=True, usage_complete=True, purpose="story")
    write(
        path,
        accepted,
        record(chat="22", input_tokens=999, purpose="private-other-model"),
        record(session="b", input_tokens=777),
        record("runtime.log", message="private transcript", traceback=[{"private": "value"}]),
    )
    result = read_events(path, chat_id="11", session_id="a")
    assert result["events"] == [{key: value for key, value in accepted.items() if key != "schema"}]
    assert result["scope"] == {"chat_ref": "8834c5a9818a893d6ddc6d47", "session_ref": "807773b625eb82d2361acdea"}
    expected_usage = {
        "attempts": 1,
        "completed_attempts": 1,
        "unfinished_attempts": 0,
        "reported_attempts": 1,
        "complete": True,
        "input_tokens": 10,
        "output_tokens": 2,
        "total_tokens": None,
        "cached_tokens": None,
        "reasoning_tokens": None,
    }
    assert result["usage"] == expected_usage
    assert result["usage_by_purpose"] == [{"purpose": "story", **expected_usage}]
    assert result["traces"] == [
        {
            "request_id": "tg-1",
            "events": 1,
            "failures": 0,
            "fallbacks": 0,
            "last_event": "provider.finish",
            "last_status": "",
            "timestamp": "2026-10-08T06:00:00.000Z",
            "unfinished_attempts": 0,
            "outcome": "",
        }
    ]
    assert result["skipped_records"] == 3
    assert "private" not in json.dumps(result)


def test_reader_revalidates_fields_and_does_not_export_legacy_content(tmp_path):
    from bridge.diagnostic_reader import read_events

    path = tmp_path / "runtime.log"
    write(path, record(prompt="secret story", response="secret reply", error="secret error", input_tokens=-1))
    with path.open("a") as stream:
        stream.write("legacy private payload\n{broken\n" + "x" * 9000 + "\n")
    result = read_events(path, chat_id="11", session_id="a")
    assert len(result["events"]) == 1
    assert "input_tokens" not in result["events"][0]
    assert "secret" not in json.dumps(result)
    assert result["skipped_records"] >= 3
    assert result["usage"]["complete"] is False
    assert result["usage"]["input_tokens"] is None


def test_retained_rotation_and_incident_filters_preserve_trace_identity(tmp_path):
    from bridge.diagnostic_reader import read_events

    path = tmp_path / "runtime.log"
    write(path.with_name("runtime.log.1"), record("provider.start", purpose="summary", attempt=1))
    write(
        path,
        record("provider.fallback", purpose="summary", status="selected", reason="timeout"),
        record(purpose="summary", status="succeeded", input_tokens=8, usage_reported=True, usage_complete=False),
    )
    result = read_events(path, chat_id="11", session_id="a", purpose="summary", request_id="tg-1")
    assert [item["event"] for item in result["events"]] == ["provider.start", "provider.fallback", "provider.finish"]
    assert result["traces"][0]["request_id"] == "tg-1"
    assert result["traces"][0]["fallbacks"] == 1
    assert result["usage"]["complete"] is False
    assert result["usage"]["input_tokens"] == 8
    assert read_events(path, chat_id="11", session_id="a", request_id="tg-other")["events"] == []


def test_limits_apply_to_bytes_and_events_even_when_no_scope_matches(tmp_path):
    from bridge.diagnostic_reader import read_events

    path = tmp_path / "runtime.log"
    write(path, *(record(message_id=n) for n in range(1000)))
    result = read_events(path, chat_id="11", session_id="a", limit=3, max_bytes=4096)
    assert len(result["events"]) == 3
    assert result["events"][-1]["message_id"] == 999
    assert result["truncated"] is True
    assert result["bytes_scanned"] <= 4096
    other = read_events(path, chat_id="absent", session_id="a", max_bytes=4096)
    assert other["events"] == []
    assert other["bytes_scanned"] <= 4096
    assert other["truncated"] is True


def test_missing_symlink_and_incomplete_tail_are_not_followed_or_parsed(tmp_path):
    from bridge.diagnostic_reader import read_events

    path = tmp_path / "runtime.log"
    assert read_events(path, chat_id="11", session_id="a")["available"] is False
    target = tmp_path / "private.log"
    write(target, record())
    path.symlink_to(target)
    assert read_events(path, chat_id="11", session_id="a")["events"] == []
    path.unlink()
    path.write_text(json.dumps(record()), encoding="utf-8")
    assert read_events(path, chat_id="11", session_id="a")["events"] == []


def test_failed_and_unreported_attempts_never_become_zero_cost_success(tmp_path):
    from bridge.diagnostic_reader import read_events

    path = tmp_path / "runtime.log"
    write(
        path,
        record(status="failed", input_tokens=5, usage_reported=True, usage_complete=False, purpose="story"),
        record(
            status="succeeded",
            input_tokens=7,
            output_tokens=2,
            usage_reported=True,
            usage_complete=True,
            purpose="story",
        ),
        record(status="failed", usage_reported=False, usage_complete=False, purpose="summary"),
    )
    result = read_events(path, chat_id="11", session_id="a")
    assert result["usage"]["attempts"] == 3
    assert result["usage"]["reported_attempts"] == 2
    assert result["usage"]["input_tokens"] == 12
    assert result["usage"]["complete"] is False
    assert {item["purpose"] for item in result["usage_by_purpose"]} == {"story", "summary"}
    assert result["traces"][0]["failures"] == 2


def test_unfinished_attempt_is_unknown_even_beside_a_successful_attempt(tmp_path):
    from bridge.diagnostic_reader import read_events

    path = tmp_path / "runtime.log"
    write(
        path,
        record("provider.start", call_id="call-a", attempt=1, purpose="story"),
        record(
            call_id="call-a",
            attempt=1,
            status="succeeded",
            input_tokens=8,
            output_tokens=2,
            usage_reported=True,
            usage_complete=True,
            purpose="story",
        ),
        record("provider.start", call_id="call-a", attempt=2, purpose="story"),
        record("provider.start", chat="22", call_id="other", attempt=1, purpose="story"),
    )
    result = read_events(path, chat_id="11", session_id="a")
    assert result["usage"]["attempts"] == 2
    assert result["usage"]["completed_attempts"] == 1
    assert result["usage"]["unfinished_attempts"] == 1
    assert result["usage"]["complete"] is False
    assert result["usage"]["input_tokens"] == 8
    assert result["traces"][0]["unfinished_attempts"] == 1
    assert result["traces"][0]["outcome"] == "unknown"
    assert result["usage_by_purpose"][0]["unfinished_attempts"] == 1


def test_start_only_has_unknown_usage_not_a_zero_cost_success(tmp_path):
    from bridge.diagnostic_reader import read_events

    path = tmp_path / "runtime.log"
    write(path, record("provider.start", call_id="call-only", attempt=1, purpose="summary"))
    result = read_events(path, chat_id="11", session_id="a")
    assert result["usage"]["attempts"] == 1
    assert result["usage"]["input_tokens"] is None
    assert result["usage"]["complete"] is False
    assert result["usage_by_purpose"][0]["purpose"] == "summary"


def test_reader_rejects_special_files_hardlinks_and_foreign_owners(tmp_path, monkeypatch):
    import os
    import stat
    from types import SimpleNamespace

    from bridge.diagnostic_reader import read_events

    path = tmp_path / "runtime.log"
    os.mkfifo(path)
    assert read_events(path, chat_id="11", session_id="a")["events"] == []
    path.unlink()
    write(path, record())
    other = tmp_path / "linked.log"
    os.link(path, other)
    assert read_events(path, chat_id="11", session_id="a")["events"] == []
    other.unlink()
    monkeypatch.setattr(
        os, "fstat", lambda _fd: SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_nlink=1, st_uid=os.getuid() + 1)
    )
    assert read_events(path, chat_id="11", session_id="a")["events"] == []


def test_reader_rejects_invalid_shapes_and_timezone_free_timestamps(tmp_path):
    from bridge.diagnostic_reader import read_events

    path = tmp_path / "runtime.log"
    invalid = [
        [],
        {},
        {**record(), "event": []},
        {**record(), "event": "Bad event"},
        {**record(), "level": []},
        {**record(), "level": "INVALID"},
        {**record(), "timestamp": "x" * 41},
        {**record(), "timestamp": "2026-10-08T06:00:00"},
        {**record(), "timestamp": "not-a-date"},
    ]
    write(path, *invalid, record())
    result = read_events(path, chat_id="11", session_id="a")
    assert len(result["events"]) == 1
    assert result["skipped_records"] == len(invalid)
    assert read_events(path, chat_id="11", session_id="a", level="ERROR")["events"] == []

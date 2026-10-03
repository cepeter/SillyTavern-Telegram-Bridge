"""Reported usage accounting, explicit scope and private ledger regressions."""

from __future__ import annotations

import json
import time
from functools import partial

import pytest
from miniapp_test_support import identity, make_services

from bridge.provider_port import ProviderPort
from bridge.sqlite_store import write_transaction


def test_reported_usage_normalizes_wrappers_and_subsets():
    from bridge.token_usage_values import UsageCapture

    meter = UsageCapture()
    meter.observe(
        {
            "data": {
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                    "total_tokens": 150,
                    "prompt_tokens_details": {"cached_tokens": 80},
                    "completion_tokens_details": {"reasoning_tokens": 10},
                }
            }
        }
    )
    result = meter.snapshot()
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (120, 30, 150)
    assert (result.cached_tokens, result.reasoning_tokens) == (80, 10)


def test_anthropic_cumulative_snapshots_do_not_double_count_cache():
    from bridge.token_usage_values import UsageCapture

    meter = UsageCapture(flavor="anthropic")
    meter.observe(
        {
            "message": {
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 30,
                }
            }
        }
    )
    meter.observe({"usage": {"output_tokens": 8, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 30}})
    result = meter.snapshot()
    assert (result.input_tokens, result.output_tokens, result.total_tokens, result.cached_tokens) == (60, 8, 68, 20)


@pytest.mark.parametrize("invalid", [-1, 2.5, "12", True, None, {}, 2**62])
def test_invalid_token_metadata_is_unknown_not_zero(invalid):
    from bridge.token_usage_values import UsageCapture

    meter = UsageCapture()
    meter.observe({"usage": {"prompt_tokens": invalid, "completion_tokens": invalid}})
    assert meter.snapshot().total_tokens is None
    assert meter.snapshot().input_tokens is None


def test_responses_partial_and_zero_usage_are_distinct():
    from bridge.token_usage_values import UsageCapture

    meter = UsageCapture()
    meter.observe(
        {
            "response": {
                "usage": {"input_tokens": 0, "output_tokens": 0, "output_tokens_details": {"reasoning_tokens": 0}}
            }
        }
    )
    assert meter.snapshot().total_tokens == 0
    assert meter.snapshot().complete
    assert not UsageCapture().snapshot().complete


def test_scoped_port_records_all_responses_without_changing_text_or_scope():
    from bridge.token_usage_values import TokenUsage

    seen = []

    def generate(*args, **kwargs):
        kwargs["usage_callback"](TokenUsage(10, 5, 15))
        kwargs["usage_callback"](TokenUsage(20, 7, 27))
        return "Actual narrative"

    root = ProviderPort(generate, usage_recorder=seen.append)
    scoped = root.for_usage("123", "s:with-colons", "story")
    assert scoped.generate("secret", "provider::model", []) == "Actual narrative"
    assert len(seen) == 1
    assert seen[0].scope.chat_id == "123"
    assert seen[0].scope.session_id == "s:with-colons"
    assert sum(u.total_tokens for u in seen[0].readings) == 42
    assert root.usage_scope is None
    assert scoped.for_purpose("humanizer").usage_scope.purpose == "humanizer"
    assert scoped.usage_scope.purpose == "story"
    assert "secret" not in repr(seen)


def test_failed_generation_records_unknown_and_normalizes_provider_timeout(caplog):
    from bridge.provider_errors import ProviderRequestError

    seen = []

    def fail(*args, **kwargs):
        raise TimeoutError("PRIVATE failure")

    port = ProviderPort(fail, usage_recorder=seen.append).for_usage("123", "s", "choices")
    with pytest.raises(ProviderRequestError, match="timed out") as raised:
        port.generate("PRIVATE key", "p::model", [])
    assert raised.value.category == "timeout"
    assert len(seen) == 1 and seen[0].status == "failed"
    assert seen[0].readings == ()
    assert "PRIVATE" not in str(raised.value)
    assert "PRIVATE" not in caplog.text


def test_recorder_failure_does_not_break_generation_or_leak(caplog):
    def fail(event):
        raise ValueError("PRIVATE database path")

    port = ProviderPort(lambda *a, **k: "response", usage_recorder=fail).for_usage("123", "s", "story")
    assert port.generate("key", "p::m", []) == "response"
    assert "Token usage could not be recorded" in caplog.text
    assert "PRIVATE" not in caplog.text


def test_unscoped_calls_are_not_attributed_to_any_user():
    seen = []

    def generate(*args, **kwargs):
        assert "usage_callback" not in kwargs
        return "admin task"

    assert ProviderPort(generate, usage_recorder=seen.append).generate("", "m", []) == "admin task"
    assert seen == []


def test_private_usage_api_isolates_chat_and_session_and_preserves_unknown(tmp_path):
    from bridge.miniapp_context import current_session
    from bridge.miniapp_usage import usage_summary
    from bridge.token_usage import record_usage
    from bridge.token_usage_values import TokenUsage, UsageEvent, UsageScope

    services = make_services(tmp_path)
    who = identity()
    session = current_session(services, who, {})["session"]
    other = identity("67890")
    other_session = current_session(services, other, {})["session"]
    for user, sid, model, values in (
        (who, session["session_id"], "p::my-model", (TokenUsage(10, 5, 15),)),
        (who, session["session_id"], "p::unknown", ()),
        (other, other_session["session_id"], "p::PRIVATE-model", (TokenUsage(1000, 1000, 2000),)),
    ):
        record_usage(
            UsageEvent(UsageScope(user.chat_id, sid, "story"), model, values, 15, "succeeded"),
            db_factory=services.db_factory,
        )
    result = usage_summary(services, who, {"period": "7d", "scope": "all", "chat_id": other.chat_id})
    assert result["totals"]["total_tokens"] == 15
    assert result["totals"]["calls"] == 2
    assert result["totals"]["reported_calls"] == 1
    assert "PRIVATE" not in json.dumps(result)
    assert result["retention_days"] == 90
    assert len(result["daily"]) <= 8
    with services.db_factory() as db:
        with write_transaction(db):
            db.execute("DELETE FROM sessions WHERE chat_id=? AND session_id=?", (who.chat_id, session["session_id"]))
        assert db.execute("SELECT count(*) FROM token_usage_events WHERE chat_id=?", (who.chat_id,)).fetchone()[0] == 0


@pytest.mark.parametrize("values", [{"period": "forever"}, {"scope": "everyone"}, {"period": "999d"}])
def test_usage_filters_fail_closed(tmp_path, values):
    from bridge.miniapp_usage import usage_summary

    with pytest.raises(ValueError):
        usage_summary(make_services(tmp_path), identity(), values)


def test_repository_write_requires_transaction_and_retention_is_bounded(tmp_path):
    from bridge.miniapp_context import current_session
    from bridge.token_usage_repository import insert_event, prune_events

    services = make_services(tmp_path)
    who = identity()
    sid = current_session(services, who, {})["session"]["session_id"]
    db = services.db_factory()
    try:
        with pytest.raises(RuntimeError):
            prune_events(db, time.time())
        with write_transaction(db):
            insert_event(
                db,
                chat_id=who.chat_id,
                session_id=sid,
                model="p::m",
                purpose="story",
                created_at=1,
                status="succeeded",
                elapsed_ms=10,
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                cached_tokens=None,
                reasoning_tokens=None,
                reported=True,
                complete=True,
            )
        with write_transaction(db):
            prune_events(db, time.time() - 90 * 86400)
        assert db.execute("SELECT count(*) FROM token_usage_events").fetchone()[0] == 0
    finally:
        db.close()


def test_upgrade_preserves_existing_sessions_and_adds_usage_ledger(tmp_path):
    import sqlite3

    from bridge.migrations import run_migrations
    from bridge.schema import SCHEMA_MIGRATIONS

    db = sqlite3.connect(tmp_path / "upgrade.sqlite3")
    try:
        run_migrations(db, SCHEMA_MIGRATIONS[:2])
        db.execute(
            "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,"
            "created_at,updated_at) "
            "VALUES('123','old','Keep me','card.png','p::m','','',1,1)"
        )
        db.commit()
        run_migrations(db, SCHEMA_MIGRATIONS)
        assert db.execute("SELECT title FROM sessions WHERE chat_id='123'").fetchone()[0] == "Keep me"
        assert db.execute("SELECT count(*) FROM token_usage_events").fetchone()[0] == 0
        assert db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall() == [
            (1,),
            (2,),
            (3,),
            (4,),
            (5,),
            (6,),
            (7,),
            (8,),
            (9,),
        ]
        run_migrations(db, SCHEMA_MIGRATIONS)
    finally:
        db.close()


def test_usage_tracks_scoped_story_and_humanizer_separately(tmp_path, monkeypatch):
    from bridge.generation import render_session_response
    from bridge.miniapp_context import current_session
    from bridge.miniapp_usage import usage_summary
    from bridge.token_usage import record_usage
    from bridge.token_usage_values import TokenUsage

    services = make_services(tmp_path)
    who = identity()
    session = current_session(services, who, {})["session"]
    session["humanizer"] = "on"

    def generate(*args, **kwargs):
        kwargs["usage_callback"](TokenUsage(12, 4, 16))
        return "A plain sentence."

    port = ProviderPort(generate, usage_recorder=partial(record_usage, db_factory=services.db_factory))
    assert (
        render_session_response("", session, "A useful sentence.", who.chat_id, {}, provider_port=port)
        == "*A plain sentence.*"
    )
    result = usage_summary(services, who, {})
    assert result["totals"]["total_tokens"] == 16
    assert result["purposes"][0]["purpose"] == "humanizer"


def test_continuity_summary_records_utility_tokens_for_its_session(tmp_path):
    from bridge.memory import generate_session_summary
    from bridge.miniapp_context import current_session
    from bridge.miniapp_usage import usage_summary
    from bridge.token_usage import record_usage
    from bridge.token_usage_values import TokenUsage

    services = make_services(tmp_path)
    who = identity()
    session = current_session(services, who, {})["session"]

    def generate(*args, **kwargs):
        kwargs["usage_callback"](TokenUsage(60, 12, 72))
        return "The companions agreed to meet by the gate."

    port = ProviderPort(generate, usage_recorder=partial(record_usage, db_factory=services.db_factory))
    db = services.db_factory()
    try:
        with write_transaction(db):
            db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (who.chat_id, session["session_id"], "assistant", "Meet by the gate.", time.time()),
            )
        result = generate_session_summary(
            db, who.chat_id, session, force=True, provider_port=port, app_settings=services.config
        )
        assert result == "The companions agreed to meet by the gate."
    finally:
        db.close()
    report = usage_summary(services, who, {})
    assert report["totals"]["total_tokens"] == 72
    assert report["purposes"][0]["purpose"] == "summary"

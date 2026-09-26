"""Behavioral regressions for the opt-in rewrite and its session boundary."""

from __future__ import annotations

import pytest
from application_test_setup import make_test_input_flow_service, make_test_rag_service

from bridge import humanize
from bridge.callbacks import is_session_scoped_panel_callback
from bridge.provider_port import ProviderPort
from bridge.request_types import RequestContext
from bridge.session_core import create_session, ensure_session, load_session, update_session
from bridge.settings import load_app_settings
from bridge.sqlite_store import db_connect, write_transaction


@pytest.fixture
def context(tmp_path):
    settings = load_app_settings({}, home=tmp_path)
    db = db_connect(app_settings=settings)
    session = ensure_session(db, "chat", "fixture::model", app_settings=settings)
    try:
        yield db, session, RequestContext(db, session["session_id"], "actor", app_settings=settings)
    finally:
        db.close()


def test_humanizer_skips_oversized_source_without_provider_call():
    port = ProviderPort(lambda *a, **k: pytest.fail("oversized source must not be sent"))
    source = "x" * 24001
    assert humanize.render_humanized_response("", "model", source, "s", provider_port=port) == source


def test_humanizer_caps_settings_and_forwards_timeout_without_mutating_input():
    calls = []
    source_settings = {"max_tokens": 16000, "temperature": 1.3, "reasoning_budget": 8000}
    port = ProviderPort(lambda *a, **k: calls.append(k) or "A plain sentence.")
    assert (
        humanize.render_humanized_response("", "model", "A useful sentence.", "s", source_settings, provider_port=port)
        == "A plain sentence."
    )
    assert calls[0]["request_timeout"] == 30.0
    assert calls[0]["force_non_stream"] is True
    assert calls[0]["settings"]["max_tokens"] <= 4096
    assert calls[0]["settings"]["reasoning_budget"] == 0
    assert source_settings == {"max_tokens": 16000, "temperature": 1.3, "reasoning_budget": 8000}


@pytest.mark.parametrize(
    "source,rewritten",
    [
        ("The cost is 120 units.", "The cost is 999 units."),
        ('She says "Stay here" quietly.', 'She says "Go away" quietly.'),
        ("Run `printf hello` now.", "Run `rm -rf notes` now."),
        ("Read https://example.test/source for details.", "Read https://other.test/source for details."),
        ("*she walks slowly* A quiet evening.", "*she runs away* A quiet evening."),
        ("Use the guide.\n\nSources: [notes.txt]", "Use the guide."),
        ("A lengthy explanation " * 80, "Summary."),
    ],
)
def test_destructive_or_truncated_rewrite_keeps_original(source, rewritten):
    result = humanize.render_humanized_response(
        "", "model", source, "s", provider_port=ProviderPort(lambda *a, **k: rewritten)
    )
    assert result == source


def test_humanizer_logs_no_exception_payload(caplog):
    def fail(*args, **kwargs):
        raise RuntimeError("private-provider-credential-and-source")

    assert (
        humanize.render_humanized_response("", "model", "original", "s", provider_port=ProviderPort(fail)) == "original"
    )
    assert "private-provider-credential-and-source" not in caplog.text


def test_humanizer_callbacks_require_session_binding():
    assert is_session_scoped_panel_callback("enum:humanizer:on")
    assert is_session_scoped_panel_callback("enum:humanizer:off")


def test_flag_uses_existing_session_metadata_without_schema_change(context):
    db, session, ctx = context
    assert "humanizer" not in {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
    assert session["humanizer"] == "off"
    update_session(db, "chat", session["session_id"], humanizer="on")
    reloaded = load_session(db, "chat", session["session_id"], "fixture::model", app_settings=ctx.app_settings)
    assert reloaded["humanizer"] == "on"
    another = create_session(db, "chat", "fixture::model", session_id="other", app_settings=ctx.app_settings)
    assert another["humanizer"] == "off"


def test_flag_joins_outer_transaction_and_validates_before_write(context):
    db, session, ctx = context
    with pytest.raises(ValueError, match="rollback"):
        with write_transaction(db):
            update_session(db, "chat", session["session_id"], humanizer="on")
            raise ValueError("rollback")
    assert (
        load_session(db, "chat", session["session_id"], "fixture::model", app_settings=ctx.app_settings)["humanizer"]
        == "off"
    )
    with pytest.raises(ValueError):
        update_session(db, "chat", session["session_id"], humanizer="maybe")


def test_reset_all_generation_settings_disables_humanizer(context, monkeypatch):
    from bridge import enum_callbacks

    db, session, ctx = context
    update_session(db, "chat", session["session_id"], humanizer="on")
    monkeypatch.setattr(enum_callbacks, "send_settings_menu", lambda *a, **k: None)
    enum_callbacks.handle_enum_callback(
        db,
        "token",
        "chat",
        session,
        "enum:settings:reset",
        {},
        input_flow_service=make_test_input_flow_service(app_settings=ctx.app_settings),
        request_context=ctx,
        rag_service=make_test_rag_service(),
    )
    assert (
        load_session(db, "chat", session["session_id"], "fixture::model", app_settings=ctx.app_settings)["humanizer"]
        == "off"
    )


def test_normal_generation_stores_and_delivers_humanized_reply_without_raw_preview(context, monkeypatch):
    from application_test_setup import make_test_application_services

    from bridge import message_commands

    db, session, ctx = context
    session["humanizer"] = "on"
    calls, deliveries = [], []

    def generate(*args, **kwargs):
        calls.append(kwargs)
        assert not db.in_transaction
        return "Plain prose." if str(kwargs["session_id"]).endswith(":humanize") else "Useful prose."

    port = ProviderPort(generate)
    services = make_test_application_services(app_settings=ctx.app_settings, provider=port)
    monkeypatch.setattr(message_commands, "build_chat_messages", lambda *a, **k: [])
    monkeypatch.setattr(message_commands, "send_typing", lambda *a, **k: None)
    monkeypatch.setattr(
        message_commands, "telegram_request", lambda *a, **k: pytest.fail("raw preview must not be sent")
    )
    monkeypatch.setattr(message_commands, "queue_user_quote_tts", lambda *a, **k: None)
    monkeypatch.setattr(message_commands, "send_reply", lambda _token, _chat, text, *a, **k: deliveries.append(text))
    message_commands.generate_and_store_reply(
        db,
        "token",
        "key",
        {"name": "Alice"},
        "chat",
        "Hello",
        session,
        session["session_id"],
        "fixture::model",
        None,
        "",
        None,
        None,
        group_service=services.group,
        provider_port=port,
        memory_service=services.memory,
        persona_service=services.persona,
        app_settings=ctx.app_settings,
        rag_service=services.rag,
    )
    assert deliveries == ["Plain prose."]
    assert db.execute("SELECT content FROM messages WHERE role='assistant'").fetchall() == [("Plain prose.",)]
    assert len(calls) == 2
    assert calls[0]["stream_callback"] is None
    assert calls[1]["request_timeout"] == 30.0

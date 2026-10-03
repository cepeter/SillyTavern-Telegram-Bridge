import json
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest
from application_test_setup import make_test_application_services
from test_light_novel_storage import novel_db as novel_db

import bridge.response_delivery as greeting_delivery
from bridge import command_routes, greetings, update_message_routing
from bridge.conversation_lifecycle import conversation_state, reset_conversation
from bridge.metadata import set_meta
from bridge.request_types import RequestContext


@pytest.mark.parametrize(
    "message",
    [
        {"text": "I walk into the tavern."},
        {"text": "start"},
        {"voice": {"file_id": "audio"}},
        {"photo": [{"file_id": "image"}]},
    ],
)
def test_prestart_input_rejected_before_enqueue(novel_db, monkeypatch, message):
    db, _session, settings = novel_db
    sent = []
    from bridge.main import _build_startup_services
    from bridge.model_router import ModelRouter

    services = _build_startup_services(settings, model_router=ModelRouter(load_catalog=lambda: {}))
    jobs = Mock()
    jobs.enqueue.side_effect = AssertionError("must not enqueue before /start")
    services = replace(
        services, jobs=jobs, telegram=replace(services.telegram, send_text=lambda *a: sent.append(a[2]) or [1])
    )
    monkeypatch.setattr(update_message_routing, "send_help_command", lambda *a, **k: False)
    message = {"chat": {"id": "chat"}, "from": {"id": "owner"}, "message_id": 4, **message}
    assert update_message_routing.route_message_update(services, db, {}, message, 4, frozenset({"owner"}))
    assert sent == ["Please use /start command."]
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0


def test_prestart_pending_character_upload_allows_png_document_enqueue(novel_db, monkeypatch):
    db, session, settings = novel_db
    sent = []
    from bridge.main import _build_startup_services
    from bridge.model_router import ModelRouter

    services = _build_startup_services(settings, model_router=ModelRouter(load_catalog=lambda: {}))
    jobs = Mock()
    jobs.enqueue.return_value = 31
    jobs.submit.return_value = True
    services = replace(
        services, jobs=jobs, telegram=replace(services.telegram, send_text=lambda *a: sent.append(a[2]) or [1])
    )
    set_meta(
        db,
        "character_upload:chat:owner",
        json.dumps(
            {
                "session_id": session["session_id"],
                "actor_id": "owner",
                "expires_at": time.time() + 600,
            }
        ),
    )
    message = {
        "chat": {"id": "chat"},
        "from": {"id": "owner"},
        "message_id": 9,
        "document": {
            "file_id": "card",
            "file_name": "Alice.png",
            "mime_type": "image/png",
            "file_size": 800,
        },
    }

    assert update_message_routing.route_message_update(services, db, {}, message, 9, frozenset({"owner"}))
    jobs.enqueue.assert_called_once()
    assert jobs.enqueue.call_args.args[5] == "document"
    assert sent == ["📄 Document queued for character-card processing or Data Bank indexing."]


def test_prestart_character_upload_state_is_actor_scoped(novel_db, monkeypatch):
    db, session, settings = novel_db
    sent = []
    from bridge.main import _build_startup_services
    from bridge.model_router import ModelRouter

    services = _build_startup_services(settings, model_router=ModelRouter(load_catalog=lambda: {}))
    jobs = Mock()
    jobs.enqueue.side_effect = AssertionError("another actor must not use pending character upload")
    services = replace(
        services, jobs=jobs, telegram=replace(services.telegram, send_text=lambda *a: sent.append(a[2]) or [1])
    )
    set_meta(
        db,
        "character_upload:chat",
        json.dumps(
            {
                "session_id": session["session_id"],
                "actor_id": "owner",
                "expires_at": time.time() + 600,
            }
        ),
    )
    message = {
        "chat": {"id": "chat"},
        "from": {"id": "other"},
        "message_id": 10,
        "document": {"file_id": "card", "file_name": "Alice.png", "mime_type": "image/png"},
    }

    assert update_message_routing.route_message_update(services, db, {}, message, 10, frozenset({"owner", "other"}))
    assert sent == ["Please use /start command."]


def test_prestart_non_image_document_keeps_management_route(novel_db, monkeypatch):
    db, _session, settings = novel_db
    sent = []
    from bridge.main import _build_startup_services
    from bridge.model_router import ModelRouter

    services = _build_startup_services(settings, model_router=ModelRouter(load_catalog=lambda: {}))
    jobs = Mock()
    jobs.enqueue.return_value = 32
    jobs.submit.return_value = True
    services = replace(
        services, jobs=jobs, telegram=replace(services.telegram, send_text=lambda *a: sent.append(a[2]) or [1])
    )
    message = {
        "chat": {"id": "chat"},
        "from": {"id": "owner"},
        "message_id": 11,
        "document": {"file_id": "pdf", "file_name": "notes.pdf", "mime_type": "application/pdf"},
    }

    assert update_message_routing.route_message_update(services, db, {}, message, 11, frozenset({"owner"}))
    jobs.enqueue.assert_called_once()
    assert jobs.enqueue.call_args.args[5] == "document"
    assert jobs.enqueue.call_args.args[6]["character_upload"] is False
    assert sent == ["📄 Document queued for character-card processing or Data Bank indexing."]


def run_start(db, session, settings, command="/start"):
    services = make_test_application_services(app_settings=settings)
    return command_routes._handle_basic(
        db,
        "token",
        "key",
        "story::test",
        {"name": "Alice", "first_mes": "Hello"},
        "chat",
        command,
        command,
        session,
        session["session_id"],
        "story::test",
        "",
        "User",
        None,
        request_context=RequestContext(db, session["session_id"], "owner", app_settings=settings),
        conversation_service=services.conversation,
        delivery_port=services.delivery,
        group_service=services.group,
        memory_service=services.memory,
        provider_port=services.provider,
    )


def test_start_opens_only_chooser_without_readiness_call(novel_db, monkeypatch):
    db, session, settings = novel_db
    opened = []
    monkeypatch.setattr(command_routes, "send_greeting_menu", lambda *a, **k: opened.append(a) or True)
    monkeypatch.setattr(command_routes, "send_text", lambda *a, **k: pytest.fail("unexpected readiness message"))
    assert run_start(db, session, settings)
    assert len(opened) == 1
    assert not conversation_state(db, "chat", "story").started


def test_greeting_commit_marks_started_and_blocks_second_start(novel_db, monkeypatch):
    db, session, settings = novel_db
    monkeypatch.setattr(
        greeting_delivery,
        "send_text",
        lambda *a, acknowledged_chunk=None, entities=None: acknowledged_chunk(71) or [71],
    )
    assert greetings.send_character_greeting(
        db,
        "token",
        "chat",
        {"name": "Alice", "first_mes": "Hello"},
        "story",
        "User",
        operation_id=41,
        app_settings=settings,
    )
    assert conversation_state(db, "chat", "story").started
    sent = []
    monkeypatch.setattr(command_routes, "send_text", lambda *a: sent.append(a[2]) or [])
    monkeypatch.setattr(command_routes, "send_greeting_menu", lambda *a, **k: pytest.fail("second opening"))
    assert run_start(db, session, settings)
    assert sent == ["This session has already started."]
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1


def test_greeting_choice_carries_reset_epoch(novel_db, monkeypatch):
    db, _session, settings = novel_db
    sent = []
    monkeypatch.setattr(
        greetings, "send_panel_request", lambda token, method, payload, **kw: sent.append(payload) or {"message_id": 72}
    )
    greetings.send_greeting_menu(
        "token",
        "chat",
        {"name": "Alice", "first_mes": "Hello"},
        "User",
        request_context=RequestContext(db, "story", "owner", app_settings=settings),
    )
    buttons = [b for row in sent[-1]["reply_markup"]["inline_keyboard"] for b in row]
    assert next(b["callback_data"] for b in buttons if "Start with" in b["text"]) == "greeting:use:0:0"
    reset_conversation(db, "chat", "story")
    monkeypatch.setattr(greeting_delivery, "send_text", lambda *a, **k: pytest.fail("stale greeting sent"))
    assert not greetings.send_character_greeting(
        db,
        "token",
        "chat",
        {"name": "Alice", "first_mes": "Hello"},
        "story",
        "User",
        expected_epoch=0,
        app_settings=settings,
    )


def test_failed_greeting_delivery_reuses_committed_row(novel_db, monkeypatch):
    db, _session, settings = novel_db
    monkeypatch.setattr(greeting_delivery, "send_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network")))
    with pytest.raises(RuntimeError):
        greetings.send_character_greeting(
            db,
            "token",
            "chat",
            {"name": "Alice", "first_mes": "Original"},
            "story",
            "User",
            operation_id=41,
            app_settings=settings,
        )
    assert conversation_state(db, "chat", "story").started
    sent = []
    monkeypatch.setattr(
        greeting_delivery,
        "send_text",
        lambda *a, acknowledged_chunk=None, entities=None: sent.append(a[2]) or acknowledged_chunk(73) or [73],
    )
    assert greetings.send_character_greeting(
        db,
        "token",
        "chat",
        {"name": "Alice", "first_mes": "Changed card"},
        "story",
        "User",
        operation_id=41,
        app_settings=settings,
    )
    assert sent == ["Original"]
    assert db.execute("SELECT content FROM messages").fetchall() == [("Original",)]

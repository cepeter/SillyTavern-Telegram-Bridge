from __future__ import annotations

import ast
from dataclasses import MISSING
from pathlib import Path
from types import SimpleNamespace

from application_test_setup import (
    make_test_application_services,
    make_test_group_service,
    make_test_memory_service,
    make_test_persona_service,
    make_test_provider_port,
    make_test_request_context,
)

ROOT = Path(__file__).parents[1]
BRIDGE = ROOT / "bridge"


def imported_modules(filename: str) -> set[str]:
    tree = ast.parse((BRIDGE / filename).read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_input_flow_service_requires_final_backends_and_injects_handler():
    from bridge.input_flow_service import InputFlowService

    for name in (
        "start_text_action_backend",
        "handle_session_name_backend",
    ):
        field = InputFlowService.__dataclass_fields__[name]
        assert field.default is MISSING

    pending_calls = []
    session_handler = object()
    text_calls = []

    service = InputFlowService(
        handle_pending_backend=lambda *args, **kwargs: pending_calls.append((args, kwargs)) or True,
        start_session_name_backend=lambda *_args, **_kwargs: None,
        start_text_action_backend=lambda *args, **kwargs: text_calls.append((args, kwargs)),
        handle_session_name_backend=session_handler,
    )

    assert service.handle_pending("db", "token", sample=1) is True
    assert pending_calls == [
        (
            ("db", "token"),
            {
                "handle_session_name": session_handler,
                "sample": 1,
            },
        )
    ]

    service.start_text_action("db", "token", action="memory_search")
    assert text_calls == [(("db", "token"), {"action": "memory_search"})]


def test_help_no_longer_imports_input_flows_and_enum_uses_service():
    import bridge.help as help_module

    assert "bridge.input_flows" not in imported_modules("help.py")

    calls = []
    input_flow = SimpleNamespace(start_text_action=lambda *args, **kwargs: calls.append((args, kwargs)))
    db = object()
    session = {"session_id": "session"}
    message = {"message_id": 41}

    help_module.handle_enum_callback(
        db,
        "token",
        "chat",
        session,
        "enum:memory:search",
        message,
        input_flow_service=input_flow,
        request_context=SimpleNamespace(db=db),
    )

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:6] == (
        db,
        "token",
        "chat",
        "session",
        "memory_search",
        "Send a query to search Hindsight memory for the active session.",
    )
    assert args[6] == {"message": message}
    assert kwargs["request_context"].db is db


def test_callback_dispatch_forwards_exact_input_flow_service(monkeypatch):
    import bridge.callback_dispatch as dispatch

    captured = {}
    input_flow = object()
    services = make_test_application_services(input_flow=input_flow)

    monkeypatch.setattr(
        dispatch,
        "handle_primary_panel_callback",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        dispatch,
        "handle_entity_panel_callback",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        dispatch,
        "panel_session_for_message",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        dispatch,
        "panel_owner_for_message",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        dispatch,
        "ensure_session",
        lambda *_args, **_kwargs: {
            "session_id": "session",
            "character_file": "mira.png",
        },
    )
    monkeypatch.setattr(
        dispatch,
        "handle_enum_callback",
        lambda *args, **kwargs: captured.update(kwargs),
    )

    dispatch.process_callback(
        object(),
        "token",
        {
            "id": "cb",
            "from": {"id": "user"},
            "data": "enum:memory:search",
            "message": {
                "message_id": 41,
                "chat": {"id": "chat"},
            },
        },
        services=services,
    )

    assert captured["input_flow_service"] is input_flow


def test_input_flows_no_longer_imports_session_naming_or_status_panels():
    imports = imported_modules("input_flows.py")
    assert "bridge.session_naming" not in imports
    assert "bridge.status_panels" not in imports


def test_pending_session_name_uses_injected_handler(monkeypatch):
    import bridge.input_flows as flows

    calls = []

    def handler(*args, **kwargs):
        return calls.append((args, kwargs)) or True

    db = object()
    session = {
        "session_id": "session",
        "character_file": "mira.png",
    }
    pending = {
        "session_id": "session",
        "kind": "standard",
        "expires_at": 99999999999,
    }

    monkeypatch.setattr(
        flows,
        "_pending_state",
        lambda _db, key, *_args: pending if key.startswith("session_name_input:") else {},
    )

    handled = flows.handle_pending_input(
        db,
        "token",
        "chat",
        session,
        "New Session",
        operation_id=77,
        handle_session_name=handler,
        group_service=make_test_group_service(),
        provider_port=make_test_provider_port(),
        memory_service=make_test_memory_service(),
        persona_service=make_test_persona_service(),
        request_context=make_test_request_context(
            db,
            "session",
            "user",
        ),
    )

    assert handled is True
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:6] == (
        db,
        "token",
        "chat",
        session,
        "New Session",
        pending,
    )
    assert args[6] == 77
    assert kwargs["group_service"] is not None
    assert kwargs["request_context"].session_id == "session"


def test_director_goal_pending_action_uses_pure_panel_delivery(monkeypatch):
    import bridge.input_flows as flows

    saved = []
    panels = []
    monkeypatch.setattr(
        flows,
        "set_director_goal",
        lambda db, chat_id, session_id, value: saved.append((db, chat_id, session_id, value)) or value,
    )
    monkeypatch.setattr(
        flows,
        "send_panel_request",
        lambda token, method, payload, **kwargs: panels.append((token, method, payload, kwargs)) or {},
    )
    monkeypatch.setattr(
        flows,
        "_cancel_pending",
        lambda *_args, **_kwargs: None,
    )

    db = object()
    session = {"session_id": "session"}
    handled = flows._handle_text_action_input(
        db,
        "token",
        "key",
        "chat",
        session,
        {"name": "Mira"},
        "Protect the witness",
        {
            "session_id": "session",
            "action": "director_goal",
        },
        17,
        provider_port=make_test_provider_port(),
        memory_service=make_test_memory_service(),
        persona_service=make_test_persona_service(),
        request_context=make_test_request_context(
            db,
            "session",
            "user",
        ),
    )

    assert handled is True
    assert saved == [(db, "chat", "session", "Protect the witness")]
    assert len(panels) == 1
    token, method, payload, kwargs = panels[0]
    assert token == "token"
    assert method == "sendMessage"
    assert payload["chat_id"] == "chat"
    assert payload["text"] == ("Director objective\n\nProtect the witness")
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "goal:set"
    assert kwargs["request_context"].session_id == "session"

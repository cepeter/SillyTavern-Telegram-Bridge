from __future__ import annotations

import ast
import inspect
from dataclasses import MISSING
from pathlib import Path
from types import SimpleNamespace

from application_test_setup import (
    make_test_delivery_port,
    make_test_group_service,
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


def test_expressions_no_longer_imports_telegram():
    assert "bridge.telegram" not in imported_modules("expressions.py")


def test_expression_menu_requires_and_uses_delivery_port(monkeypatch):
    import bridge.expressions as expressions

    param = inspect.signature(expressions.send_expression_menu).parameters.get("delivery_port")
    assert param is not None
    assert param.default is inspect.Parameter.empty

    calls = []
    delivery = make_test_delivery_port(
        send_panel_request=lambda token, method, payload, **kwargs: (
            calls.append((token, method, payload, kwargs)) or {}
        ),
    )
    monkeypatch.setattr(expressions, "get_meta", lambda *_args: "off")
    monkeypatch.setattr(
        expressions,
        "discover_expression_assets",
        lambda *_args: {"joy": Path("joy.png")},
    )

    expressions.send_expression_menu(
        "token",
        "chat",
        {"session_id": "session", "character_file": "mira.png"},
        object(),
        delivery_port=delivery,
        request_context="ctx",
    )

    assert len(calls) == 1
    token, method, payload, kwargs = calls[0]
    assert token == "token"
    assert method == "sendMessage"
    assert payload["chat_id"] == "chat"
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "expression:joy"
    assert kwargs["request_context"] == "ctx"


def test_expression_callback_requires_delivery_port():
    import bridge.panel_callback_routes as routes

    param = inspect.signature(routes.handle_expression_callback).parameters.get("delivery_port")
    assert param is not None
    assert param.default is inspect.Parameter.empty


def test_input_flow_service_requires_session_name_backend_and_delegates():
    from bridge.input_flow_service import InputFlowService

    field = InputFlowService.__dataclass_fields__["start_session_name_backend"]
    assert field.default is MISSING

    calls = []

    service = InputFlowService(
        handle_pending_backend=lambda *_args, **_kwargs: False,
        start_session_name_backend=lambda *args, **kwargs: calls.append((args, kwargs)),
        start_text_action_backend=lambda *_args, **_kwargs: None,
        handle_session_name_backend=lambda *_args, **_kwargs: False,
    )
    service.start_session_name(1, 2, kind="group")

    assert calls == [((1, 2), {"kind": "group"})]


def test_groups_no_longer_imports_session_naming():
    assert "bridge.session_naming" not in imported_modules("groups.py")


def test_group_new_session_uses_injected_input_flow_service():
    import bridge.groups as groups

    calls = []
    group_service = make_test_group_service()
    input_flow = SimpleNamespace(start_session_name=lambda *args, **kwargs: calls.append((args, kwargs)))
    db = object()
    session = {"session_id": "session", "character_file": "mira.png"}
    message = {"message_id": 41}

    groups.handle_group_panel_callback(
        db,
        "token",
        "chat|topic:1",
        session,
        "group:new_session",
        message,
        group_service=group_service,
        input_flow_service=input_flow,
        request_context=SimpleNamespace(db=db),
    )

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (db, "token", "chat|topic:1", session)
    assert kwargs["kind"] == "group"
    assert kwargs["message"] is message
    assert kwargs["group_service"] is group_service

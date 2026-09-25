from __future__ import annotations

import ast
from pathlib import Path

from application_test_setup import make_test_delivery_port

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


def test_language_module_does_not_import_telegram():
    assert "bridge.telegram" not in imported_modules("language.py")


def test_language_menu_uses_injected_delivery_port():
    import bridge.language as language

    calls = []
    delivery = make_test_delivery_port(
        send_panel_request=lambda token, method, payload, **kwargs: (
            calls.append((token, method, payload, kwargs)) or {}
        ),
    )

    language.send_language_menu(
        "token",
        "chat",
        "id",
        41,
        0,
        delivery_port=delivery,
        request_context="ctx",
    )

    assert len(calls) == 1
    token, method, payload, kwargs = calls[0]
    assert token == "token"
    assert method == "editMessageText"
    assert payload["chat_id"] == "chat"
    assert payload["message_id"] == 41
    assert "Bahasa Indonesia" in payload["text"]
    assert kwargs["request_context"] == "ctx"


def test_set_response_language_uses_injected_session_update():
    import bridge.language as language

    calls = []

    def update_session(db, chat_id, session_id, **kwargs):
        calls.append((db, chat_id, session_id, kwargs))

    db = object()
    result = language.set_response_language(
        db,
        "chat",
        "session",
        "bahasa indonesia",
        operation_id=77,
        update_session=update_session,
    )

    assert result == "id"
    assert calls == [
        (
            db,
            "chat",
            "session",
            {
                "operation_id": 77,
                "operation_kind": "language_select",
                "response_language": "id",
            },
        )
    ]


def test_language_command_uses_injected_delivery_and_update_session():
    import bridge.language as language

    sent = []
    updates = []
    delivery = make_test_delivery_port(
        send_text=lambda token, chat_id, text: sent.append((token, chat_id, text)) or [],
    )

    language.handle_language_command(
        object(),
        "token",
        "chat",
        {"session_id": "session", "response_language": "auto"},
        "/language id",
        operation_id=11,
        delivery_port=delivery,
        update_session=lambda *args, **kwargs: updates.append((args, kwargs)),
        request_context="ctx",
    )

    assert updates
    assert updates[0][0][1:3] == ("chat", "session")
    assert updates[0][1]["response_language"] == "id"
    assert sent == [("token", "chat", "Model response language set to: Bahasa Indonesia.")]


def test_language_boundary_does_not_change_remember_inline_action(monkeypatch):
    from types import SimpleNamespace

    import bridge.command_routes as routes

    calls = []
    provider = object()
    services = SimpleNamespace(
        memory=object(),
        persona=object(),
        provider=provider,
        delivery=object(),
    )

    monkeypatch.setattr(
        routes,
        "handle_inline_text_action",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    handled = routes._handle_memory_media(
        object(),
        "token",
        "api-key",
        "chat",
        "/remember durable fact",
        "/remember durable fact",
        {"session_id": "session"},
        {"name": "Mira"},
        17,
        services,
        request_context="ctx",
    )

    assert handled is True
    assert len(calls) == 1
    _args, kwargs = calls[0]
    assert "delivery_port" not in kwargs
    assert kwargs["provider_port"] is provider

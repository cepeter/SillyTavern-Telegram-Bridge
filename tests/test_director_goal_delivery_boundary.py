from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace

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


def test_director_goal_panel_is_pure_and_exact():
    path = BRIDGE / "director_goal_panel.py"
    assert path.is_file()
    module = importlib.import_module("bridge.director_goal_panel")
    assert not any(
        name == "bridge" or name.startswith("bridge.") for name in imported_modules("director_goal_panel.py")
    )

    text, markup = module.director_goal_panel("")
    assert text == "Director objective\n\nNo hidden objective is set."
    assert markup == {
        "inline_keyboard": [
            [{"text": "✏️ Set objective", "callback_data": "goal:set"}],
            [
                {"text": "🧹 Clear", "callback_data": "goal:clear"},
                {"text": "❌ Close", "callback_data": "goal:close"},
            ],
        ]
    }

    text, _markup = module.director_goal_panel("Protect the witness")
    assert text == "Director objective\n\nProtect the witness"


def test_director_goals_has_no_status_panels_or_telegram_imports():
    imports = imported_modules("director_goals.py")
    assert "bridge.status_panels" not in imports
    assert "bridge.telegram" not in imports


def test_director_goal_command_requires_delivery_port():
    import bridge.director_goals as goals

    param = inspect.signature(goals.handle_director_goal_command).parameters.get("delivery_port")
    assert param is not None
    assert param.default is inspect.Parameter.empty


def test_director_goal_status_uses_injected_delivery(monkeypatch):
    import bridge.director_goals as goals

    calls = []
    delivery = make_test_delivery_port(
        send_panel_request=lambda token, method, payload, **kwargs: (
            calls.append((token, method, payload, kwargs)) or {}
        ),
    )
    monkeypatch.setattr(goals, "parse_topic_scope", lambda _chat: ("chat", "topic"))
    monkeypatch.setattr(
        goals,
        "get_director_goal",
        lambda *_args: "Protect the witness",
    )

    goals.handle_director_goal_command(
        object(),
        "token",
        "chat|topic:1",
        {"session_id": "session"},
        "/group goal status",
        delivery_port=delivery,
        request_context="ctx",
    )

    assert len(calls) == 1
    token, method, payload, kwargs = calls[0]
    assert token == "token"
    assert method == "sendMessage"
    assert payload["text"] == "Director objective\n\nProtect the witness"
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "goal:set"
    assert kwargs["request_context"] == "ctx"


def test_director_goal_extension_route_forwards_services_delivery(monkeypatch):
    import bridge.director_goals as goals

    captured = {}
    delivery = object()
    monkeypatch.setattr(
        goals,
        "handle_director_goal_command",
        lambda *args, **kwargs: captured.update(kwargs),
    )

    handled = goals._director_goal_command_route(
        object(),
        "token",
        "key",
        "model",
        {},
        "chat|topic:1",
        "/group goal",
        "/group goal",
        {"session_id": "session"},
        "session",
        "model",
        "",
        "User",
        request_context="ctx",
        services=SimpleNamespace(delivery=delivery),
    )

    assert handled is True
    assert captured["delivery_port"] is delivery
    assert captured["request_context"] == "ctx"

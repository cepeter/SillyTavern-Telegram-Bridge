from __future__ import annotations

import ast
import inspect
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from application_test_setup import make_test_delivery_port, make_test_request_context

import bridge.command_panels as _command_panels

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


def top_level_names(filename: str) -> set[str]:
    tree = ast.parse((BRIDGE / filename).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def test_help_and_help_details_do_not_import_each_other():
    assert "bridge.help_details" not in imported_modules("help.py")
    assert "bridge.help" not in imported_modules("help_details.py")


def test_help_details_is_canonical_catalog_and_menu_owner():
    details = top_level_names("help_details.py")
    help_names = top_level_names("help.py")
    assert "HELP_CATEGORIES" in details
    assert "send_help_menu" in details
    assert "HELP_CATEGORIES" not in help_names
    assert "send_help_menu" not in help_names


def test_help_menu_requires_delivery_port_and_preserves_payload():
    import bridge.help_details as details

    param = inspect.signature(details.send_help_menu).parameters.get("delivery_port")
    assert param is not None
    assert param.default is inspect.Parameter.empty

    context = make_test_request_context()
    calls = []
    delivery = make_test_delivery_port(
        send_panel_request=lambda token, method, payload, **kwargs: (
            calls.append((token, method, payload, kwargs)) or {}
        ),
    )

    details.send_help_menu(
        "token",
        "chat",
        "basic",
        41,
        0,
        0,
        delivery_port=delivery,
        request_context=context,
    )

    assert len(calls) == 1
    token, method, payload, kwargs = calls[0]
    assert token == "token"
    assert method == "editMessageText"
    assert payload["chat_id"] == "chat"
    assert payload["message_id"] == 41
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "help:basic"
    assert kwargs["request_context"] is context


def test_help_command_requires_delivery_port():
    import bridge.help_details as details

    param = inspect.signature(details.send_help_command).parameters.get("delivery_port")
    assert param is not None
    assert param.default is inspect.Parameter.empty


def test_help_callback_requires_delivery_port():
    import bridge.help_details as details

    param = inspect.signature(details.handle_help_callback).parameters.get("delivery_port")
    assert param is not None
    assert param.default is inspect.Parameter.empty


def test_command_route_help_detail_forwards_correct_arguments(monkeypatch):
    import bridge.command_routes as routes

    context = make_test_request_context()
    calls = []
    delivery = object()
    monkeypatch.setattr(
        routes,
        "send_help_command",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    handled = routes._handle_basic(
        object(),
        "token",
        "key",
        "model",
        {},
        "chat",
        "/help sync",
        "/help sync",
        {"session_id": "session"},
        "session",
        "model",
        "",
        "User",
        None,
        request_context=context,
        conversation_service=object(),
        delivery_port=SimpleNamespace(delivery=delivery, provider=object()).delivery,
        group_service=object(),
        memory_service=object(),
        provider_port=SimpleNamespace(delivery=delivery, provider=object()).provider,
    )

    assert handled is True
    assert calls == [
        (
            ("token", "chat", "/help sync"),
            {
                "delivery_port": delivery,
                "request_context": context,
            },
        )
    ]


def _route_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE processed_updates(update_id INTEGER PRIMARY KEY,processed_at REAL NOT NULL)")
    db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    return db


def test_update_routing_help_callback_fast_path_forwards_delivery_and_context(monkeypatch):
    import bridge.update_callback_routing as callback_routing
    import bridge.update_routing as routing

    db = _route_db()
    calls = []
    delivery = object()
    services = SimpleNamespace(
        config=SimpleNamespace(bot_token="token", default_model="model"),
        delivery=delivery,
    )
    monkeypatch.setattr(callback_routing, "is_help_callback", lambda _data: True)
    monkeypatch.setattr(
        callback_routing,
        "ensure_session",
        lambda *_args, app_settings=None, **_kwargs: {"session_id": "session"},
    )
    monkeypatch.setattr(
        callback_routing,
        "handle_help_callback",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    try:
        offset = routing.route_update(
            services,
            db,
            {},
            {
                "update_id": 10,
                "callback_query": {
                    "id": "callback",
                    "from": {"id": "user"},
                    "data": "help:menu",
                    "message": {
                        "message_id": 41,
                        "chat": {"id": "chat"},
                    },
                },
            },
            0,
            frozenset({"user"}),
        )
    finally:
        db.close()

    assert offset == 11
    assert len(calls) == 1
    _args, kwargs = calls[0]
    assert kwargs["delivery_port"] is delivery
    ctx = kwargs["request_context"]
    assert ctx.session_id == "session"
    assert ctx.actor_id == "user"


def test_update_routing_help_text_fast_path_forwards_delivery_and_context(monkeypatch):
    import bridge.update_message_routing as message_routing
    import bridge.update_routing as routing

    db = _route_db()
    calls = []
    delivery = object()
    services = SimpleNamespace(
        config=SimpleNamespace(bot_token="token", default_model="model"),
        delivery=delivery,
    )
    monkeypatch.setattr(
        message_routing,
        "ensure_session",
        lambda *_args, app_settings=None, **_kwargs: {"session_id": "session"},
    )
    monkeypatch.setattr(
        message_routing,
        "send_help_command",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    try:
        offset = routing.route_update(
            services,
            db,
            {},
            {
                "update_id": 11,
                "message": {
                    "message_id": 42,
                    "from": {"id": "user"},
                    "chat": {"id": "chat"},
                    "text": "/help sync",
                },
            },
            0,
            frozenset({"user"}),
        )
    finally:
        db.close()

    assert offset == 12
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("token", "chat", "/help sync")
    assert kwargs["delivery_port"] is delivery
    assert kwargs["request_context"].session_id == "session"
    assert kwargs["request_context"].actor_id == "user"


def test_memory_module_no_longer_imports_telegram():
    assert "bridge.telegram" not in imported_modules("memory.py")


def test_memory_command_requires_text_delivery_and_preserves_message(*, app_settings_builder):
    import bridge.memory as memory

    param = inspect.signature(memory.handle_memory_command).parameters.get("send_text_fn")
    assert param is not None
    assert param.default is inspect.Parameter.empty

    sent = []
    memory.handle_memory_command(
        object(),
        "token",
        "chat",
        {"session_id": "session"},
        {"name": "Mira"},
        "/memory scope user",
        send_text_fn=lambda token, chat_id, text: sent.append((token, chat_id, text)),
        app_settings=app_settings_builder.build(),
    )

    assert sent == [
        (
            "token",
            "chat",
            "Hindsight recall is fixed to the active session; broader scopes are disabled.",
        )
    ]


def test_command_routes_memory_search_forwards_delivery_send_text(monkeypatch):

    calls = []
    send_text_fn = object()
    monkeypatch.setattr(
        _command_panels,
        "handle_memory_command",
        lambda *args, app_settings=None, **kwargs: calls.append((args, kwargs)),
    )

    handled = _command_panels._handle_memory_media(
        object(),
        "token",
        "key",
        "chat",
        "/memory search fact",
        "/memory search fact",
        {"session_id": "session"},
        {"name": "Mira"},
        None,
        request_context=make_test_request_context(),
        delivery_port=SimpleNamespace(
            group=object(),
            sync=object(),
            memory=object(),
            persona=object(),
            provider=object(),
            delivery=SimpleNamespace(send_text=send_text_fn),
        ).delivery,
        group_service=SimpleNamespace(
            group=object(),
            sync=object(),
            memory=object(),
            persona=object(),
            provider=object(),
            delivery=SimpleNamespace(send_text=send_text_fn),
        ).group,
        memory_service=SimpleNamespace(
            group=object(),
            sync=object(),
            memory=object(),
            persona=object(),
            provider=object(),
            delivery=SimpleNamespace(send_text=send_text_fn),
        ).memory,
        persona_service=SimpleNamespace(
            group=object(),
            sync=object(),
            memory=object(),
            persona=object(),
            provider=object(),
            delivery=SimpleNamespace(send_text=send_text_fn),
        ).persona,
        provider_port=SimpleNamespace(
            group=object(),
            sync=object(),
            memory=object(),
            persona=object(),
            provider=object(),
            delivery=SimpleNamespace(send_text=send_text_fn),
        ).provider,
        sync_service=SimpleNamespace(
            group=object(),
            sync=object(),
            memory=object(),
            persona=object(),
            provider=object(),
            delivery=SimpleNamespace(send_text=send_text_fn),
        ).sync,
    )

    assert handled is True
    assert len(calls) == 1
    _args, kwargs = calls[0]
    assert kwargs["send_text_fn"] is send_text_fn


def test_pending_memory_search_forwards_existing_send_text(monkeypatch):
    import bridge.input_flows as flows

    calls = []
    monkeypatch.setattr(
        flows,
        "handle_memory_command",
        lambda *args, app_settings=None, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(flows, "send_memory_menu", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(flows, "_cancel_pending", lambda *_args, **_kwargs: None)

    result = flows._handle_text_action_input(
        object(),
        "token",
        "key",
        "chat",
        {"session_id": "session"},
        {"name": "Mira"},
        "fact",
        {"action": "memory_search"},
        None,
        provider_port=object(),
        memory_service=object(),
        persona_service=object(),
        request_context=make_test_request_context(),
    )

    assert result is True
    assert len(calls) == 1
    _args, kwargs = calls[0]
    assert kwargs["send_text_fn"] is flows.send_text

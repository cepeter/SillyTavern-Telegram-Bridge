from __future__ import annotations

import ast
import importlib
import inspect
import os
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


def top_level_functions(filename: str) -> set[str]:
    tree = ast.parse((BRIDGE / filename).read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def top_level_classes(filename: str) -> set[str]:
    tree = ast.parse((BRIDGE / filename).read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }


def test_sillytavern_api_module_is_canonical_and_lower_level():
    path = BRIDGE / "sillytavern_api.py"
    assert path.is_file()
    imports = imported_modules("sillytavern_api.py")
    assert not ({
        "bridge.sync_api",
        "bridge.sync_core",
        "bridge.persona_sync",
        "bridge.telegram",
    } & imports)

    module = importlib.import_module("bridge.sillytavern_api")
    assert hasattr(module, "SillyTavernApiError")
    assert hasattr(module, "SillyTavernApiClient")
    assert callable(module.live_sync_api_configured)
    assert callable(module.live_sync_client)
    assert callable(module.refresh_sillytavern_api_config)


def test_sync_api_no_longer_owns_client_or_error_symbols():
    assert "SillyTavernApiClient" not in top_level_classes("sync_api.py")
    assert "SillyTavernApiError" not in top_level_classes("sync_api.py")
    assert "live_sync_client" not in top_level_functions("sync_api.py")
    assert "live_sync_api_configured" not in top_level_functions("sync_api.py")


def test_persona_sync_has_no_sync_api_or_sync_core_imports():
    imports = imported_modules("persona_sync.py")
    assert "bridge.sync_api" not in imports
    assert "bridge.sync_core" not in imports


def test_native_api_config_refresh_owns_api_credentials_and_timeout(monkeypatch):
    module = importlib.import_module("bridge.sillytavern_api")

    monkeypatch.setenv("SILLYTAVERN_SYNC_API_URL", "http://localhost:8123/")
    monkeypatch.setenv("SILLYTAVERN_SYNC_API_HANDLE", "tester")
    monkeypatch.setenv("SILLYTAVERN_SYNC_API_PASSWORD", "secret")
    monkeypatch.setenv("SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS", "999")

    module.refresh_sillytavern_api_config()

    assert module.LIVE_SYNC_API_URL == "http://localhost:8123"
    assert module.LIVE_SYNC_API_HANDLE == "tester"
    assert module.LIVE_SYNC_API_PASSWORD == "secret"
    assert module.LIVE_SYNC_TIMEOUT_SECONDS == 30
    assert module.live_sync_api_configured() is True


def test_scene_panel_is_pure_and_exact():
    path = BRIDGE / "scene_panel.py"
    assert path.is_file()
    assert not any(
        name == "bridge" or name.startswith("bridge.")
        for name in imported_modules("scene_panel.py")
    )
    module = importlib.import_module("bridge.scene_panel")

    text, markup = module.scene_panel({"location": "dock"}, 17)
    assert text == (
        'Scene state (through message row 17)\n\n{\n  "location": "dock"\n}'
    )
    assert markup == {
        "inline_keyboard": [
            [
                {"text": "🔄 Refresh", "callback_data": "scene:refresh"},
                {"text": "🧹 Clear", "callback_data": "scene:clear"},
            ],
            [
                {"text": "⬅️ Status", "callback_data": "scene:status"},
                {"text": "❌ Close", "callback_data": "scene:close"},
            ],
        ]
    }

    empty, _ = module.scene_panel(None, 0)
    assert empty == (
        "Scene state (through message row 0)\n\n"
        "No structured scene state has been established yet."
    )


def test_scene_state_has_no_status_panels_import_and_requires_delivery_port():
    assert "bridge.status_panels" not in imported_modules("scene_state.py")
    import bridge.scene_state as scene_state

    param = inspect.signature(scene_state.handle_scene_command).parameters.get(
        "delivery_port"
    )
    assert param is not None
    assert param.default is inspect.Parameter.empty


def test_scene_status_command_uses_injected_delivery(monkeypatch):
    import bridge.scene_state as scene_state

    calls = []
    delivery = make_test_delivery_port(
        send_panel_request=lambda token, method, payload, **kwargs: (
            calls.append((token, method, payload, kwargs)) or {}
        ),
    )
    monkeypatch.setattr(
        scene_state,
        "get_scene_state",
        lambda *_args: ({"location": "dock"}, 17),
    )

    scene_state.handle_scene_command(
        object(),
        "token",
        "key",
        "chat",
        {"session_id": "session"},
        {"name": "Mira"},
        "/scene",
        provider_port=object(),
        delivery_port=delivery,
        request_context="ctx",
    )

    assert len(calls) == 1
    token, method, payload, kwargs = calls[0]
    assert token == "token"
    assert method == "sendMessage"
    assert payload["chat_id"] == "chat"
    assert "through message row 17" in payload["text"]
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "scene:refresh"
    assert kwargs["request_context"] == "ctx"


def test_scene_extension_route_forwards_delivery_and_provider(monkeypatch):
    import bridge.scene_state as scene_state

    captured = {}
    delivery = object()
    provider = object()
    monkeypatch.setattr(
        scene_state,
        "handle_scene_command",
        lambda *args, **kwargs: captured.update(kwargs),
    )

    handled = scene_state._scene_state_command_route(
        object(),
        "token",
        "key",
        "model",
        {"name": "Mira"},
        "chat",
        "/scene",
        "/scene",
        {"session_id": "session"},
        "session",
        "model",
        "",
        "User",
        request_context="ctx",
        services=SimpleNamespace(delivery=delivery, provider=provider),
    )

    assert handled is True
    assert captured["delivery_port"] is delivery
    assert captured["provider_port"] is provider
    assert captured["request_context"] == "ctx"

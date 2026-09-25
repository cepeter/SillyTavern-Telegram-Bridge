from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from application_test_setup import make_test_delivery_port, make_test_provider_port
from settings_test_support import make_test_settings

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
    return {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_world_storage_is_canonical_owner_and_low_level():
    path = BRIDGE / "world_storage.py"
    assert path.is_file()
    imports = imported_modules("world_storage.py")
    assert "bridge.telegram" not in imports
    assert "bridge.catalog" not in imports
    assert "bridge.cards" not in imports
    assert "install_world_info_document" in top_level_functions("world_storage.py")
    assert "install_world_info_document" not in top_level_functions("catalog.py")
    assert "bridge.catalog" not in imported_modules("telegram.py")


def test_world_storage_preserves_validation_and_atomic_install(tmp_path, monkeypatch):
    module = importlib.import_module("bridge.world_storage")
    settings = make_test_settings(world_dir=tmp_path)

    raw = b'{"entries":{"1":{"key":["dragon"],"content":"fire"}}}'
    target = module.install_world_info_document("dragons.json", raw, app_settings=settings)

    assert target == tmp_path / "dragons.json"
    assert target.read_bytes() == raw

    with pytest.raises(FileExistsError, match="already exists"):
        module.install_world_info_document("dragons.json", raw, app_settings=settings)
    with pytest.raises(ValueError, match="simple filename"):
        module.install_world_info_document("../dragons.json", raw, app_settings=settings)
    with pytest.raises(ValueError, match="JSON file"):
        module.install_world_info_document("dragons.txt", raw, app_settings=settings)
    with pytest.raises(ValueError, match="entries object"):
        module.install_world_info_document("empty.json", b"{}", app_settings=settings)
    with pytest.raises(ValueError, match="JSON is invalid"):
        module.install_world_info_document("bad.json", b"{not-json", app_settings=settings)


def test_curated_memory_panel_is_pure_and_exact():
    path = BRIDGE / "curated_memory_panel.py"
    assert path.is_file()
    module = importlib.import_module("bridge.curated_memory_panel")
    assert not any(
        name == "bridge" or name.startswith("bridge.") for name in imported_modules("curated_memory_panel.py")
    )

    text, markup = module.curated_memory_panel("")
    assert text == "Curated memory\n\nNo curated durable memories yet."
    assert markup == {
        "inline_keyboard": [
            [{"text": "🔄 Refresh", "callback_data": "curated:refresh"}],
            [
                {"text": "⬅️ Memory", "callback_data": "curated:back"},
                {"text": "❌ Close", "callback_data": "curated:close"},
            ],
        ]
    }

    text, _markup = module.curated_memory_panel("Remember the red key.")
    assert text == "Curated memory\n\nRemember the red key."


def test_memory_curator_does_not_import_status_panels_and_requires_delivery_port():
    import bridge.memory_curator as curator

    assert "bridge.status_panels" not in imported_modules("memory_curator.py")
    param = inspect.signature(curator.handle_curated_memory_command).parameters.get("delivery_port")
    assert param is not None
    assert param.default is inspect.Parameter.empty


def test_curated_memory_status_uses_injected_delivery(monkeypatch):
    import bridge.memory_curator as curator

    calls = []
    delivery = make_test_delivery_port(
        send_panel_request=lambda token, method, payload, **kwargs: (
            calls.append((token, method, payload, kwargs)) or {}
        ),
    )
    monkeypatch.setattr(
        curator,
        "curated_memory_text",
        lambda *_args: "Remember the red key.",
    )

    curator.handle_curated_memory_command(
        object(),
        "token",
        "api-key",
        "chat",
        {"session_id": "session"},
        {"name": "Mira"},
        "/memory curated",
        provider_port=make_test_provider_port(),
        delivery_port=delivery,
        request_context="ctx",
    )

    assert len(calls) == 1
    token, method, payload, kwargs = calls[0]
    assert token == "token"
    assert method == "sendMessage"
    assert payload["text"] == "Curated memory\n\nRemember the red key."
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "curated:refresh"
    assert kwargs["request_context"] == "ctx"


def test_memory_curator_extension_route_forwards_provider_and_delivery(monkeypatch):
    import bridge.memory_curator as curator

    captured = {}
    provider = object()
    delivery = object()
    monkeypatch.setattr(
        curator,
        "handle_curated_memory_command",
        lambda *args, **kwargs: captured.update(kwargs),
    )

    handled = curator._memory_curator_command_route(
        object(),
        "token",
        "key",
        "model",
        {"name": "Mira"},
        "chat",
        "/memory curated",
        "/memory curated",
        {"session_id": "session"},
        "session",
        "model",
        "",
        "User",
        request_context="ctx",
        delivery_port=SimpleNamespace(provider=provider, delivery=delivery).delivery,
        provider_port=SimpleNamespace(provider=provider, delivery=delivery).provider,
    )

    assert handled is True
    assert captured["provider_port"] is provider
    assert captured["delivery_port"] is delivery
    assert captured["request_context"] == "ctx"


def test_curated_memory_panel_preserves_existing_text_whitespace():
    from bridge.curated_memory_panel import curated_memory_panel

    text, _markup = curated_memory_panel("  Remember exactly.  ")
    assert text == "Curated memory\n\n  Remember exactly.  "

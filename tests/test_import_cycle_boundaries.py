from __future__ import annotations

import ast
import importlib
from pathlib import Path

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


def test_persona_sync_is_canonical_identity_owner(monkeypatch, *, app_settings_builder):
    import bridge.persona_sync as persona_sync

    for name in ("get_persona", "default_persona_id", "persona_name"):
        assert name in top_level_functions("persona_sync.py")

    monkeypatch.setattr(
        persona_sync,
        "load_personas",
        lambda *, app_settings=None: {
            "alice.png": {
                "name": "Alice",
                "description": "Test",
            }
        },
    )
    monkeypatch.setattr(
        persona_sync,
        "_native_settings",
        lambda *, app_settings=None: {
            "power_user": {
                "default_persona": "alice.png",
            }
        },
    )

    assert persona_sync.get_persona("alice.png", app_settings=app_settings_builder.build())["name"] == "Alice"
    assert persona_sync.persona_name("alice.png", app_settings=app_settings_builder.build()) == "Alice"
    assert persona_sync.persona_name("missing.png", app_settings=app_settings_builder.build()) == ""
    assert persona_sync.default_persona_id(app_settings=app_settings_builder.build()) == "alice.png"


def test_cards_no_longer_owns_or_imports_persona_identity():
    owned = top_level_functions("cards.py")
    assert not (
        {
            "get_persona",
            "default_persona_id",
            "persona_name",
        }
        & owned
    )
    assert "bridge.persona_sync" not in imported_modules("cards.py")


def test_persona_sync_has_no_cards_or_telegram_imports():
    imports = imported_modules("persona_sync.py")
    assert "bridge.cards" not in imports
    assert "bridge.telegram" not in imports


def test_telegram_has_no_cards_import():
    assert "bridge.cards" not in imported_modules("telegram.py")


def test_pure_panel_message_request_builds_exact_send_and_edit_payloads():
    panel_utils = importlib.import_module("bridge.panel_utils")
    assert not any(name == "bridge" or name.startswith("bridge.") for name in imported_modules("panel_utils.py"))
    assert hasattr(panel_utils, "panel_message_request")

    method, payload = panel_utils.panel_message_request(
        "chat",
        "Body",
        {"inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]]},
    )
    assert method == "sendMessage"
    assert payload == {
        "chat_id": "chat",
        "text": "Body",
        "reply_markup": {"inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]]},
    }

    method, payload = panel_utils.panel_message_request(
        "chat",
        "Body",
        {"inline_keyboard": []},
        41,
    )
    assert method == "editMessageText"
    assert payload == {
        "chat_id": "chat",
        "text": "Body",
        "reply_markup": {"inline_keyboard": []},
        "message_id": 41,
    }

    method, payload = panel_utils.panel_message_request(
        "chat",
        "Body",
        {"inline_keyboard": []},
        0,
    )
    assert method == "sendMessage"
    assert "message_id" not in payload


def test_cards_and_telegram_use_shared_panel_request_builder():
    cards_source = (BRIDGE / "cards.py").read_text(encoding="utf-8")
    telegram_source = (BRIDGE / "telegram.py").read_text(encoding="utf-8")
    assert "panel_message_request" in cards_source
    assert "panel_message_request" in telegram_source


def test_persona_identity_consumers_use_persona_sync_owner():
    for filename in (
        "commands.py",
        "main.py",
        "sync_core.py",
        "telegram.py",
    ):
        imports = imported_modules(filename)
        assert "bridge.persona_sync" in imports

    assert "bridge.cards" not in imported_modules("commands.py")
    assert "bridge.cards" not in imported_modules("main.py")
    assert "bridge.cards" not in imported_modules("sync_core.py")
    assert "bridge.cards" not in imported_modules("telegram.py")

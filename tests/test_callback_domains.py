"""Callback ownership and no-op behavior follow feature domains, not one UI monolith."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
from pathlib import Path
from types import SimpleNamespace

import application_test_setup as application_setup
import pytest

ROOT = Path(__file__).parents[1]
DOMAINS = {
    "settings_callbacks": [
        "handle_system_prompt_callback",
        "handle_note_callback",
        "handle_language_callback",
        "handle_expression_callback",
    ],
    "conversation_callbacks": ["handle_reset_callback", "handle_swipe_callback", "handle_greeting_callback"],
    "sync_callbacks": ["handle_sync_callback"],
    "character_callbacks": ["handle_character_callback"],
    "session_callbacks": ["handle_session_callback"],
    "world_callbacks": ["handle_world_callback"],
    "provider_callbacks": ["handle_provider_model_callback"],
}


@pytest.mark.parametrize("module,functions", DOMAINS.items())
def test_domain_owns_handlers_and_rejects_unrelated_actions_without_io(module, functions):
    path = ROOT / "bridge" / f"{module}.py"
    assert path.is_file(), f"{module} must be a canonical owner"
    owner = importlib.import_module(f"bridge.{module}")
    inputs = dict(
        db=object(),
        token="fixture",
        callback={},
        answer_callback=lambda *_a: pytest.fail("unrelated callback answered"),
        data="unrelated:action",
        chat_id="chat",
        message={},
        session={},
        session_id="session",
        operation_id=None,
        request_context=SimpleNamespace(app_settings=None),
    )
    for name in functions:
        handler = getattr(owner, name)
        assert handler.__module__ == f"bridge.{module}"
        arguments = {name: inputs.get(name, object()) for name in inspect.signature(handler).parameters}
        assert handler(**arguments) is False


def test_panel_router_contains_only_ordered_dispatchers():
    tree = ast.parse((ROOT / "bridge/panel_callback_routes.py").read_text())
    assert {node.name for node in tree.body if isinstance(node, ast.FunctionDef)} == {
        "handle_primary_panel_callback",
        "handle_entity_panel_callback",
    }


def test_callback_sibling_import_is_an_architecture_violation(tmp_path):
    spec = importlib.util.spec_from_file_location("policy", ROOT / "tools/static_analysis.py")
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    (bridge / "character_callbacks.py").write_text("import bridge.session_callbacks\n")
    (bridge / "session_callbacks.py").write_text("")
    report = policy.check_dependency_direction(bridge, static_targets=())
    assert any("character_callbacks" in error and "session_callbacks" in error for error in report.errors)


def test_expired_character_selection_cannot_read_or_upload_a_card(monkeypatch):
    path = ROOT / "bridge/character_callbacks.py"
    assert path.is_file(), "missing character callback owner"
    owner = importlib.import_module("bridge.character_callbacks")
    answers = []
    monkeypatch.setattr(owner, "resolve_dynamic_callback_token", lambda *_a, **_k: None)
    monkeypatch.setattr(owner, "safe_character_path", lambda *_a, **_k: None)
    monkeypatch.setattr(owner, "card_fields_from_file", lambda *_a, **_k: pytest.fail("expired card was read"))
    monkeypatch.setattr(owner, "send_panel_photo", lambda *_a, **_k: pytest.fail("expired card was sent"))
    handled = owner.handle_character_callback(
        object(),
        "fixture",
        {"id": "cb"},
        lambda *args: answers.append(args[-1]),
        "characterinfo:expired",
        "chat",
        {"message_id": 1},
        {},
        "session",
        None,
        group_service=object(),
        request_context=SimpleNamespace(app_settings=None),
        provider_port=application_setup.make_test_provider_port(),
    )
    assert handled is True
    assert answers == ["Character choice expired"]

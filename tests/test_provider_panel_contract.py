"""Provider maintenance is a panel action, never a misleading text alias."""

from __future__ import annotations

from pathlib import Path

import pytest
from application_test_setup import make_test_request_context
from settings_test_support import make_test_settings

from bridge import catalog, command_routes, help_details, panel_callback_routes
from bridge.update_message_routing import is_long_running_command


@pytest.mark.parametrize("command", ["/providers health", "/providers refresh", "/providers unknown"])
def test_provider_subcommands_explain_panel_without_running_or_opening_it(monkeypatch, command):
    sent = []
    monkeypatch.setattr(command_routes, "task_model_for_session", lambda *a, **kw: "")
    monkeypatch.setattr(command_routes, "send_text", lambda token, chat, text: sent.append(text))
    monkeypatch.setattr(command_routes, "send_model_target_menu", lambda *a, **k: pytest.fail("not a text action"))
    monkeypatch.setattr(catalog, "provider_health_checks", lambda *a, **k: pytest.fail("no network action"))
    monkeypatch.setattr(catalog, "refresh_model_catalog", lambda *a, **k: pytest.fail("no refresh action"))
    assert command_routes._handle_entities(
        None,
        "token",
        "",
        {},
        "chat",
        command,
        {},
        "session",
        "",
        "",
        request_context=make_test_request_context(),
        persona_service=None,
    )
    assert sent == ["Use /providers and choose an action from the panel."]
    assert not is_long_running_command(command)


def test_plain_providers_opens_target_selection(monkeypatch):
    calls = []
    monkeypatch.setattr(command_routes, "send_model_target_menu", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(command_routes, "task_model_for_session", lambda *a, **k: "utility::model")
    context = make_test_request_context()
    assert command_routes._handle_entities(
        None,
        "token",
        "",
        {},
        "chat",
        "/providers",
        {},
        "session",
        "story::model",
        "",
        request_context=context,
        persona_service=None,
    )
    assert calls[0][0] == ("token", "chat", "story::model", "utility::model")
    assert calls[0][1]["request_context"] is context


def test_provider_help_and_readme_advertise_only_panel_entry():
    commands = {command for entries in help_details.HELP_CATEGORIES.values() for command, _ in entries}
    assert "/providers" in commands
    for command in ("/providers health", "/providers refresh"):
        assert command not in commands
        assert command not in help_details.COMMAND_DETAILS
        assert command not in (Path(__file__).parents[1] / "README.md").read_text()


@pytest.mark.parametrize("action", ["provider:health", "provider:refresh"])
def test_panel_buttons_still_run_their_real_action(monkeypatch, action):
    calls = []
    context = make_test_request_context(app_settings=make_test_settings())
    monkeypatch.setattr(
        catalog,
        "provider_health_checks",
        lambda **kw: calls.append(("health", kw)) or [("provider", "Provider", "healthy")],
    )
    monkeypatch.setattr(catalog, "send_panel_message", lambda *a, **kw: None)
    monkeypatch.setattr(
        panel_callback_routes, "refresh_model_catalog", lambda **kw: calls.append(("refresh", kw)) or ({}, 2, 0)
    )
    monkeypatch.setattr(panel_callback_routes, "send_text", lambda *a: None)
    monkeypatch.setattr(panel_callback_routes, "send_model_menu", lambda *a, **kw: None)
    handled = panel_callback_routes.handle_provider_model_callback(
        None,
        "token",
        {"id": "cb"},
        lambda *a: None,
        action,
        "chat",
        {"message_id": 1},
        {"model_id": "provider::model"},
        "session",
        None,
        request_context=context,
    )
    assert handled
    assert calls == (
        [("health", {"app_settings": context.app_settings})]
        if action.endswith("health")
        else [("refresh", {"force": True, "app_settings": context.app_settings})]
    )

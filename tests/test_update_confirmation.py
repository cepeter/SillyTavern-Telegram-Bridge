"""Release metadata never grants execution; confirmations bind a reviewed version."""

from __future__ import annotations

import io
import json

import pytest
from application_test_setup import make_test_request_context

import bridge.update as update


def test_latest_release_uses_explicit_github_network_policy(monkeypatch):
    calls = []

    def opened(request, timeout, *, policy):
        calls.append((request.full_url, timeout, policy))
        return io.BytesIO(json.dumps({"tag_name": "v0.2.030", "body": "Fixed\x00\u202e notes"}).encode())

    monkeypatch.setattr(update, "strict_urlopen", opened, raising=False)
    monkeypatch.setattr(update.urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("default opener bypass"))
    version, notes = update.latest_bridge_release()
    assert version == "0.2.030"
    assert "\x00" not in notes and "\u202e" not in notes
    assert calls[0][2].allowed_hosts == frozenset({"api.github.com"})
    assert calls[0][2].allow_loopback is False


def test_unknown_release_does_not_show_confirm_or_exception_details(monkeypatch):
    def failed():
        raise OSError("secret-in-internal-error")

    monkeypatch.setattr(update, "latest_bridge_release", failed)
    monkeypatch.setattr(update, "installed_bridge_version", lambda *, app_settings=None: "0.2.024")
    monkeypatch.setattr(update, "installed_bridge_has_unreleased", lambda *, app_settings=None: False)
    calls = []
    monkeypatch.setattr(update, "send_panel_request", lambda *args, **kwargs: calls.append((args, kwargs)))
    update.send_update_menu("token", "chat", request_context=make_test_request_context())
    payload = calls[0][0][2]
    assert "secret-in-internal-error" not in payload["text"]
    actions = [item["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for item in row]
    assert not any(action.startswith("update:confirm") for action in actions)


def test_confirmation_is_bound_to_release_version(monkeypatch):
    monkeypatch.setattr(update, "latest_bridge_release", lambda: ("0.2.030", "Notes"))
    monkeypatch.setattr(update, "installed_bridge_version", lambda *, app_settings=None: "0.2.024")
    monkeypatch.setattr(update, "installed_bridge_has_unreleased", lambda *, app_settings=None: False)
    calls = []
    monkeypatch.setattr(update, "send_panel_request", lambda *args, **kwargs: calls.append((args, kwargs)))
    update.send_update_menu("token", "chat", request_context=make_test_request_context())
    payload = calls[0][0][2]
    actions = [item["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for item in row]
    assert "update:confirm:0.2.030" in actions


def test_new_release_requires_new_confirmation(monkeypatch, *, app_settings_builder):
    monkeypatch.setattr(update, "latest_bridge_release", lambda: ("0.2.031", "Notes"))
    monkeypatch.setattr(update, "installed_bridge_version", lambda *, app_settings=None: "0.2.024")
    monkeypatch.setattr(update, "apply_update", lambda *_a: pytest.fail("must not mutate"), raising=False)
    outcome = update._run_update(expected_version="0.2.030", app_settings=app_settings_builder.build())
    assert outcome.code == "stale_confirmation"

"""Empty provider configuration must not imply a hidden endpoint/model fallback."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import bridge.catalog as catalog
import bridge.config as config
import bridge.provider_transport as transport
from bridge.model_router import ModelRouter
from bridge.settings import load_app_settings


@pytest.mark.parametrize("endpoint", [None, "", "   ", "/"])
def test_missing_chat_endpoint_is_a_named_config_error_before_network(tmp_path, monkeypatch, endpoint):
    settings = load_app_settings({}, home=tmp_path)
    router = ModelRouter(load_catalog=lambda: {"fixture": {"models": ["model"], "api_endpoint": endpoint}})
    monkeypatch.setattr(
        transport, "validate_provider_endpoint", lambda *_a, **_k: pytest.fail("no endpoint to validate")
    )
    monkeypatch.setattr(transport, "strict_urlopen", lambda *_a, **_k: pytest.fail("missing endpoint must not send"))
    with pytest.raises(RuntimeError, match="api_endpoint") as error:
        transport.generate_provider_text(router, "private-test-key", "fixture::model", [], app_settings=settings)
    assert "private-test-key" not in str(error.value)


@pytest.mark.parametrize("field", ["api_endpoint", "api"])
def test_explicit_canonical_or_alias_endpoint_is_used(tmp_path, monkeypatch, field):
    settings = load_app_settings({}, home=tmp_path)
    router = ModelRouter(load_catalog=lambda: {"fixture": {"models": ["model"], field: "https://provider.example/v1/"}})
    validated = []
    requests = []
    monkeypatch.setattr(transport, "validate_provider_endpoint", lambda endpoint, **_k: validated.append(endpoint))

    def response(request, **_kwargs):
        requests.append(request)
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}).encode())

    monkeypatch.setattr(transport, "strict_urlopen", response)
    assert transport.generate_provider_text(router, "fixture-key", "fixture::model", [], app_settings=settings) == "OK"
    assert validated == ["https://provider.example/v1"]
    assert requests[0].full_url == "https://provider.example/v1/chat/completions"


def test_empty_catalog_panel_explains_how_to_add_models(tmp_path, monkeypatch):
    settings = load_app_settings({}, home=tmp_path)
    monkeypatch.setattr(catalog, "refresh_model_catalog", lambda **_k: ({"providers": {}}, 0, 0))
    calls = []
    monkeypatch.setattr(catalog, "send_panel_message", lambda *args, **_k: calls.append(args))
    db = sqlite3.connect(":memory:")
    try:
        context = SimpleNamespace(db=db, session_id="session", app_settings=settings)
        assert catalog.get_model_groups(app_settings=settings) == {}
        catalog.send_model_menu("token", "chat", "", request_context=context)
    finally:
        db.close()
    text, markup = calls[-1][2:4]
    assert "No provider models available" in text
    assert "SILLYTAVERN_PROVIDER_CONFIG" in text
    callbacks = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
    assert "provider:health" in callbacks
    assert "provider:refresh" in callbacks
    assert not any(value.startswith("model:") for value in callbacks)


def test_empty_provider_fallback_symbols_are_retired_not_real_defaults():
    assert not hasattr(config, "DEFAULT_PROVIDER_URL")
    assert not hasattr(config, "MODEL_CHOICES")
    assert config.STT_DEFAULT_MODEL == "base"
    root = Path(__file__).parents[1]
    assert "DEFAULT_PROVIDER_URL" not in (root / "bridge/provider_transport.py").read_text()
    assert "MODEL_CHOICES" not in (root / "bridge/catalog.py").read_text()


def test_security_policy_names_existing_codeql_default_setup():
    root = Path(__file__).parents[1]
    security = (root / "SECURITY.md").read_text()
    assert "GitHub CodeQL Default Setup" in security
    assert "Python" in security and "GitHub Actions" in security
    assert "workflow file" in security

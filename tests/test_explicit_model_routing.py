"""Resolve only real configured routes; startup diagnostics never guess a provider."""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

import bridge.main as main
import bridge.provider_catalog as catalog
from bridge.model_router import ModelRouter
from bridge.settings import load_app_settings


def configured_router():
    return ModelRouter(
        load_catalog=lambda: {
            "alpha": {"models": ["unique", "shared", "vendor/model"]},
            "beta": {"models": ["shared", "other"]},
        }
    )


@pytest.mark.parametrize(
    "model", ["", " ", "::unique", "alpha::", "alpha::x::y", "alpha::bad model", "alpha::unique\n"]
)
def test_invalid_model_selection_is_rejected(model):
    with pytest.raises(RuntimeError, match=r"model|provider"):
        configured_router().route(model)


@pytest.mark.parametrize("model", ["unknown::unique", "alpha::unknown", "mystery", "other-vendor/model"])
def test_unknown_routes_never_fall_back_to_a_provider(model):
    with pytest.raises(RuntimeError, match=r"provider|model"):
        configured_router().route(model)


def test_ambiguous_bare_model_requires_qualification():
    with pytest.raises(RuntimeError, match="ambiguous"):
        configured_router().route("shared")
    route = configured_router().route("beta::shared")
    assert (route.provider_id, route.model_id) == ("beta", "shared")


def test_exact_unique_model_including_vendor_prefix_is_resolved_without_guessing():
    router = configured_router()
    assert (router.route("unique").provider_id, router.route("unique").model_id) == ("alpha", "unique")
    assert router.route("vendor/model").model_id == "vendor/model"


def test_catalog_loader_error_is_explicit_and_redacted():
    def broken():
        raise OSError("sensitive-path-and-credential")

    with pytest.raises(RuntimeError, match="catalog") as error:
        ModelRouter(load_catalog=broken).route("alpha::unique")
    assert "sensitive" not in str(error.value)


def test_routing_catalog_uses_only_opted_in_discovery_cache(tmp_path):
    settings = load_app_settings({}, home=tmp_path)
    settings.provider_config_file.parent.mkdir(parents=True)
    settings.provider_config_file.write_text(
        "providers:\n  live:\n    discover_models: true\n    models: [seed]\n  fixed:\n    models: [static]\n"
    )
    settings.model_cache_file.write_text(
        json.dumps(
            {
                "live": {"models": ["discovered", "seed"]},
                "fixed": {"models": ["not-configured"]},
                "removed": {"models": ["injected"]},
            }
        )
    )
    assert hasattr(catalog, "load_routing_catalog"), "routing needs the same known discovered model IDs as the menu"
    router = ModelRouter(load_catalog=lambda: catalog.load_routing_catalog(app_settings=settings))
    assert router.route("live::discovered").model_id == "discovered"
    assert router.route("seed").provider_id == "live"
    with pytest.raises(RuntimeError):
        router.route("fixed::not-configured")
    with pytest.raises(RuntimeError):
        router.route("removed::injected")


@pytest.mark.parametrize(
    "spec",
    [
        {"models": ["model"]},
        {"models": ["model"], "api_endpoint": "not-a-url"},
        {"models": ["model"], "api_endpoint": "http://external.example/v1"},
        {"models": ["model"], "api_endpoint": "https://provider.example/v1", "transport": "unrecognized"},
    ],
)
def test_startup_rejects_invalid_provider_before_polling_even_with_credentials(tmp_path, spec):
    settings = load_app_settings(
        {"LLM_API_KEY": "private-fixture", "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "provider.example"}, home=tmp_path
    )
    router = ModelRouter(load_catalog=lambda: {"provider": spec})
    with pytest.raises(RuntimeError) as error:
        main.validate_startup_credential("provider::model", router, app_settings=settings)
    assert "private-fixture" not in str(error.value)


def test_check_does_not_register_bot_commands(tmp_path, monkeypatch):
    settings = load_app_settings({}, home=tmp_path)
    services = SimpleNamespace(config=settings)
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda *_a: argparse.Namespace(check=True))
    for name in (
        "bootstrap_environment",
        "validate_startup_credential",
        "enforce_runtime_permissions",
        "configure_logging",
        "_initialize_extensions",
    ):
        monkeypatch.setattr(main, name, lambda *_a, **_k: None)
    monkeypatch.setattr(main, "_load_startup_config", lambda *_a: settings)
    monkeypatch.setattr(main, "_build_startup_services", lambda *_a, **_k: services)
    monkeypatch.setattr(main, "run_check", lambda selected: 0 if selected is services else 1)
    monkeypatch.setattr(
        main, "set_bot_commands", lambda *_a: pytest.fail("--check must not mutate the Telegram command list")
    )
    monkeypatch.setattr(main, "run_bridge_runtime", lambda *_a: pytest.fail("--check must not poll"))
    assert main.main() == 0

"""Application configuration is constructed explicitly and cannot leak between instances."""

from __future__ import annotations

import dataclasses
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest


def loader():
    return importlib.import_module("bridge.settings").load_app_settings


def test_settings_are_frozen_snapshots_with_independent_paths(tmp_path):
    load = loader()
    supplied = {"SILLYTAVERN_MODEL": "one::model", "HINDSIGHT_API_KEY": "private-test-secret"}
    first = load(supplied, home=tmp_path / "one")
    second = load({"SILLYTAVERN_MODEL": "two::model"}, home=tmp_path / "two")
    supplied["SILLYTAVERN_MODEL"] = "changed"
    assert first.default_model == "one::model"
    assert second.default_model == "two::model"
    assert first.db_file != second.db_file
    assert first.db_file.is_relative_to(tmp_path / "one")
    assert second.db_file.is_relative_to(tmp_path / "two")
    assert "private-test-secret" not in repr(first)
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.default_model = "mutated"
    with pytest.raises(TypeError):
        first.environ["SILLYTAVERN_MODEL"] = "mutated"


def test_explicit_settings_override_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SILLYTAVERN_MODEL", "ambient::wrong")
    settings = loader()({"SILLYTAVERN_MODEL": "explicit::right"}, home=tmp_path)
    assert settings.default_model == "explicit::right"
    assert settings.environ.get("SILLYTAVERN_MODEL") == "explicit::right"


def test_config_and_main_import_do_not_read_application_environment():
    source = """
import os
class Guard(dict):
    def get(self, key, default=None):
        if key.startswith(("SILLYTAVERN_", "HINDSIGHT_")) or key == "LLM_API_KEY":
            raise AssertionError("application environment read during import: " + key)
        return super().get(key, default)
os.environ = Guard(os.environ)
import bridge.config
import bridge.main
"""
    result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_module_entrypoint_help_is_supported_without_configuration(tmp_path):
    env = dict(os.environ, SILLYTAVERN_ENV_FILE=str(tmp_path / "absent.env"))
    result = subprocess.run([sys.executable, "-m", "bridge.main", "--help"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--check" in result.stdout


def test_two_explicit_settings_choose_distinct_default_character_names(tmp_path, *, app_settings_builder):
    from bridge.card_content import card_fields

    load = loader()
    first = load({"SILLYTAVERN_DEFAULT_CHARACTER": "First.png"}, home=tmp_path / "one")
    second = load({"SILLYTAVERN_DEFAULT_CHARACTER": "Second.png"}, home=tmp_path / "two")
    assert card_fields({}, app_settings=first)["name"] == "First"
    assert card_fields({}, app_settings=second)["name"] == "Second"


def test_two_database_factories_do_not_share_configured_paths(tmp_path, *, app_settings_builder):
    from bridge.database import db_connect

    load = loader()
    first = load({}, home=tmp_path / "one")
    second = load({}, home=tmp_path / "two")
    one = db_connect(app_settings=first)
    two = db_connect(app_settings=second)
    try:
        one_path = Path(one.execute("PRAGMA database_list").fetchone()[2])
        two_path = Path(two.execute("PRAGMA database_list").fetchone()[2])
        assert one_path == first.db_file
        assert two_path == second.db_file
        assert one_path != two_path
    finally:
        one.close()
        two.close()


def test_numeric_settings_validate_during_load_not_import(tmp_path):
    from bridge.config_values import ConfigurationError

    with pytest.raises(ConfigurationError, match="SILLYTAVERN_RAG_MAX_PDF_PAGES"):
        loader()({"SILLYTAVERN_RAG_MAX_PDF_PAGES": "-1"}, home=tmp_path)


def test_native_persona_metadata_is_scoped_and_never_reuses_another_app_cache(tmp_path):
    import json

    from bridge.persona_sync import load_native_personas

    load = loader()
    settings = [load({"SILLYTAVERN_DIR": str(tmp_path / name)}, home=tmp_path / name) for name in ("one", "two")]
    for name, config in zip(("One", "Two"), settings, strict=True):
        config.native_persona_settings_file.parent.mkdir(parents=True)
        config.native_persona_settings_file.write_text(
            json.dumps(
                {
                    "power_user": {
                        "personas": {"avatar.png": name},
                        "persona_descriptions": {"avatar.png": {"description": name + " only"}},
                    },
                }
            )
        )
    assert load_native_personas(app_settings=settings[0])["avatar.png"]["name"] == "One"
    assert load_native_personas(app_settings=settings[1])["avatar.png"]["name"] == "Two"
    config = settings[0]
    config.native_persona_settings_file.write_text(
        json.dumps({"power_user": {"personas": {"avatar.png": "Changed"}, "persona_descriptions": {}}})
    )
    assert load_native_personas(app_settings=config)["avatar.png"]["name"] == "Changed"
    assert load_native_personas(app_settings=settings[1])["avatar.png"]["name"] == "Two"


def test_native_api_clients_have_instance_credentials_timeout_and_cookie_jars(tmp_path):
    from bridge.sillytavern_api import live_sync_client

    load = loader()
    first = load(
        {
            "SILLYTAVERN_SYNC_API_URL": "http://localhost:8111",
            "SILLYTAVERN_SYNC_API_PASSWORD": "first-secret",
            "SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS": "4",
        },
        home=tmp_path / "one",
    )
    second = load(
        {
            "SILLYTAVERN_SYNC_API_URL": "http://localhost:8222",
            "SILLYTAVERN_SYNC_API_PASSWORD": "second-secret",
            "SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS": "9",
        },
        home=tmp_path / "two",
    )
    one, two = live_sync_client(app_settings=first), live_sync_client(app_settings=second)
    assert (one.base_url, one.password, one.timeout) == ("http://localhost:8111", "first-secret", 4)
    assert (two.base_url, two.password, two.timeout) == ("http://localhost:8222", "second-secret", 9)
    assert one.cookies is not two.cookies


def test_startup_provider_ports_capture_their_own_credentials_and_policy(tmp_path, monkeypatch):
    import io
    import json
    from concurrent.futures import ThreadPoolExecutor

    import bridge.provider_transport as transport
    from bridge.main import _build_startup_services
    from bridge.model_router import ModelRouter

    load = loader()
    catalog = {
        "fixture": {
            "api_endpoint": "https://provider.example/v1",
            "transport": "chat_completions",
            "api_key_env": "FIXTURE_PROVIDER_KEY",
            "models": ["model"],
        }
    }
    configs = [
        load(
            {
                "SILLYTAVERN_MODEL": "fixture::model",
                "FIXTURE_PROVIDER_KEY": secret,
                "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "provider.example",
            },
            home=tmp_path / name,
        )
        for name, secret in (("one", "first-private"), ("two", "second-private"))
    ]
    services = [
        _build_startup_services(config, model_router=ModelRouter(load_catalog=lambda: catalog)) for config in configs
    ]
    observed = []

    def fake_open(request, timeout, *, environ):
        observed.append((request.get_header("Authorization"), environ["FIXTURE_PROVIDER_KEY"]))
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}).encode())

    monkeypatch.setattr(transport, "strict_urlopen", fake_open)
    monkeypatch.setenv("FIXTURE_PROVIDER_KEY", "unrelated-global-value")

    def generate(service):
        return service.provider.generate("", "fixture::model", [{"role": "user", "content": "test"}])

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(generate, services)) == ["OK", "OK"]
    assert set(observed) == {("Bearer first-private", "first-private"), ("Bearer second-private", "second-private")}
    assert configs[0].environ["FIXTURE_PROVIDER_KEY"] == "first-private"


def test_current_config_module_contains_fixed_limits_only():
    import bridge.config as config
    from bridge.settings import AppSettings

    for name in ("DB_FILE", "LOG_FILE", "DEFAULT_MODEL", "CHARACTER_DIR", "BRIDGE_HOME", "PROVIDER_CONFIG_FILE"):
        assert not hasattr(config, name)
        assert name.lower() in AppSettings.__dataclass_fields__
    assert config.CATALOG_MAX_ITEMS == 40


def test_no_hidden_configuration_context_or_module_setter_is_introduced():
    import ast

    source = Path("bridge/settings.py").read_text()
    tree = ast.parse(source)
    assert not any(isinstance(node, ast.Global) for node in ast.walk(tree))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert "contextvars" not in imports
    assert "threading" not in imports
    assert "os" not in imports

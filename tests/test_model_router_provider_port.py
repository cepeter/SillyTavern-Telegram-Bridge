from __future__ import annotations

import ast
from dataclasses import MISSING
import importlib
import inspect
from pathlib import Path

import pytest

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


def test_model_router_is_pure_and_routes_qualified_unqualified_and_slash_models():
    path = BRIDGE / "model_router.py"
    assert path.is_file(), "ModelRouter module is missing"
    module = importlib.import_module("bridge.model_router")
    assert not any(
        name == "bridge" or name.startswith("bridge.")
        for name in imported_modules("model_router.py")
    )
    catalog = {
        "alpha": {"models": ["alpha-one", "shared/model"]},
        "beta": {"models": ["beta-one", "model-x"]},
    }
    router = module.ModelRouter(load_catalog=lambda: catalog)

    qualified = router.route("beta::beta-one")
    assert (qualified.provider_id, qualified.model_id) == ("beta", "beta-one")
    assert dict(qualified.spec) == catalog["beta"]

    direct = router.route("alpha-one")
    assert (direct.provider_id, direct.model_id) == ("alpha", "alpha-one")

    slash = router.route("vendor/model-x")
    assert (slash.provider_id, slash.model_id) == ("beta", "vendor/model-x")


def test_model_router_falls_back_when_catalog_loader_fails_or_model_is_unknown():
    module = importlib.import_module("bridge.model_router")

    def broken():
        raise OSError("catalog unavailable")

    failed = module.ModelRouter(load_catalog=broken).route("mystery")
    assert failed.provider_id == "provider-one"
    assert failed.model_id == "mystery"
    assert dict(failed.spec) == {}

    unknown = module.ModelRouter(load_catalog=lambda: {"other": {"models": ["known"]}}).route("mystery")
    assert unknown.provider_id == "provider-one"
    assert unknown.model_id == "mystery"
    assert dict(unknown.spec) == {}


def test_provider_port_is_pure_and_delegates_exact_call_shape():
    path = BRIDGE / "provider_port.py"
    assert path.is_file(), "ProviderPort module is missing"
    module = importlib.import_module("bridge.provider_port")
    assert not any(
        name == "bridge" or name.startswith("bridge.")
        for name in imported_modules("provider_port.py")
    )
    calls = []

    def backend(*args, **kwargs):
        calls.append((args, kwargs))
        return "visible"

    port = module.ProviderPort(generate_backend=backend)
    callback = object()
    cancel = object()
    result = port.generate(
        "key",
        "alpha::model",
        [{"role": "user", "content": "hello"}],
        session_id="session",
        settings={"max_tokens": 3},
        stream_callback=callback,
        cancel_event=cancel,
        force_non_stream=True,
        request_timeout=12.5,
    )
    assert result == "visible"
    assert calls == [(
        ("key", "alpha::model", [{"role": "user", "content": "hello"}]),
        {
            "session_id": "session",
            "settings": {"max_tokens": 3},
            "stream_callback": callback,
            "cancel_event": cancel,
            "force_non_stream": True,
            "request_timeout": 12.5,
        },
    )]


def test_provider_catalog_returns_empty_mapping_on_read_failure(tmp_path):
    module = importlib.import_module("bridge.provider_catalog")
    assert module.load_provider_catalog(tmp_path / "missing.yaml") == {}


def test_model_router_and_provider_are_required_by_composition():
    from bridge.composition import BridgeServices, build_bridge_services

    for name in ("model_router", "provider"):
        field = BridgeServices.__dataclass_fields__[name]
        assert field.default is MISSING
        assert "None" not in str(field.type)
        param = inspect.signature(build_bridge_services).parameters[name]
        assert param.default is inspect.Parameter.empty


def test_startup_composes_model_router_and_provider_port():
    source = (BRIDGE / "main.py").read_text(encoding="utf-8")
    assert "_ModelRouter(" in source
    assert "_ProviderPort(" in source
    assert "load_catalog=load_provider_catalog" in source

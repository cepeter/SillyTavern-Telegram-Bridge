from __future__ import annotations

import ast
from dataclasses import MISSING
import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace

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


def test_input_flow_service_is_pure_and_delegates():
    path = BRIDGE / "input_flow_service.py"
    assert path.is_file(), "InputFlowService module is missing"
    module = importlib.import_module("bridge.input_flow_service")
    assert not any(
        name == "bridge" or name.startswith("bridge.")
        for name in imported_modules("input_flow_service.py")
    )
    calls = []

    def backend(*args, **kwargs):
        calls.append((args, kwargs))
        return True

    session_handler = object()
    service = module.InputFlowService(
        handle_pending_backend=backend,
        start_session_name_backend=lambda *_args, **_kwargs: None,
        start_text_action_backend=lambda *_args, **_kwargs: None,
        handle_session_name_backend=session_handler,
    )
    assert service.handle_pending(1, 2, key="value") is True
    assert calls == [
        (
            (1, 2),
            {
                "handle_session_name": session_handler,
                "key": "value",
            },
        )
    ]


def test_input_flow_service_is_required_by_composition():
    from bridge.composition import BridgeServices, build_bridge_services

    field = BridgeServices.__dataclass_fields__["input_flow"]
    assert field.default is MISSING
    assert "None" not in str(field.type)
    parameter = inspect.signature(build_bridge_services).parameters["input_flow"]
    assert parameter.default is inspect.Parameter.empty


def test_message_commands_no_longer_imports_input_flows():
    assert "bridge.input_flows" not in imported_modules("message_commands.py")


def test_startup_composes_canonical_pending_handler():
    source = (BRIDGE / "main.py").read_text(encoding="utf-8")
    assert "_InputFlowService(" in source
    assert "handle_pending_backend=handle_pending_input" in source


def test_prepare_message_forwards_pending_context_through_service(monkeypatch):
    import bridge.message_commands as message_commands
    module = importlib.import_module("bridge.input_flow_service")

    captured = []
    db = object()
    fields = {"name": "Mira"}
    session = {
        "session_id": "session",
        "character_file": "mira.png",
        "model_id": "model",
        "persona_id": "",
    }
    group = object()
    provider = object()
    memory = object()
    persona = object()
    input_flow = module.InputFlowService(
        handle_pending_backend=lambda *args, **kwargs: (
            captured.append((args, kwargs)) or True
        ),
        start_session_name_backend=lambda *_args, **_kwargs: None,
        start_text_action_backend=lambda *_args, **_kwargs: None,
        handle_session_name_backend=lambda *_args, **_kwargs: False,
    )
    services = SimpleNamespace(
        input_flow=input_flow,
        group=group,
        provider=provider,
        memory=memory,
        persona=persona,
    )

    monkeypatch.setattr(
        message_commands,
        "ensure_session",
        lambda *_args, **_kwargs: session,
    )

    result = message_commands.prepare_message(
        db,
        "token",
        "api-key",
        "model",
        fields,
        "chat",
        "pending text",
        services=services,
    )

    assert result is None
    assert len(captured) == 1
    args, kwargs = captured[0]
    assert args == (db, "token", "chat", session, "pending text")
    assert kwargs["api_key"] == "api-key"
    assert kwargs["fields"] is fields
    assert kwargs["operation_id"] is None
    assert kwargs["group_service"] is group
    assert kwargs["provider_port"] is provider
    assert kwargs["memory_service"] is memory
    assert kwargs["persona_service"] is persona
    assert kwargs["request_context"].db is db
    assert kwargs["request_context"].session_id == "session"

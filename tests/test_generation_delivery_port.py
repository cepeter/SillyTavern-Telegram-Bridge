from __future__ import annotations

import ast
from dataclasses import MISSING
import importlib
import inspect
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


def test_delivery_port_is_pure_and_declares_required_callables():
    path = BRIDGE / "delivery_port.py"
    assert path.is_file(), "DeliveryPort module is missing"
    module = importlib.import_module("bridge.delivery_port")
    assert not any(
        name == "bridge" or name.startswith("bridge.")
        for name in imported_modules("delivery_port.py")
    )
    assert set(module.DeliveryPort.__dataclass_fields__) == {
        "request",
        "send_text",
        "send_reply",
        "send_typing",
        "send_panel_request",
        "delete_outgoing_message_row",
    }


def test_delivery_port_is_required_by_composition():
    from bridge.composition import BridgeServices, build_bridge_services

    field = BridgeServices.__dataclass_fields__["delivery"]
    assert field.default is MISSING
    assert "None" not in str(field.type)
    param = inspect.signature(build_bridge_services).parameters["delivery"]
    assert param.default is inspect.Parameter.empty


def test_startup_binds_delivery_port_to_canonical_concrete_owners():
    source = (BRIDGE / "main.py").read_text(encoding="utf-8")
    assert "_DeliveryPort(" in source
    for binding in (
        "request=telegram_request",
        "send_text=send_text",
        "send_reply=send_reply",
        "send_typing=send_typing",
        "send_panel_request=send_panel_request",
        "delete_outgoing_message_row=delete_outgoing_message_row",
    ):
        assert binding in source

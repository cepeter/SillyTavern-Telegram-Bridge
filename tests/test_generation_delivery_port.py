from __future__ import annotations

import ast
import importlib
import inspect
from dataclasses import MISSING
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
    assert not any(name == "bridge" or name.startswith("bridge.") for name in imported_modules("delivery_port.py"))
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
        "send_reply=_partial(send_reply, app_settings=config)",
        "send_typing=send_typing",
        "send_panel_request=send_panel_request",
        "delete_outgoing_message_row=delete_outgoing_message_row",
    ):
        assert binding in source


def test_generation_imports_no_concrete_delivery_modules():
    imports = imported_modules("generation.py")
    assert "bridge.media" not in imports
    assert "bridge.telegram" not in imports


def test_generation_has_no_module_global_recovery_binding():
    tree = ast.parse((BRIDGE / "generation.py").read_text(encoding="utf-8"))
    assigned = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "_GENERATION_OPERATION_RECOVERY" not in assigned


def test_regen_and_continue_require_delivery_port(*, app_settings_builder):
    import bridge.generation as generation

    for fn in (generation.regenerate_last, generation.continue_last):
        param = inspect.signature(fn).parameters.get("delivery_port")
        assert param is not None
        assert param.default is inspect.Parameter.empty


def test_recovery_factory_binds_exact_delivery_collaborators():
    from application_test_setup import make_test_delivery_port

    import bridge.generation as generation

    def request(*_args, **_kwargs):
        return {}

    def delete(*_args, **_kwargs):
        return None

    port = make_test_delivery_port(
        request=request,
        delete_outgoing_message_row=delete,
    )
    recovery = generation._generation_operation_recovery(port)
    assert recovery.telegram_request is request
    assert recovery.delete_outgoing_message_row is delete

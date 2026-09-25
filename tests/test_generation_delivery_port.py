from __future__ import annotations

import ast
import importlib
import inspect
from dataclasses import MISSING
from pathlib import Path

import bridge.continuation as _owner_continuation
import bridge.generation_recovery as _owner_generation_recovery
import bridge.regeneration as _owner_regeneration

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
    assert {
        name for name in imported_modules("delivery_port.py") if name == "bridge" or name.startswith("bridge.")
    } <= {"bridge.port_contracts"}
    assert set(module.DeliveryPort.__dataclass_fields__) == {
        "request",
        "send_text",
        "send_reply",
        "send_typing",
        "send_panel_request",
        "delete_outgoing_message_row",
    }


def test_delivery_port_is_required_by_composition():
    from bridge.composition import BridgeServices

    field = BridgeServices.__dataclass_fields__["delivery"]
    assert field.default is MISSING
    assert "None" not in str(field.type)
    param = inspect.signature(BridgeServices).parameters["delivery"]
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
    imports = set().union(
        imported_modules("continuation.py"),
        imported_modules("generation.py"),
        imported_modules("generation_recovery.py"),
        imported_modules("regeneration.py"),
        imported_modules("response_variants.py"),
        imported_modules("swipe_panels.py"),
    )
    assert "bridge.media" not in imports
    assert "bridge.telegram" not in imports


def test_generation_has_no_module_global_recovery_binding():
    tree = ast.parse(
        "\n".join(
            (
                (BRIDGE / "continuation.py").read_text(encoding="utf-8"),
                (BRIDGE / "generation.py").read_text(encoding="utf-8"),
                (BRIDGE / "generation_recovery.py").read_text(encoding="utf-8"),
                (BRIDGE / "regeneration.py").read_text(encoding="utf-8"),
                (BRIDGE / "response_variants.py").read_text(encoding="utf-8"),
                (BRIDGE / "swipe_panels.py").read_text(encoding="utf-8"),
            )
        )
    )
    assigned = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "_GENERATION_OPERATION_RECOVERY" not in assigned


def test_regen_and_continue_require_delivery_port(*, app_settings_builder):

    for fn in (_owner_regeneration.regenerate_last, _owner_continuation.continue_last):
        param = inspect.signature(fn).parameters.get("delivery_port")
        assert param is not None
        assert param.default is inspect.Parameter.empty


def test_recovery_factory_binds_exact_delivery_collaborators():
    from application_test_setup import make_test_delivery_port

    def request(*_args, **_kwargs):
        return {}

    def delete(*_args, **_kwargs):
        return None

    port = make_test_delivery_port(
        request=request,
        delete_outgoing_message_row=delete,
    )
    recovery = _owner_generation_recovery._generation_operation_recovery(port)
    assert recovery.telegram_request is request
    assert recovery.delete_outgoing_message_row is delete

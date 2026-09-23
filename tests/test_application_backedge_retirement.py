from __future__ import annotations

import ast
from pathlib import Path

from application_test_setup import make_native_test_persona_service

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


def test_persona_sync_does_not_import_input_flows():
    assert "bridge.input_flows" not in imported_modules("persona_sync.py")


def test_input_flows_does_not_own_persona_edit_lock():
    import bridge.input_flows as input_flows

    assert not hasattr(input_flows, "PERSONA_EDIT_LOCK")


def test_native_persona_store_and_service_share_canonical_lock():
    import bridge.persona_sync as persona_sync

    service = make_native_test_persona_service()
    assert persona_sync._PERSONA_STORE.edit_lock() is persona_sync.PERSONA_EDIT_LOCK
    assert service.persona_edit_lock() is persona_sync.PERSONA_EDIT_LOCK

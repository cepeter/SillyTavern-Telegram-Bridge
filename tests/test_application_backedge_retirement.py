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


def test_session_title_contract_has_pure_owner():
    import importlib
    import bridge.session_naming as session_naming

    owner = BRIDGE / "session_titles.py"
    assert owner.is_file()
    session_titles = importlib.import_module("bridge.session_titles")
    assert session_titles.SESSION_TITLE_MAX_CHARS == 80
    assert not hasattr(session_naming, "SESSION_TITLE_MAX_CHARS")
    assert not hasattr(session_naming, "normalize_session_title")
    assert "bridge.session_naming" not in imported_modules("telegram.py")


def test_session_title_contract_behavior_is_unchanged():
    import importlib
    import pytest

    owner = BRIDGE / "session_titles.py"
    assert owner.is_file()
    normalize = importlib.import_module("bridge.session_titles").normalize_session_title
    assert normalize(" A   name ") == "A name"
    with pytest.raises(ValueError, match="Session name must contain 1–80"):
        normalize("x" * 81)
    with pytest.raises(ValueError, match="cannot start with /"):
        normalize("/bad")

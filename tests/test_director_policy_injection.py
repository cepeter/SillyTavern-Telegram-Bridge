from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path


ROOT = Path(__file__).parents[1]
BRIDGE = ROOT / "bridge"


def top_level_functions(filename: str) -> set[str]:
    tree = ast.parse((BRIDGE / filename).read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def top_level_classes(filename: str) -> set[str]:
    tree = ast.parse((BRIDGE / filename).read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }


def imported_names(filename: str) -> set[str]:
    tree = ast.parse((BRIDGE / filename).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def test_extension_registry_has_no_director_single_provider_slot():
    import bridge.extension_registry as registry

    assert "DirectorCustomization" not in top_level_classes(
        "extension_registry.py"
    )
    functions = top_level_functions("extension_registry.py")
    assert "register_director_customization_provider" not in functions
    assert "get_director_customization" not in functions

    registry.reset_extension_registry()
    snapshot = registry.extension_registry_snapshot()
    assert "director_customization" not in snapshot


def test_group_director_service_owns_typed_policy_contract():
    import bridge.group_director_service as service_module

    assert hasattr(service_module, "DirectorCustomization")
    assert hasattr(service_module, "DirectorPolicy")

    field_names = {
        field.name
        for field in fields(service_module.GroupDirectorService)
    }
    assert "director_policy" in field_names
    assert "director_customization" not in field_names

    customization = service_module.DirectorCustomization(
        model="director-model",
        hidden_instructions="hidden",
        max_tokens=220,
        speaker_context="context",
    )
    assert customization.model == "director-model"
    assert customization.hidden_instructions == "hidden"
    assert customization.max_tokens == 220
    assert customization.speaker_context == "context"


def test_director_goals_exposes_direct_policy_without_registry_slot(monkeypatch):
    import bridge.director_goals as goals
    from bridge.group_director_service import DirectorCustomization

    assert "director_goal_policy" in top_level_functions("director_goals.py")
    assert "_director_goal_customization" not in top_level_functions(
        "director_goals.py"
    )

    source = (BRIDGE / "director_goals.py").read_text(encoding="utf-8")
    assert "register_director_customization_provider" not in source
    assert "DirectorCustomization as _DirectorCustomization" not in source

    monkeypatch.setattr(
        goals,
        "get_director_goal",
        lambda *_args, **_kwargs: "Protect the witness",
    )
    monkeypatch.setattr(
        goals,
        "task_model_for_session",
        lambda *_args, **_kwargs: "director-model",
    )

    result = goals.director_goal_policy(
        object(),
        "chat",
        {"session_id": "session"},
    )

    assert isinstance(result, DirectorCustomization)
    assert result.model == "director-model"
    assert result.max_tokens == 220
    assert "Protect the witness" in result.hidden_instructions
    assert "Protect the witness" in result.speaker_context


def test_director_goal_extension_registration_only_registers_command_route():
    import bridge.application_composition as composition
    import bridge.extension_registry as registry

    composition.initialize_extensions()
    snapshot = registry.extension_registry_snapshot()

    assert "director_customization" not in snapshot
    assert "director_goals" in snapshot["command_routes"]


def test_main_injects_director_goal_policy_directly():
    source = (BRIDGE / "main.py").read_text(encoding="utf-8")

    assert "get_director_customization" not in source
    assert "director_goal_policy" in source
    assert "director_policy=director_goal_policy" in source


def test_group_director_service_stays_bridge_independent():
    source = (BRIDGE / "group_director_service.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("bridge")
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("bridge")

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
BRIDGE = ROOT / "bridge"
POLICY = ROOT / "tools" / "static_analysis.py"


def load_policy():
    assert POLICY.is_file(), "tools/static_analysis.py is missing"
    spec = importlib.util.spec_from_file_location(
        "repo_static_analysis",
        POLICY,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_module(root: Path, name: str, source: str) -> None:
    path = root / (name + ".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_real_repository_dependency_policy_passes():
    policy = load_policy()
    report = policy.check_dependency_direction(BRIDGE)
    assert report.errors == ()
    assert report.cyclic_modules == 0
    assert report.reciprocal_pairs == 0


def test_synthetic_cycle_is_rejected(tmp_path):
    policy = load_policy()
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    write_module(bridge, "a", "import bridge.b\n")
    write_module(bridge, "b", "import bridge.a\n")

    report = policy.check_dependency_direction(
        bridge,
        static_targets=(),
    )

    assert report.cyclic_modules == 2
    assert any("cycle" in error.casefold() for error in report.errors)


def test_function_local_import_participates_in_cycle_detection(tmp_path):
    policy = load_policy()
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    write_module(
        bridge,
        "a",
        "def load():\n    import bridge.b\n    return bridge.b\n",
    )
    write_module(bridge, "b", "import bridge.a\n")

    report = policy.check_dependency_direction(
        bridge,
        static_targets=(),
    )

    assert report.cyclic_modules == 2
    assert report.reciprocal_pairs == 1


def test_isolated_static_target_cannot_import_bridge_module(tmp_path):
    policy = load_policy()
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    write_module(bridge, "service", "import bridge.adapter\n")
    write_module(bridge, "adapter", "")

    report = policy.check_dependency_direction(
        bridge,
        static_targets=("bridge/service.py",),
    )

    assert any(
        "isolated" in error.casefold() and "bridge.service" in error and "bridge.adapter" in error
        for error in report.errors
    )


def test_missing_static_target_is_rejected(tmp_path):
    policy = load_policy()
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    write_module(bridge, "service", "")

    report = policy.check_dependency_direction(
        bridge,
        static_targets=("bridge/missing.py",),
    )

    assert any("missing" in error.casefold() and "bridge/missing.py" in error for error in report.errors)


def test_static_target_manifest_is_expected_service_port_surface():
    policy = load_policy()

    assert set(policy.STATIC_TARGETS) == {
        "bridge/conversation_service.py",
        "bridge/delivery_port.py",
        "bridge/group_director_service.py",
        "bridge/group_service.py",
        "bridge/input_flow_service.py",
        "bridge/job_service.py",
        "bridge/memory_service.py",
        "bridge/model_router.py",
        "bridge/persona_service.py",
        "bridge/provider_port.py",
        "bridge/sync_service.py",
        "bridge/session_service.py",
    }


def test_ci_requires_static_architecture_gates():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "  static-analysis:" in workflow
    assert "python tools/static_analysis.py" in workflow
    assert "python -m ruff check ." in workflow
    assert "python -m ruff format --check ." in workflow
    assert ("python tools/static_analysis.py --print-type-targets | xargs python -m mypy") in workflow


def test_isolated_services_may_depend_on_pure_contracts(tmp_path):
    policy = load_policy()
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    write_module(bridge, "service", "from bridge.port_contracts import Port\n")
    write_module(bridge, "port_contracts", "class Port: pass\n")
    report = policy.check_dependency_direction(bridge, static_targets=("bridge/service.py",))
    assert report.errors == ()


def test_contract_layer_cannot_smuggle_concrete_adapter_imports(tmp_path):
    policy = load_policy()
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    write_module(bridge, "service", "from bridge.port_contracts import Port\n")
    write_module(bridge, "port_contracts", "import bridge.telegram\nclass Port: pass\n")
    write_module(bridge, "telegram", "")
    report = policy.check_dependency_direction(bridge, static_targets=("bridge/service.py",))
    assert any("contract" in error.casefold() and "bridge.telegram" in error for error in report.errors)


def test_low_level_owner_cannot_import_application_adapter(tmp_path):
    policy = load_policy()
    for name in ("topic_scope", "limits", "runtime_logging", "background", "sqlite_store"):
        bridge = tmp_path / name / "bridge"
        bridge.mkdir(parents=True)
        write_module(bridge, name, "def lazy():\n    import bridge.telegram\n")
        write_module(bridge, "telegram", "")
        report = policy.check_dependency_direction(bridge, static_targets=())
        assert any(name in error and "bridge.telegram" in error for error in report.errors), name


def test_sqlite_mechanics_cannot_depend_on_database_operations(tmp_path):
    policy = load_policy()
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    write_module(bridge, "sqlite_store", "import bridge.database\n")
    write_module(bridge, "database", "")
    report = policy.check_dependency_direction(bridge, static_targets=())
    assert any("sqlite_store" in error and "bridge.database" in error for error in report.errors)


def test_panel_owner_cannot_import_root_command_dispatch(tmp_path):
    policy = load_policy()
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    write_module(bridge, "command_panels", "import bridge.command_routes\n")
    write_module(bridge, "command_routes", "")
    report = policy.check_dependency_direction(bridge, static_targets=())
    assert any("command_panels" in error and "command_routes" in error for error in report.errors)


def owner_definitions(filename: str) -> set[str]:
    path = BRIDGE / filename
    assert path.is_file(), f"canonical owner missing: {filename}"
    return {node.name for node in ast.parse(path.read_text()).body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


def test_shared_runtime_bag_is_retired():
    assert not (BRIDGE / "common.py").exists()


def test_topic_logging_and_scheduler_owners_are_separate():
    assert {"topic_scope_id", "parse_topic_scope", "topic_scope_from_message"} <= owner_definitions("topic_scope.py")
    assert {"configure_logging", "enforce_runtime_permissions"} <= owner_definitions("runtime_logging.py")
    assert {"submit_chat_background", "submit_background", "shutdown_background_executors"} <= owner_definitions(
        "background.py"
    )


def test_sqlite_mechanics_have_one_owner():
    expected = {
        "db_connect",
        "_lightweight_db_connect",
        "_open_initialized_database",
        "write_transaction",
        "run_database_maintenance",
    }
    assert expected <= owner_definitions("sqlite_store.py")
    assert not (BRIDGE / "database.py").exists()
    for path in BRIDGE.glob("*_repository.py"):
        assert not expected & owner_definitions(path.name)


def test_grouped_panel_commands_are_not_owned_by_root_dispatch():
    expected = {"_handle_panels", "_handle_generation_panels", "_handle_memory_media", "_handle_voice_panels"}
    assert expected <= owner_definitions("command_panels.py")
    assert not expected & owner_definitions("command_routes.py")


def test_resource_limits_have_one_data_only_owner():
    source = BRIDGE / "limits.py"
    assert source.is_file()
    tree = ast.parse(source.read_text())
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom, ast.Call)) for node in ast.walk(tree))
    assigned = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert {
        "IMAGE_MAX_BYTES",
        "SYNC_MAX_BYTES",
        "CATALOG_MAX_ITEMS",
        "RAG_MAX_FILE_BYTES",
        "MAX_TELEGRAM_LENGTH",
        "_BACKGROUND_MAX_QUEUED_PER_CHAT",
    } <= assigned


def test_ci_lints_complete_tree():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "python -m ruff check ." in workflow
    assert "xargs python -m ruff check" not in workflow


def test_ci_checks_formatting():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "python -m ruff format --check ." in workflow


def test_bug_and_security_rules_enabled():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert {"E4", "E5", "E7", "E9", "F", "I", "B", "S", "RUF"} <= set(config["tool"]["ruff"]["lint"]["select"])


def test_type_gate_includes_security_modules_separately_from_layer_rule():
    policy = load_policy()
    assert {"bridge/network_security.py", "bridge/callback_tokens.py"} <= set(policy.TYPE_TARGETS)
    assert "bridge/network_security.py" not in policy.STATIC_TARGETS


def test_quality_manifest_cli_and_graph_cli_work():
    command = [sys.executable, str(POLICY)]
    typed = subprocess.run([*command, "--print-type-targets"], capture_output=True, text=True)
    assert typed.returncode == 0, typed.stderr
    assert "bridge/network_security.py" in typed.stdout.splitlines()
    graph = subprocess.run(command, capture_output=True, text=True)
    assert graph.returncode == 0, graph.stderr
    assert "cyclic_modules=0" in graph.stdout


def test_session_repository_cannot_import_adapter_or_service(tmp_path):
    policy = load_policy()
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    write_module(bridge, "session_repository", "import bridge.telegram\n")
    write_module(bridge, "telegram", "")
    report = policy.check_dependency_direction(bridge, static_targets=())
    assert any("session_repository" in error and "bridge.telegram" in error for error in report.errors)


def test_telegram_transport_cannot_reabsorb_session_or_import_owners(tmp_path):
    policy = load_policy()
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    write_module(bridge, "telegram", "import bridge.session_core\nimport bridge.native_imports\n")
    write_module(bridge, "session_core", "")
    write_module(bridge, "native_imports", "")
    report = policy.check_dependency_direction(bridge, static_targets=())
    assert any("session_core" in error for error in report.errors)
    assert any("native_imports" in error for error in report.errors)


def test_any_domain_repository_cannot_import_use_cases_or_commit(tmp_path):
    policy = load_policy()
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    write_module(bridge, "new_repository", "import bridge.metadata\ndef write(db):\n    db.commit()\n")
    write_module(bridge, "metadata", "")
    report = policy.check_dependency_direction(bridge, static_targets=())
    assert any("new_repository" in error and "metadata" in error for error in report.errors)
    assert any("new_repository" in error and "commit" in error for error in report.errors)

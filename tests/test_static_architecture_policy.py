from __future__ import annotations

import importlib.util
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
        "def load():\n"
        "    import bridge.b\n"
        "    return bridge.b\n",
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
        "isolated" in error.casefold()
        and "bridge.service" in error
        and "bridge.adapter" in error
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

    assert any(
        "missing" in error.casefold()
        and "bridge/missing.py" in error
        for error in report.errors
    )


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
    }


def test_ci_requires_static_architecture_gates():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "  static-analysis:" in workflow
    assert "python tools/static_analysis.py" in workflow
    assert (
        "python tools/static_analysis.py --print-targets "
        "| xargs python -m ruff check"
    ) in workflow
    assert (
        "python tools/static_analysis.py --print-targets "
        "| xargs python -m mypy"
    ) in workflow

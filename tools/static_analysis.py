"""Static architecture policy for the bridge repository."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
from typing import NamedTuple, Sequence

STATIC_TARGETS: tuple[str, ...] = (
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
)


# Type coverage grows independently of the deliberately isolated service layer.
TYPE_TARGETS: tuple[str, ...] = (
    *STATIC_TARGETS,
    "bridge/session_repository.py",
    "bridge/repository_contracts.py",
    "bridge/network_security.py",
    "bridge/callback_tokens.py",
    "bridge/config_values.py",
    "bridge/environment.py",
    "bridge/self_update.py",
    "bridge/settings.py",
    "bridge/port_contracts.py",
    "bridge/request_types.py",
    "bridge/composition.py",
    "bridge/topic_scope.py",
    "bridge/runtime_logging.py",
    "bridge/limits.py",
    "tools/check_dependency_lock.py",
)


PURE_CONTRACT_IMPORTS = {
    "bridge.port_contracts": frozenset({"bridge.request_types"}),
    "bridge.request_types": frozenset({"bridge.settings"}),
    "bridge.settings": frozenset({"bridge.config_values"}),
    "bridge.config_values": frozenset(),
}
SERVICE_CONTRACT_IMPORTS = frozenset({"bridge.port_contracts", "bridge.request_types"})


LOW_LEVEL_IMPORTS = {
    "bridge.repository_contracts": frozenset(),
    "bridge.session_repository": frozenset({"bridge.repository_contracts"}),
    "bridge.topic_scope": frozenset(),
    "bridge.limits": frozenset(),
    "bridge.config": frozenset({"bridge.limits"}),
    "bridge.background": frozenset({"bridge.limits"}),
    "bridge.runtime_logging": frozenset({"bridge.settings"}),
    "bridge.sqlite_store": frozenset({"bridge.limits", "bridge.settings", "bridge.schema", "bridge.scheduler_safety"}),
}
CALLBACK_DOMAIN_MODULES = frozenset(
    {
        "bridge.settings_callbacks",
        "bridge.conversation_callbacks",
        "bridge.sync_callbacks",
        "bridge.character_callbacks",
        "bridge.session_callbacks",
        "bridge.world_callbacks",
        "bridge.provider_callbacks",
    }
)
FORBIDDEN_OWNER_IMPORTS = {
    "bridge.command_panels": frozenset({"bridge.command_routes"}),
    "bridge.telegram": frozenset(
        {"bridge.session_core", "bridge.session_repository", "bridge.native_imports", "bridge.session_panels"}
    ),
}


class DependencyReport(NamedTuple):
    modules: int
    edges: int
    cyclic_modules: int
    reciprocal_pairs: int
    cyclic_components: tuple[tuple[str, ...], ...]
    errors: tuple[str, ...]


def _module_name(bridge_root: Path, path: Path) -> str:
    relative = path.relative_to(bridge_root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    prefix = bridge_root.name
    return ".".join([prefix, *parts]) if parts else prefix


def _discover_modules(bridge_root: Path) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in sorted(bridge_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _module_name(bridge_root, path)
        modules[module] = path
    return modules


def _absolute_import_targets(
    node: ast.Import | ast.ImportFrom,
    *,
    current_module: str,
    package_name: str,
) -> set[str]:
    targets: set[str] = set()

    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name == package_name or alias.name.startswith(package_name + "."):
                targets.add(alias.name)
        return targets

    module = node.module or ""

    if node.level == 0:
        if module == package_name:
            for alias in node.names:
                if alias.name != "*":
                    targets.add(f"{package_name}.{alias.name}")
        elif module.startswith(package_name + "."):
            targets.add(module)
        return targets

    current_parts = current_module.split(".")
    package_parts = current_parts[:-1]
    ascend = max(0, node.level - 1)
    if ascend:
        package_parts = package_parts[:-ascend]

    if module:
        base_parts = [*package_parts, *module.split(".")]
        targets.add(".".join(base_parts))
    else:
        for alias in node.names:
            if alias.name != "*":
                targets.add(".".join([*package_parts, alias.name]))
    return targets


def _graph(bridge_root: Path) -> dict[str, set[str]]:
    modules = _discover_modules(bridge_root)
    module_names = set(modules)
    package_name = bridge_root.name
    graph: dict[str, set[str]] = {module: set() for module in module_names}

    for module, path in modules.items():
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for target in _absolute_import_targets(
                node,
                current_module=module,
                package_name=package_name,
            ):
                if target in module_names and target != module:
                    graph[module].add(target)
    return graph


def _strongly_connected_components(
    graph: dict[str, set[str]],
) -> list[tuple[str, ...]]:
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    components: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for target in sorted(graph[node]):
            if target not in indexes:
                visit(target)
                lowlinks[node] = min(
                    lowlinks[node],
                    lowlinks[target],
                )
            elif target in on_stack:
                lowlinks[node] = min(
                    lowlinks[node],
                    indexes[target],
                )

        if lowlinks[node] != indexes[node]:
            return

        component: list[str] = []
        while True:
            target = stack.pop()
            on_stack.remove(target)
            component.append(target)
            if target == node:
                break
        components.append(tuple(sorted(component)))

    for module in sorted(graph):
        if module not in indexes:
            visit(module)

    return sorted(
        components,
        key=lambda component: (-len(component), component),
    )


def _target_module(target: str) -> str:
    path = Path(target)
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def check_dependency_direction(
    bridge_root: Path,
    *,
    static_targets: Sequence[str] = STATIC_TARGETS,
) -> DependencyReport:
    bridge_root = Path(bridge_root)
    graph = _graph(bridge_root)
    components = _strongly_connected_components(graph)
    cyclic_components = tuple(component for component in components if len(component) > 1)
    cyclic_modules = sum(len(component) for component in cyclic_components)
    reciprocal_pairs = sum(
        1 for left in graph for right in graph[left] if left < right and left in graph.get(right, set())
    )
    errors: list[str] = []

    for component in cyclic_components:
        errors.append("Dependency cycle: " + " -> ".join(component))

    repo_root = bridge_root.parent
    for target in static_targets:
        target_path = repo_root / target
        if not target_path.is_file():
            errors.append(f"Missing static target: {target}")
            continue

        module = _target_module(target)
        if module not in graph:
            errors.append(f"Missing static target module: {target}")
            continue

        for imported in sorted(graph[module] - SERVICE_CONTRACT_IMPORTS):
            errors.append(f"Isolated static target {module} imports {imported}")

    for module, allowed in PURE_CONTRACT_IMPORTS.items():
        for imported in sorted(graph.get(module, set()) - allowed):
            errors.append(f"Pure contract layer {module} imports {imported}")

    for module, allowed in LOW_LEVEL_IMPORTS.items():
        for imported in sorted(graph.get(module, set()) - allowed):
            errors.append(f"Low-level owner {module} imports {imported}")
    for module in CALLBACK_DOMAIN_MODULES:
        forbidden = CALLBACK_DOMAIN_MODULES - {module}
        for imported in sorted(graph.get(module, set()) & forbidden):
            errors.append(f"Callback domain violation: {module} imports sibling {imported}")
    for module, forbidden in FORBIDDEN_OWNER_IMPORTS.items():
        for imported in sorted(graph.get(module, set()) & forbidden):
            errors.append(f"Owner direction violation: {module} imports {imported}")

    return DependencyReport(
        modules=len(graph),
        edges=sum(len(targets) for targets in graph.values()),
        cyclic_modules=cyclic_modules,
        reciprocal_pairs=reciprocal_pairs,
        cyclic_components=cyclic_components,
        errors=tuple(sorted(errors)),
    )


def _print_report(report: DependencyReport) -> None:
    print(f"modules={report.modules}")
    print(f"edges={report.edges}")
    print(f"cyclic_modules={report.cyclic_modules}")
    print(f"reciprocal_pairs={report.reciprocal_pairs}")
    if report.cyclic_components:
        for component in report.cyclic_components:
            print("cycle=" + ",".join(component))
    if report.errors:
        print("violations:")
        for error in report.errors:
            print(f"- {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check bridge dependency direction policy.")
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "bridge",
    )
    parser.add_argument(
        "--print-targets",
        action="store_true",
        help="Print the incremental static target paths and exit.",
    )
    parser.add_argument("--print-type-targets", action="store_true", help="Print progressively typed module paths.")
    args = parser.parse_args()

    if args.print_type_targets:
        print("\n".join(TYPE_TARGETS))
        return 0
    if args.print_targets:
        print("\n".join(STATIC_TARGETS))
        return 0

    report = check_dependency_direction(args.bridge_root)
    _print_report(report)
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

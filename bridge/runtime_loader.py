"""Validated final compatibility loader used only for bridge/main.py.

Phase 7B4 moved every domain and adapter module to ordinary imports. Phase 7C
will remove this final production exec boundary when main becomes the ordinary
composition entry point.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import MutableMapping

from bridge.extension_registry import reset_extension_registry as _reset_extension_registry


@dataclass(frozen=True)
class RuntimeStage:
    name: str
    modules: tuple[str, ...]
    allowed_public_callable_overrides: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def allowed_overrides_for(self, filename: str) -> frozenset[str]:
        mapping = dict(self.allowed_public_callable_overrides)
        return frozenset(mapping.get(filename, ()))


DEFAULT_RUNTIME_STAGES = (
    RuntimeStage("core", ("main.py",)),
)


def _validate_stages(stages: tuple[RuntimeStage, ...]) -> None:
    seen: set[str] = set()
    for stage in stages:
        if not stage.name:
            raise RuntimeError("runtime stage name must not be empty")
        module_names = set(stage.modules)
        for allowed_filename, allowed_names in stage.allowed_public_callable_overrides:
            if allowed_filename not in module_names:
                raise RuntimeError(
                    f"runtime override allowlist references module outside stage {stage.name}: {allowed_filename}"
                )
            if len(set(allowed_names)) != len(allowed_names):
                raise RuntimeError(f"runtime override allowlist contains duplicates for {allowed_filename}")
        for filename in stage.modules:
            if filename in seen:
                raise RuntimeError(f"runtime module listed more than once: {filename}")
            seen.add(filename)


def _public_callable_overrides(
    before: dict[str, object],
    namespace: MutableMapping[str, object],
) -> tuple[str, ...]:
    changed = []
    for name, old_value in before.items():
        if name.startswith("_") or name not in namespace:
            continue
        new_value = namespace[name]
        if old_value is new_value:
            continue
        if callable(old_value) and callable(new_value):
            changed.append(name)
    return tuple(sorted(changed))


def load_runtime_namespace(
    namespace: MutableMapping[str, object],
    base_dir: Path,
    stages: tuple[RuntimeStage, ...] = DEFAULT_RUNTIME_STAGES,
    reset_extensions: bool = True,
) -> tuple[dict[str, object], ...]:
    """Execute runtime modules in validated stages and return an override report.

    Normal runtime loads reset extension registrations first. Isolated loader
    validation can opt out so it does not mutate an already-running registry.
    """
    _validate_stages(stages)
    if reset_extensions:
        _reset_extension_registry()
    report = []
    runtime_root = base_dir.resolve()
    for stage in stages:
        for filename in stage.modules:
            path = (runtime_root / filename).resolve()
            if not path.is_relative_to(runtime_root):
                raise RuntimeError(f"runtime module is outside runtime base directory: {filename}")
            if not path.is_file():
                raise RuntimeError(f"runtime module is missing: {path}")
            before = dict(namespace)
            source = path.read_text(encoding="utf-8")
            # Sources are repository-controlled and path-confined before execution.
            exec(compile(source, str(path), "exec"), namespace, namespace)  # nosec B102
            overrides = _public_callable_overrides(before, namespace)
            allowed = stage.allowed_overrides_for(filename)
            unexpected = tuple(name for name in overrides if name not in allowed)
            if unexpected:
                joined = ", ".join(unexpected)
                raise RuntimeError(
                    f"runtime module {filename} unexpectedly overrides public callables: {joined}"
                )
            report.append(
                {
                    "stage": stage.name,
                    "module": filename,
                    "public_callable_overrides": overrides,
                }
            )
    return tuple(report)

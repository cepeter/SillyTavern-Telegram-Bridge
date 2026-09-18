"""Validated loader for the legacy shared runtime namespace.

The bridge still exposes one compatibility namespace, but load order and
intentional late overrides are described explicitly here instead of being an
implicit property of a flat exec() loop. Core/extension modules are not allowed
to silently replace public callables. Recovery/safety stages may replace them
because those modules intentionally harden earlier implementations.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import MutableMapping


@dataclass(frozen=True)
class RuntimeStage:
    name: str
    modules: tuple[str, ...]
    allow_public_callable_overrides: bool = False


DEFAULT_RUNTIME_STAGES = (
    RuntimeStage(
        "core",
        (
            "common.py", "cards.py", "schema.py", "database.py", "memory.py",
            "rag.py", "groups.py", "telegram.py", "persona_delete_panel.py",
            "language.py", "greetings.py", "help_details.py", "help.py", "input_flows.py",
            "catalog.py", "update.py", "image_generation.py", "expressions.py",
            "media.py", "generation.py", "commands.py", "command_routes.py",
            "message_commands.py", "callbacks.py", "panel_callback_routes.py",
            "main.py",
        ),
    ),
    RuntimeStage("recovery_overrides", ("recovery.py",), True),
    RuntimeStage("sync_extensions", ("sync_core.py", "sync_api.py")),
    RuntimeStage("native_adapter_overrides", ("persona_sync.py",), True),
    RuntimeStage(
        "identity_extensions",
        ("character_identity.py", "session_naming.py"),
    ),
    RuntimeStage(
        "safety_overrides",
        ("sync_safety.py", "state_integrity.py", "scheduler_safety.py"),
        True,
    ),
)


def _validate_stages(stages: tuple[RuntimeStage, ...]) -> None:
    seen: set[str] = set()
    for stage in stages:
        if not stage.name:
            raise RuntimeError("runtime stage name must not be empty")
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
) -> tuple[dict[str, object], ...]:
    """Execute runtime modules in validated stages and return an override report."""
    _validate_stages(stages)
    report = []
    for stage in stages:
        for filename in stage.modules:
            path = base_dir / filename
            if not path.is_file():
                raise RuntimeError(f"runtime module is missing: {path}")
            before = dict(namespace)
            source = path.read_text(encoding="utf-8")
            exec(compile(source, str(path), "exec"), namespace, namespace)
            overrides = _public_callable_overrides(before, namespace)
            if overrides and not stage.allow_public_callable_overrides:
                joined = ", ".join(overrides)
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

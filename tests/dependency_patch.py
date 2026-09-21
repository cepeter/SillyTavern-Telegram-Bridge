"""Dependency-aware module patching for transitional application tests.

This is not an aggregate runtime namespace. Each proxy represents one explicit
bridge module. Reads and writes follow the canonical source declared in
bridge.ordinary_dependencies so tests can patch the owner while the current
transitional module-local bindings are kept consistent.

Delete this helper when ordinary_dependencies is retired.
"""
from __future__ import annotations

import importlib
import sys

from bridge.application_composition import initialize_extensions
from bridge.ordinary_dependencies import DECLARED_DEPENDENCIES


_APPLICATION_READY = False


def _canonical_source(
    module_name: str,
    attribute: str,
) -> tuple[str, str | None]:
    """Resolve one declared local binding to its ultimate source."""
    current_module = module_name
    current_attribute: str | None = attribute
    seen: set[tuple[str, str | None]] = set()

    while current_attribute is not None:
        key = (current_module, current_attribute)
        if key in seen:
            raise RuntimeError(
                "cyclic dependency source while resolving "
                f"{module_name}.{attribute}"
            )
        seen.add(key)

        spec = DECLARED_DEPENDENCIES.get(current_module, {}).get(
            current_attribute
        )
        if spec is None:
            break
        current_module, current_attribute = spec

    return current_module, current_attribute


def _binding_matches_source(
    module_name: str,
    local_name: str,
    source: tuple[str, str | None],
) -> bool:
    return _canonical_source(module_name, local_name) == source


def _set_source(
    source: tuple[str, str | None],
    value: object,
) -> None:
    source_module_name, source_attribute = source
    if source_attribute is None:
        raise AttributeError(
            f"cannot replace module dependency {source_module_name}"
        )

    source_module = importlib.import_module(source_module_name)
    original = getattr(source_module, source_attribute)
    setattr(source_module, source_attribute, value)

    # Keep declared module-local bindings in sync.
    for module_name, declarations in DECLARED_DEPENDENCIES.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for local_name in declarations:
            if _binding_matches_source(module_name, local_name, source):
                setattr(module, local_name, value)

    # Acyclic dependencies may use normal from-import bindings instead of the
    # transitional declaration table. Update only loaded bridge-module copies
    # that still point at the exact canonical object being replaced. Unlike
    # the retired runtime facade, this never mutates merely by name.
    for module_name, module in tuple(sys.modules.items()):
        if not module_name.startswith("bridge.") or module is None:
            continue
        namespace = vars(module)
        if namespace.get(source_attribute) is original:
            namespace[source_attribute] = value


class _DependencyModuleProxy:
    def __init__(self, module_name: str) -> None:
        object.__setattr__(self, "_module_name", module_name)
        object.__setattr__(self, "_patch_originals", {})
        importlib.import_module(module_name)

    @property
    def __name__(self) -> str:
        return object.__getattribute__(self, "_module_name")

    def __getattr__(self, name: str):
        module_name = object.__getattribute__(self, "_module_name")
        source_module_name, source_attribute = _canonical_source(
            module_name,
            name,
        )
        if source_attribute is None:
            return importlib.import_module(source_module_name)
        source_module = importlib.import_module(source_module_name)
        return getattr(source_module, source_attribute)

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"_module_name", "_patch_originals"}:
            object.__setattr__(self, name, value)
            return

        module_name = object.__getattribute__(self, "_module_name")
        source = _canonical_source(module_name, name)
        originals = object.__getattribute__(self, "_patch_originals")
        if name not in originals:
            source_module_name, source_attribute = source
            if source_attribute is None:
                raise AttributeError(
                    f"cannot replace module dependency {source_module_name}"
                )
            source_module = importlib.import_module(source_module_name)
            originals[name] = getattr(source_module, source_attribute)

        original = originals[name]
        _set_source(source, value)
        if value is original:
            originals.pop(name, None)

    def __delattr__(self, name: str) -> None:
        originals = object.__getattribute__(self, "_patch_originals")
        if name not in originals:
            raise AttributeError(name)
        module_name = object.__getattribute__(self, "_module_name")
        source = _canonical_source(module_name, name)
        original = originals.pop(name)
        _set_source(source, original)


def dependency_module(module_name: str) -> _DependencyModuleProxy:
    """Return a patch-aware proxy for one explicitly named bridge module."""
    if not module_name.startswith("bridge.") or module_name == "bridge.runtime":
        raise ValueError(
            f"canonical bridge module required, got {module_name!r}"
        )

    global _APPLICATION_READY
    if not _APPLICATION_READY:
        # bridge.main completes the current declared application graph. The
        # retired test facade also composed built-in extensions once, so keep
        # that test setup explicitly through the public composition boundary.
        importlib.import_module("bridge.main")
        initialize_extensions()
        _APPLICATION_READY = True

    return _DependencyModuleProxy(module_name)

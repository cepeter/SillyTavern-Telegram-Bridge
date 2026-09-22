"""One-time application setup for tests using ordinary module imports.

Built-in extensions are composed explicitly. During tests, assignments to a
concrete bridge module also update already-imported bindings that still point
at the exact same object. This preserves normal patch semantics across
direct-import consumers without any dependency ownership table or runtime
facade.
"""
from __future__ import annotations

import importlib
import sys
from types import ModuleType

from bridge.application_composition import initialize_extensions


_INITIALIZED = False
_MISSING = object()


class _IdentityPropagatingModule(ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        original = vars(self).get(name, _MISSING)
        ModuleType.__setattr__(self, name, value)

        if original is _MISSING or name.startswith("__"):
            return

        for module_name, module in tuple(sys.modules.items()):
            if (
                not module_name.startswith("bridge.")
                or module is None
                or module is self
            ):
                continue
            if vars(module).get(name, _MISSING) is original:
                ModuleType.__setattr__(module, name, value)


def _install_identity_propagation() -> None:
    for module_name, module in tuple(sys.modules.items()):
        if (
            module_name.startswith("bridge.")
            and module is not None
            and module.__class__ is ModuleType
        ):
            module.__class__ = _IdentityPropagatingModule


def ensure_application_extensions() -> None:
    global _INITIALIZED

    # Import the complete ordinary application graph before enabling test-only
    # assignment propagation. Production import behavior remains untouched.
    importlib.import_module("bridge.main")

    if not _INITIALIZED:
        initialize_extensions()
        _INITIALIZED = True

    # Also catch any bridge module imported by an individual test afterwards.
    _install_identity_propagation()

"""Legacy shared-runtime patch semantics for tests only.

Production bridge.runtime is a plain re-export facade in Phase 7C. Older
tests intentionally patch names through one surface, so this proxy preserves
that ergonomic behavior without keeping mutation interception in production.
"""
from __future__ import annotations

import importlib
import sys

from bridge.application_composition import initialize_extensions


_RUNTIME = importlib.import_module("bridge.runtime")
initialize_extensions()

_MODULE_NAMES = (
    "bridge.common",
    "bridge.cards",
    "bridge.memory",
    "bridge.rag",
    "bridge.groups",
    "bridge.telegram",
    "bridge.persona_delete_panel",
    "bridge.language",
    "bridge.main",
    "bridge.greetings",
    "bridge.help_details",
    "bridge.help",
    "bridge.input_flows",
    "bridge.catalog",
    "bridge.update",
    "bridge.image_generation",
    "bridge.expressions",
    "bridge.media",
    "bridge.generation",
    "bridge.commands",
    "bridge.status_panels",
    "bridge.command_routes",
    "bridge.message_commands",
    "bridge.callbacks",
    "bridge.panel_callback_routes",
    "bridge.sync_core",
    "bridge.sync_api",
    "bridge.persona_sync",
    "bridge.character_identity",
    "bridge.session_naming",
    "bridge.scene_state",
    "bridge.director_goals",
    "bridge.memory_curator",
)


class _RuntimeTestFacade:
    def __init__(self) -> None:
        object.__setattr__(self, "_runtime", _RUNTIME)
        owners = {}
        for module_name in _MODULE_NAMES:
            module = importlib.import_module(module_name)
            for name in vars(module):
                if not name.startswith("__"):
                    owners[name] = module
        object.__setattr__(self, "_owners", owners)
        for name, value in vars(_RUNTIME).items():
            if not name.startswith("__"):
                object.__setattr__(self, name, value)

    def __getattribute__(self, name: str):
        if name in {
            "_runtime",
            "_owners",
            "__dict__",
            "__class__",
            "__setattr__",
            "__getattribute__",
            "__delattr__",
        }:
            return object.__getattribute__(self, name)
        owners = object.__getattribute__(self, "_owners")
        owner = owners.get(name)
        if owner is not None and name in vars(owner):
            return vars(owner)[name]
        runtime = object.__getattribute__(self, "_runtime")
        if hasattr(runtime, name):
            return getattr(runtime, name)
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value) -> None:
        object.__setattr__(self, name, value)
        runtime = object.__getattribute__(self, "_runtime")
        if hasattr(runtime, name):
            setattr(runtime, name, value)
        for module_name in _MODULE_NAMES:
            module = sys.modules.get(module_name)
            if module is not None and name in vars(module):
                vars(module)[name] = value

    def __delattr__(self, name: str) -> None:
        if name in self.__dict__:
            object.__delattr__(self, name)


runtime = _RuntimeTestFacade()

"""One-time explicit application extension setup for tests.

This helper exists only because both unittest discovery and pytest import test
modules independently. It performs no patch propagation, module mutation, or
dependency binding.
"""
from __future__ import annotations

from bridge.application_composition import initialize_extensions


_INITIALIZED = False


def ensure_application_extensions() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    initialize_extensions()
    _INITIALIZED = True

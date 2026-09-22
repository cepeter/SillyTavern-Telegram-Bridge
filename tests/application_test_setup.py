"""One-time explicit application extension setup for tests.

This helper exists only because both unittest discovery and pytest import test
modules independently. It performs no patch propagation, module mutation, or
dependency binding.
"""
from __future__ import annotations

from bridge.application_composition import initialize_extensions
from bridge.memory_service import MemoryService


_INITIALIZED = False


def ensure_application_extensions() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    initialize_extensions()
    _INITIALIZED = True


def make_test_memory_service(*, purge_session_memory=None) -> MemoryService:
    """Return an explicit MemoryService for tests that do not compose startup."""
    return MemoryService(
        recall_context=lambda *_args, **_kwargs: "",
        summary_for_prompt=lambda *_args, **_kwargs: "",
        summary_state=lambda *_args, **_kwargs: ("", 0),
        retain_session=lambda *_args, **_kwargs: None,
        purge_session_memory=(
            purge_session_memory
            if purge_session_memory is not None
            else (lambda *_args, **_kwargs: 0)
        ),
    )

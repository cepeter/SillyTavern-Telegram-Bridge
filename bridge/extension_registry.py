"""Explicit extension points for compatibility-runtime feature modules.

Feature modules may still be loaded through bridge.runtime for compatibility,
but cross-cutting composition should be declared here rather than implemented by
replacing previously loaded functions. Named registrations are deterministic and
introspectable, which makes extension order visible to tests and diagnostics.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


CommandRoute = Callable[..., bool]
PostRetainHook = Callable[[Any, str, dict[str, str], dict[str, str]], None]
SummaryContextHook = Callable[[str, Any, str, dict[str, str]], str]
SummaryClearHook = Callable[[Any, str, str], None]


_COMMAND_ROUTES: dict[str, CommandRoute] = {}
_POST_RETAIN_HOOKS: dict[str, PostRetainHook] = {}
_SUMMARY_CONTEXT_HOOKS: dict[str, SummaryContextHook] = {}
_SUMMARY_CLEAR_HOOKS: dict[str, SummaryClearHook] = {}


def _register(registry: dict[str, Callable], name: str, handler: Callable) -> None:
    key = str(name or "").strip()
    if not key:
        raise ValueError("extension name must not be empty")
    if not callable(handler):
        raise TypeError(f"extension {key} must be callable")
    registry[key] = handler


def register_command_route(name: str, handler: CommandRoute) -> None:
    _register(_COMMAND_ROUTES, name, handler)


def dispatch_command_routes(*args, **kwargs) -> bool:
    for handler in tuple(_COMMAND_ROUTES.values()):
        if handler(*args, **kwargs):
            return True
    return False


def register_post_retain_hook(name: str, handler: PostRetainHook) -> None:
    _register(_POST_RETAIN_HOOKS, name, handler)


def run_post_retain_hooks(
    db: Any,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
) -> None:
    for handler in tuple(_POST_RETAIN_HOOKS.values()):
        handler(db, chat_id, session, fields)


def register_summary_context_hook(name: str, handler: SummaryContextHook) -> None:
    _register(_SUMMARY_CONTEXT_HOOKS, name, handler)


def apply_summary_context_hooks(
    summary: str,
    db: Any,
    chat_id: str,
    session: dict[str, str],
) -> str:
    result = str(summary or "")
    for handler in tuple(_SUMMARY_CONTEXT_HOOKS.values()):
        result = str(handler(result, db, chat_id, session) or "")
    return result


def register_summary_clear_hook(name: str, handler: SummaryClearHook) -> None:
    _register(_SUMMARY_CLEAR_HOOKS, name, handler)


def run_summary_clear_hooks(db: Any, chat_id: str, session_id: str) -> None:
    for handler in tuple(_SUMMARY_CLEAR_HOOKS.values()):
        handler(db, chat_id, session_id)


def extension_registry_snapshot() -> dict[str, tuple[str, ...]]:
    """Return registered extension names in dispatch order for diagnostics/tests."""
    return {
        "command_routes": tuple(_COMMAND_ROUTES),
        "post_retain": tuple(_POST_RETAIN_HOOKS),
        "summary_context": tuple(_SUMMARY_CONTEXT_HOOKS),
        "summary_clear": tuple(_SUMMARY_CLEAR_HOOKS),
    }

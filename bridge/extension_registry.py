"""Explicit extension points for ordinary bridge feature modules.

Cross-cutting composition is declared here rather than implemented by replacing
functions after load. Named registrations are deterministic and introspectable,
which makes extension order visible to production composition, tests, and
diagnostics.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any


CommandRoute = Callable[..., bool]
PostRetainHook = Callable[[Any, str, dict[str, str], dict[str, str], Any], None]
SummaryContextHook = Callable[[str, Any, str, dict[str, str]], str | None]
SummaryClearHook = Callable[[Any, str, str], None]


@dataclass(frozen=True)
class DirectorCustomization:
    model: str | None = None
    hidden_instructions: str = ""
    max_tokens: int | None = None
    speaker_context: str = ""


DirectorCustomizationProvider = Callable[
    [Any, str, dict[str, str]],
    DirectorCustomization | None,
]


_COMMAND_ROUTES: dict[str, CommandRoute] = {}
_POST_RETAIN_HOOKS: dict[str, PostRetainHook] = {}
_SUMMARY_CONTEXT_HOOKS: dict[str, SummaryContextHook] = {}
_SUMMARY_CLEAR_HOOKS: dict[str, SummaryClearHook] = {}
_DIRECTOR_CUSTOMIZATION_PROVIDER: tuple[str, DirectorCustomizationProvider] | None = None


def reset_extension_registry() -> None:
    """Clear all registered extensions before explicit application composition."""
    global _DIRECTOR_CUSTOMIZATION_PROVIDER

    _COMMAND_ROUTES.clear()
    _POST_RETAIN_HOOKS.clear()
    _SUMMARY_CONTEXT_HOOKS.clear()
    _SUMMARY_CLEAR_HOOKS.clear()
    _DIRECTOR_CUSTOMIZATION_PROVIDER = None


def _register(registry: dict[str, Callable], name: str, handler: Callable) -> None:
    key = str(name or "").strip()
    if not key:
        raise ValueError("extension name must not be empty")
    if not callable(handler):
        raise TypeError(f"extension {key} must be callable")
    if key in registry:
        raise RuntimeError(f"extension name already registered: {key}")
    registry[key] = handler


def register_command_route(name: str, handler: CommandRoute) -> None:
    _register(_COMMAND_ROUTES, name, handler)


def dispatch_command_routes(*args, **kwargs) -> bool:
    """Dispatch registered command routes.

    Handler exceptions intentionally propagate to the durable command worker.
    This prevents a failed extension command from falling through into ordinary
    character generation.
    """
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
    provider_port: Any,
) -> None:
    for name, handler in tuple(_POST_RETAIN_HOOKS.items()):
        try:
            handler(db, chat_id, session, fields, provider_port)
        except Exception:
            logging.exception("Post-retain extension hook failed: %s", name)


def register_summary_context_hook(name: str, handler: SummaryContextHook) -> None:
    _register(_SUMMARY_CONTEXT_HOOKS, name, handler)


def apply_summary_context_hooks(
    summary: str,
    db: Any,
    chat_id: str,
    session: dict[str, str],
) -> str:
    result = str(summary or "")
    for name, handler in tuple(_SUMMARY_CONTEXT_HOOKS.items()):
        try:
            updated = handler(result, db, chat_id, session)
        except Exception:
            logging.exception("Summary-context extension hook failed: %s", name)
            continue
        if updated is not None:
            result = str(updated)
    return result


def register_summary_clear_hook(name: str, handler: SummaryClearHook) -> None:
    _register(_SUMMARY_CLEAR_HOOKS, name, handler)


def run_summary_clear_hooks(db: Any, chat_id: str, session_id: str) -> None:
    for name, handler in tuple(_SUMMARY_CLEAR_HOOKS.items()):
        try:
            handler(db, chat_id, session_id)
        except Exception:
            logging.exception("Summary-clear extension hook failed: %s", name)


def register_director_customization_provider(
    name: str,
    provider: DirectorCustomizationProvider,
) -> None:
    global _DIRECTOR_CUSTOMIZATION_PROVIDER

    key = str(name or "").strip()
    if not key:
        raise ValueError("extension name must not be empty")
    if not callable(provider):
        raise TypeError(f"extension {key} must be callable")
    if _DIRECTOR_CUSTOMIZATION_PROVIDER is not None:
        existing_name, _existing_provider = _DIRECTOR_CUSTOMIZATION_PROVIDER
        raise RuntimeError(
            f"director customization provider already registered: {existing_name}"
        )
    _DIRECTOR_CUSTOMIZATION_PROVIDER = (key, provider)


def get_director_customization(
    db: Any,
    chat_id: str,
    session: dict[str, str],
) -> DirectorCustomization | None:
    registered = _DIRECTOR_CUSTOMIZATION_PROVIDER
    if registered is None:
        return None

    name, provider = registered
    try:
        result = provider(db, chat_id, session)
    except Exception:
        logging.exception("Director customization provider failed: %s", name)
        return None

    if result is not None and not isinstance(result, DirectorCustomization):
        logging.error(
            "Director customization provider returned invalid value: %s",
            name,
        )
        return None
    return result


def extension_registry_snapshot() -> dict[str, tuple[str, ...]]:
    """Return registered extension names in dispatch order for diagnostics/tests."""
    return {
        "command_routes": tuple(_COMMAND_ROUTES),
        "post_retain": tuple(_POST_RETAIN_HOOKS),
        "summary_context": tuple(_SUMMARY_CONTEXT_HOOKS),
        "summary_clear": tuple(_SUMMARY_CLEAR_HOOKS),
        "director_customization": (
            (_DIRECTOR_CUSTOMIZATION_PROVIDER[0],)
            if _DIRECTOR_CUSTOMIZATION_PROVIDER is not None
            else ()
        ),
    }

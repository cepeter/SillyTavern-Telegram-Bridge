"""Small, content-free diagnostic context; never retain application payloads."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from types import TracebackType
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")
_CONTEXT: ContextVar[dict[str, object] | None] = ContextVar("bridge_diagnostics", default=None)
_IDENTITY_KEY = secrets.token_bytes(32)
_LOG = logging.getLogger("bridge.events")
_DROPPED = 0
_NAME = re.compile(r"[a-z][a-z0-9_.]{0,79}\Z")
_VALUE = re.compile(r"[A-Za-z0-9_./:@+\-]{1,160}\Z")
_TEXT_FIELDS = frozenset(
    {
        "request_id",
        "parent_request_id",
        "call_id",
        "chat_ref",
        "session_ref",
        "purpose",
        "provider",
        "model",
        "previous_model",
        "next_model",
        "status",
        "reason",
        "error_type",
        "layer",
        "label",
        "kind",
        "method",
        "route",
        "source",
        "version",
        "commit",
        "worker_id",
        "phase",
        "operation_id",
        "profile_request_id",
        "model_ref",
        "provider_ref",
        "transport",
        "prefix_scope",
        "section_attribution",
        "rejection_code",
        "npc_rejection_code",
        "simulation_rejection_code",
        "root_type",
        "state_type",
        "blocks_type",
        "npcs_type",
        "simulation_type",
        "finish_reason",
        "attempt_ref",
        "document_ref",
        "server_operation_ref",
    }
)
_NUMBER_FIELDS = frozenset(
    {
        "job_id",
        "parent_job_id",
        "update_id",
        "attempt",
        "elapsed_ms",
        "queue_ms",
        "http_status",
        "retry_after_ms",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "request_count",
        "source_message_id",
        "revision",
        "message_id",
        "rss_kib",
        "threads",
        "count",
        "coverage",
        "bytes",
        "dropped",
        "observation_ordinal",
        "message_count",
        "stable_prefix_characters",
        "profile_sample_count",
        "instruction_duplicate_groups",
        "usage_readings",
        "instruction_characters",
        "user_characters",
        "assistant_characters",
        "tool_characters",
    }
)
_BOOL_FIELDS = frozenset(
    {
        "usage_reported",
        "usage_complete",
        "stream_visible",
        "recovered",
        "accepted",
        "retryable",
        "prefix_observed",
        "non_text_payload_present",
        "full_prompt_token_count_known",
        "state_present",
        "blocks_present",
        "npcs_present",
        "simulation_present",
        "npc_valid",
        "simulation_valid",
        "output_cap_reached",
    }
)


def configure_identity(secret: str) -> None:
    """Stable local pseudonyms across restarts; bot-token rotation changes them."""
    global _IDENTITY_KEY
    if secret:
        _IDENTITY_KEY = hashlib.sha256(("sttb-diagnostics:" + secret).encode()).digest()


def scope_reference(kind: str, value: object) -> str:
    if not isinstance(value, (str, int)):
        return ""
    return hmac.new(_IDENTITY_KEY, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:24]


def clean_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Reject unknown keys, free-form text, containers and oversized values."""
    result: dict[str, object] = {}
    for key, value in fields.items():
        if key in {"chat_id", "session_id"}:
            if isinstance(value, (str, int)) and str(value):
                kind = key.removesuffix("_id")
                result[kind + "_ref"] = scope_reference(kind, value)
        elif key in _TEXT_FIELDS and isinstance(value, str) and _VALUE.fullmatch(value):
            result[key] = value
        elif key in _NUMBER_FIELDS and type(value) is int and 0 <= value <= 2**63 - 1:
            result[key] = value
        elif key in _BOOL_FIELDS and type(value) is bool:
            result[key] = value
    return result


def diagnostic_context() -> dict[str, object]:
    return dict(_CONTEXT.get() or {})


@contextmanager
def diagnostic_scope(*, inherit: bool = True, **fields: object) -> Iterator[None]:
    """Durable recovery can replace, rather than inherit, dispatcher identity."""
    parent = diagnostic_context() if inherit else {}
    token = _CONTEXT.set({**parent, **clean_fields(fields)})
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def bind_diagnostics(function: Callable[P, T]) -> Callable[P, T]:
    """Capture only bounded diagnostic scalars, not copy_context()/private state."""
    captured = diagnostic_context()

    @wraps(function)
    def bound(*args: P.args, **kwargs: P.kwargs) -> T:
        token = _CONTEXT.set(captured)
        try:
            return function(*args, **kwargs)
        finally:
            _CONTEXT.reset(token)

    return bound


def new_request_id(prefix: str = "request") -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


def exception_metadata(error_type: type[BaseException] | None, tb: TracebackType | None) -> dict[str, object]:
    """Snapshot only code locations; never retain an exception, traceback, frame or locals."""
    if error_type is None:
        return {}
    frames: list[dict[str, object]] = []
    while tb is not None:
        frames.append(
            {
                "file": Path(tb.tb_frame.f_code.co_filename).name[:80],
                "function": tb.tb_frame.f_code.co_name[:80],
                "line": tb.tb_lineno,
            }
        )
        frames = frames[-12:]
        tb = tb.tb_next
    return {"error_type": error_type.__name__[:80], "traceback": frames}


def event(name: str, *, level: int = logging.INFO, exc_info: bool = False, **fields: object) -> None:
    """Diagnostics are best effort and never change the application's outcome."""
    global _DROPPED
    values = {**diagnostic_context(), **clean_fields(fields)}
    values["event"] = name if _NAME.fullmatch(name) else "diagnostic.invalid_event"
    try:
        details = exception_metadata(sys.exc_info()[0], sys.exc_info()[2]) if exc_info else {}
        _LOG.log(level, values["event"], extra={"diagnostic_fields": values, "diagnostic_exception": details})
    except Exception:
        # A broken/custom handler must not fail a generation or worker.
        _DROPPED += 1


def diagnostic_failures() -> int:
    return _DROPPED

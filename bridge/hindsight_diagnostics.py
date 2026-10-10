"""Content-free failure diagnostics for Hindsight retention."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager

from bridge.diagnostic_events import diagnostic_context, diagnostic_scope, event, new_request_id, scope_reference


def hindsight_retain_failure_reason(error: BaseException) -> str:
    """Classify a retain failure without persisting response text or credentials."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, json.JSONDecodeError):
            return "upstream_invalid_response"
        response = getattr(current, "response", None)
        if isinstance(getattr(response, "status_code", None), int):
            return "upstream_http_error"
        if isinstance(current, TimeoutError) or "timeout" in type(current).__name__.casefold():
            return "upstream_timeout"
        current = current.__cause__ or current.__context__
    return "retain_failed"


def handle_hindsight_retain_failure(error: BaseException, chat_id: str, session_id: str) -> bool:
    """Record a bounded classification and preserve the boolean retain contract."""
    event(
        "memory.hindsight_retain_failed",
        chat_id=chat_id,
        session_id=session_id,
        reason=hindsight_retain_failure_reason(error),
        error_type=type(error).__name__,
        retryable=True,
    )
    logging.warning("Hindsight native fact retain unavailable")
    return False


@contextmanager
def hindsight_fact_attempt(token: str, document_id: str) -> Iterator[None]:
    # The job target is a queue high-water mark, not the oldest native fact.
    fields = diagnostic_context()
    fields.pop("source_message_id", None)
    fields.update(
        operation_id=new_request_id("retain"),
        attempt_ref=scope_reference("memory_attempt", token),
        document_ref=scope_reference("memory_document", document_id),
    )
    with diagnostic_scope(inherit=False, **fields):
        yield


@contextmanager
def hindsight_retain_scope(document_id: str, session_id: str, character_name: str) -> Iterator[dict[str, str]]:
    fields = diagnostic_context()
    fields.pop("source_message_id", None)
    fields["document_ref"] = scope_reference("memory_document", document_id)
    if not fields.get("attempt_ref"):
        fields["operation_id"] = new_request_id("retain")
        fields["attempt_ref"] = new_request_id("attempt")
    with diagnostic_scope(inherit=False, **fields):
        event("memory.hindsight_retain_start")
        yield {
            "source": "sillytavern_telegram_bridge",
            "session_id": session_id,
            "character": character_name,
            "bridge_operation_ref": str(fields["operation_id"]),
            "bridge_attempt_ref": str(fields["attempt_ref"]),
            "bridge_document_ref": str(fields["document_ref"]),
        }


_SERVER_OPERATION = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


def report_hindsight_completion(response: object) -> None:
    # Installed SDK RetainResponse has optional operation_id / operation_ids.
    # Sync calls usually supply neither. Never infer cancellation or a server ID.
    single = getattr(response, "operation_id", None)
    values = getattr(response, "operation_ids", None)
    candidates = [single, *(values[:8] if isinstance(values, list) else [])]
    operations = list(
        dict.fromkeys(value for value in candidates if isinstance(value, str) and _SERVER_OPERATION.fullmatch(value))
    )
    metadata = {"server_operation_ref": operations[0]} if operations else {}
    event("memory.hindsight_retain_finished", status="returned", **metadata)
    for operation in operations[1:]:
        event("memory.hindsight_server_operation", server_operation_ref=operation)

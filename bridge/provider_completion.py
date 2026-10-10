"""Allowlisted completion metadata; token counts never imply truncation."""

from __future__ import annotations

import logging

from bridge.diagnostic_events import clean_fields, event

_CAPPED = frozenset({"length", "max_tokens", "max_output_tokens"})
_NOT_CAPPED = frozenset(
    {
        "stop",
        "end_turn",
        "stop_sequence",
        "tool_use",
        "tool_calls",
        "function_call",
        "content_filter",
        "refusal",
        "pause_turn",
    }
)
_RESPONSE_KEYS = frozenset({"id", "object", "model", "choices", "usage", "data", "success"})


def warn_missing_assistant_content(
    provider: str, model: str, response: object, choices: list[dict], finish_reason: object, result: object
) -> None:
    """Retain bounded response metadata without logging provider-controlled text."""
    metadata = clean_fields({"provider": provider, "model": model})
    try:
        http_status = getattr(response, "status", None)
        if http_status is None:
            getcode = getattr(response, "getcode", None)
            http_status = getcode() if callable(getcode) else None
    except Exception:
        http_status = None
    safe_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else "unknown"
    safe_reason = (
        finish_reason if isinstance(finish_reason, str) and finish_reason in _CAPPED | _NOT_CAPPED else "unknown"
    )
    response_keys = sorted(_RESPONSE_KEYS.intersection(result)) if isinstance(result, dict) else []
    logging.getLogger("bridge.provider_transport").warning(
        "Provider response missing assistant content: "
        "provider=%s model=%s http_status=%s choice_count=%s finish_reason=%s response_keys=%s",
        metadata.get("provider", "unknown"),
        metadata.get("model", "unknown"),
        safe_status,
        len(choices),
        safe_reason,
        response_keys,
    )


def report_finish_reason(reason: object) -> None:
    fields: dict[str, object] = {}
    if reason is not None:
        known = isinstance(reason, str) and reason in _CAPPED | _NOT_CAPPED
        fields["finish_reason"] = reason if known else "unknown"
        if known:
            fields["output_cap_reached"] = reason in _CAPPED
    event("provider.output_finished", **fields)


def report_response_completion(payload: object) -> None:
    """Responses APIs expose an incomplete reason, not a Chat Completions finish_reason."""
    if not isinstance(payload, dict):
        return
    response = payload.get("response", payload)
    if not isinstance(response, dict):
        return
    details = response.get("incomplete_details")
    if isinstance(details, dict) and "reason" in details:
        report_finish_reason(details["reason"])

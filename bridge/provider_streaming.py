"""Existing OpenAI stream parsing with per-response observation callback binding."""

from __future__ import annotations

import json
import time

from bridge.provider_completion import report_finish_reason
from bridge.provider_response import openai_response_choices as _openai_response_choices
from bridge.provider_response import provider_response_lines
from bridge.request_observation_context import observed_usage_callback
from bridge.token_usage_values import UsageCallback, UsageCapture


def _stream_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(str(item.get("text") or item.get("content") or "") for item in value if isinstance(item, dict))
    return ""


def _join_visible_stream(prefix: str, segment: str) -> str:
    return " ".join(part for part in (str(prefix or "").strip(), str(segment or "").strip()) if part)


def read_openai_stream_segment(
    response,
    *,
    prefix: str,
    stream_callback,
    cancel_event,
    usage_callback: UsageCallback | None = None,
) -> tuple[str, str | None, bool]:
    with UsageCapture(observed_usage_callback(usage_callback), flavor="openai") as usage:
        parts: list[str] = []
        finish_reason: str | None = None
        last_emit = 0.0
        cancelled = False

        for raw_line in provider_response_lines(response):
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].lstrip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            usage.observe(event)
            for choice in _openai_response_choices(event):
                delta = choice.get("delta") or {}
                text_delta = _stream_text(delta.get("content"))
                if text_delta:
                    parts.append(text_delta)
                if choice.get("finish_reason"):
                    finish_reason = str(choice["finish_reason"])
            if stream_callback and parts and time.monotonic() - last_emit >= 0.5:
                stream_callback(_join_visible_stream(prefix, "".join(parts)))
                last_emit = time.monotonic()

        content = "".join(parts).strip()
        if stream_callback and content:
            stream_callback(_join_visible_stream(prefix, content))
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
        report_finish_reason(finish_reason)
        usage.final = not cancelled and finish_reason is not None
        return content, finish_reason, cancelled

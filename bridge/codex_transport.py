"""Native OpenAI Codex Responses transport for character-chat generation."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from collections.abc import Callable
from typing import Any

from bridge.codex_auth import codex_headers, resolve_access_token, validate_codex_endpoint
from bridge.codex_models import codex_wire_model
from bridge.config import GENERATION_DEFAULTS
from bridge.context_attempt_budget import check_attempt_budget
from bridge.network_security import strict_urlopen
from bridge.provider_completion import report_response_completion
from bridge.provider_errors import ProviderTransportError, parse_retry_after, provider_category_for_status
from bridge.request_observation_context import observed_request, observed_usage_callback
from bridge.settings import AppSettings
from bridge.token_usage_values import UsageCallback, UsageCapture

OpenRequest = Callable[..., Any]


def _text_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    return "\n".join(
        str(item.get("text") or "")
        for item in content
        if isinstance(item, dict) and item.get("type") in {"text", "input_text", "output_text"}
    )


def _responses_content(role: str, content: object) -> list[dict[str, str]]:
    item_type = "output_text" if role == "assistant" else "input_text"
    if isinstance(content, str):
        return [{"type": item_type, "text": content}]
    blocks: list[dict[str, str]] = []
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"text", "input_text", "output_text"}:
            blocks.append({"type": item_type, "text": str(item.get("text") or "")})
            continue
        image_url = item.get("image_url")
        if role == "user" and isinstance(image_url, dict) and image_url.get("url"):
            blocks.append({"type": "input_image", "image_url": str(image_url["url"])})
    return blocks or [{"type": item_type, "text": ""}]


def _codex_input(messages: list[dict]) -> tuple[str, list[dict[str, Any]]]:
    instructions = []
    inputs = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if role in {"system", "developer"}:
            text = _text_content(content).strip()
            if text:
                instructions.append(text)
            continue
        if role not in {"user", "assistant"}:
            continue
        inputs.append({"role": role, "content": _responses_content(role, content)})
    if not inputs:
        inputs.append({"role": "user", "content": [{"type": "input_text", "text": "Begin the conversation."}]})
    return "\n\n".join(
        instructions
    ) or "Continue the conversation while following the supplied character context.", inputs


def _integer(value: object, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _reasoning_effort(model: str, raw_budget: object) -> str | None:
    budget = _integer(raw_budget, 0)
    if budget <= 0:
        return None
    if budget <= 1024:
        return "low"
    if budget <= 4096:
        return "medium"
    if budget <= 8192:
        return "high"
    normalized = model.lower().rsplit("/", 1)[-1]
    if normalized.startswith("gpt-5.6") or "astra" in normalized or "daybreak" in normalized:
        return "max"
    return "xhigh"


def _response_output_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    direct = payload.get("output_text")
    if isinstance(direct, str):
        return direct.strip()
    chunks = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") not in {"output_text", "text"}:
                continue
            text = content.get("text")
            if isinstance(text, dict):
                text = text.get("value") or text.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks).strip()


_MAX_STREAM_BYTES = 4 * 1024 * 1024
_MAX_STREAM_LINE_BYTES = 256 * 1024


def _stream_response_text(
    response: Any, *, stream_callback: Any = None, cancel_event: Any = None, usage_callback: UsageCallback | None = None
) -> str:
    with UsageCapture(observed_usage_callback(usage_callback), flavor="openai") as usage:
        chunks: list[str] = []
        completed = ""
        saw_completion = False
        cancelled = False
        total_bytes = 0
        last_emit = float("-inf")
        while True:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            raw_line = response.readline(_MAX_STREAM_LINE_BYTES + 1)
            if not raw_line:
                break
            total_bytes += len(raw_line)
            if total_bytes > _MAX_STREAM_BYTES or len(raw_line) > _MAX_STREAM_LINE_BYTES:
                raise RuntimeError("OpenAI Codex response exceeded the safety limit")
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].lstrip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            usage.observe(event)
            report_response_completion(event)
            event_type = str(event.get("type") or "")
            if event_type in {
                "response.output_text.delta",
                "response.text.delta",
                "response.refusal.delta",
            } and isinstance(event.get("delta"), str):
                chunks.append(event["delta"])
                now = time.monotonic()
                if stream_callback and now - last_emit >= 0.5:
                    stream_callback("".join(chunks).strip())
                    last_emit = now
            elif event_type == "response.completed":
                completed = _response_output_text(event.get("response"))
                saw_completion = True
                break
            elif event_type in {"response.failed", "response.error", "error", "response.incomplete"}:
                # Provider messages may echo prompts, URLs or credentials. Never surface them.
                raise RuntimeError("OpenAI Codex request failed; provider response was unsuccessful")
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
        usage.final = not cancelled
        if cancelled:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        elif not saw_completion:
            raise RuntimeError("OpenAI Codex stream ended without a completion event")
        visible = completed or "".join(chunks).strip()
        if stream_callback and visible and not cancelled:
            stream_callback(visible)
        return visible


def generate_codex_response(
    actual_model: str,
    messages: list[dict],
    settings: dict[str, object],
    spec: dict,
    session_id: str,
    request_timeout: float | None = None,
    stream_callback: Any = None,
    cancel_event: Any = None,
    *,
    app_settings: AppSettings,
    usage_callback: UsageCallback | None = None,
    context_observer: Callable[[dict[str, object]], None] | None = None,
    context_model: str = "",
    open_request: OpenRequest = strict_urlopen,
) -> str:
    """Generate visible assistant text through ChatGPT's native Codex backend."""
    if cancel_event is not None and cancel_event.is_set():
        return ""
    instructions, inputs = _codex_input(messages)
    body = {
        "model": codex_wire_model(actual_model),
        "instructions": instructions,
        "input": inputs,
        "store": False,
        "stream": True,
        "prompt_cache_key": hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:64],
    }
    reasoning_effort = _reasoning_effort(actual_model, settings.get("reasoning_budget"))
    if reasoning_effort is not None:
        body["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}
    wire_messages = [{"role": "system", "content": instructions}, *inputs]
    requested = int(settings.get("max_tokens") or GENERATION_DEFAULTS["max_tokens"])

    def check_budget() -> None:
        check_attempt_budget(
            wire_messages,
            context_model or actual_model,
            requested,
            transport="openai_codex",
            app_settings=app_settings,
            context_observer=context_observer,
            stage="codex",
        )

    # Normalize and reject oversized requests before OAuth file/refresh access.
    check_budget()
    endpoint = validate_codex_endpoint(spec, environ=app_settings.environ)
    access_token = resolve_access_token(
        app_settings.codex_oauth_file,
        environ=app_settings.environ,
        open_request=open_request,
    )
    encoded_body = json.dumps(body).encode("utf-8")
    for attempt in range(2):
        if cancel_event is not None and cancel_event.is_set():
            return ""
        check_budget()
        request = urllib.request.Request(  # noqa: S310 -- opened only through DNS-pinned strict_urlopen
            endpoint + "/responses",
            data=encoded_body,
            headers=codex_headers(access_token, client_version=app_settings.codex_client_version),
            method="POST",
        )
        try:
            with (
                observed_request(
                    body,
                    wire_messages,
                    selection=context_model or actual_model,
                    route=endpoint,
                    transport="openai_codex",
                    phase="auth_retry" if attempt else "initial",
                    request_bytes=len(encoded_body),
                ),
                open_request(
                    request, timeout=240 if request_timeout is None else request_timeout, environ=app_settings.environ
                ) as response,
            ):
                output = _stream_response_text(
                    response, stream_callback=stream_callback, cancel_event=cancel_event, usage_callback=usage_callback
                )
            break
        except Exception as exc:
            status = getattr(exc, "code", None)
            error_headers = getattr(exc, "headers", None)
            retry_after = parse_retry_after(error_headers.get("Retry-After")) if error_headers is not None else None
            close = getattr(exc, "close", None)
            if callable(close):
                close()
            if cancel_event is not None and cancel_event.is_set():
                return ""
            if status == 401 and attempt == 0:
                access_token = resolve_access_token(
                    app_settings.codex_oauth_file,
                    environ=app_settings.environ,
                    open_request=open_request,
                    rejected_access_token=access_token,
                )
                continue
            if status == 401:
                raise ProviderTransportError("authentication", 401) from None
            if isinstance(status, int):
                raise ProviderTransportError(
                    provider_category_for_status(status), status, retry_after=retry_after
                ) from None
            raise
    else:  # pragma: no cover - the bounded loop always breaks or raises
        output = ""
    if cancel_event is not None and cancel_event.is_set():
        return output
    if not output:
        raise RuntimeError("OpenAI Codex returned no assistant content")
    return output

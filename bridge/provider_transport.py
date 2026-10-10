"""Infrastructure adapters for configured model-provider HTTP transports."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.parse
import urllib.request
from collections.abc import Callable

from bridge.codex_transport import generate_codex_response
from bridge.config import GENERATION_DEFAULTS, structured_json_options
from bridge.context_attempt_budget import check_attempt_budget
from bridge.limits import DEFAULT_MAX_TOKENS
from bridge.model_router import ModelRouter
from bridge.network_security import strict_urlopen, validate_provider_endpoint
from bridge.provider_completion import report_finish_reason, report_response_completion, warn_missing_assistant_content
from bridge.provider_response import openai_response_choices as _openai_response_choices
from bridge.provider_response import provider_response_lines, read_provider_response
from bridge.provider_streaming import read_openai_stream_segment as _read_openai_stream_segment
from bridge.request_observation_context import observed_request, observed_usage_callback
from bridge.settings import AppSettings
from bridge.token_usage_values import TokenUsage, UsageCallback, UsageCapture


def anthropic_content(value):
    if isinstance(value, str):
        return value
    blocks = []
    for item in value or []:
        if item.get("type") == "text":
            blocks.append({"type": "text", "text": str(item.get("text") or "")})
        elif item.get("type") == "image_url":
            url = str((item.get("image_url") or {}).get("url") or "")
            if url.startswith("data:") and ";base64," in url:
                header, data = url.split(";base64,", 1)
                media_type = header[5:] or "image/jpeg"
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
    return blocks or [{"type": "text", "text": ""}]


def _recovery_settings(settings: dict[str, object]) -> dict[str, object] | None:
    """Increase the output budget when a provider spends the whole budget reasoning."""
    current = int(settings.get("max_tokens") or DEFAULT_MAX_TOKENS)
    target = min(max(current * 2, 4096), 12000)
    if target <= current:
        return None
    recovered = dict(settings)
    recovered["max_tokens"] = target
    return recovered


_CONTINUATION_INSTRUCTION = (
    "Continue from the exact ending without repeating existing text. "
    "Preserve the response language exactly. Output only the continuation."
)
_MAX_VISIBLE_CONTINUATIONS = 3


def _parse_stop_sequences(raw: object) -> list[str]:
    return [item for item in str(raw or "").split("\n") if item]


def _resolve_provider_credential(
    spec: dict, api_key: str, default_env: str, label: str, *, app_settings: AppSettings
) -> str:
    """Prefer the provider-specific env key; fall back to the caller-supplied key."""
    configured_env = spec.get("api_key_env")
    key_env = str(configured_env or default_env)
    resolved = app_settings.environ.get(key_env, "")
    if not resolved and (not configured_env or key_env == "LLM_API_KEY"):
        resolved = api_key
    if not resolved:
        raise RuntimeError(f"Missing {label} credential: {key_env}")
    return resolved


def anthropic_generate(
    api_key: str,
    actual_model: str,
    messages: list[dict],
    settings: dict[str, object],
    spec: dict,
    session_id: str,
    request_timeout: float | None = None,
    *,
    app_settings: AppSettings,
    usage_callback: UsageCallback | None = None,
    context_observer: Callable[[dict[str, object]], None] | None = None,
    context_model: str = "",
) -> str:
    system_parts = [str(message.get("content") or "") for message in messages if message.get("role") == "system"]
    conversation = []
    for message in messages:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = anthropic_content(message.get("content"))
        if conversation and conversation[-1]["role"] == role:
            previous = conversation[-1]["content"]
            if isinstance(previous, str) and isinstance(content, str):
                conversation[-1]["content"] = previous + "\n\n" + content
            else:
                previous_blocks = previous if isinstance(previous, list) else [{"type": "text", "text": previous}]
                current_blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
                conversation[-1]["content"] = previous_blocks + current_blocks
        else:
            conversation.append({"role": role, "content": content})
    while conversation and conversation[0]["role"] == "assistant":
        opening = conversation.pop(0)["content"]
        system_parts.append(
            "## Opening character message\n"
            + (opening if isinstance(opening, str) else json.dumps(opening, ensure_ascii=False))
        )
    if not conversation:
        conversation = [{"role": "user", "content": "Begin the conversation."}]
    body = {
        "model": actual_model,
        "messages": conversation,
        "max_tokens": int(settings["max_tokens"]),
        "temperature": float(settings["temperature"]),
        "stream": True,
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    if float(settings.get("top_p", 1.0)) < 1.0:
        body["top_p"] = float(settings["top_p"])
    stops = _parse_stop_sequences(settings.get("stop_sequences"))
    if stops:
        body["stop_sequences"] = stops[:4]
    wire_messages = ([{"role": "system", "content": body["system"]}] if "system" in body else []) + conversation
    check_attempt_budget(
        wire_messages,
        context_model or actual_model,
        int(settings["max_tokens"]),
        transport="anthropic_messages",
        app_settings=app_settings,
        context_observer=context_observer,
        stage="anthropic",
    )
    endpoint = str(spec.get("api_endpoint") or spec.get("api") or "").rstrip("/")
    validate_provider_endpoint(endpoint, environ=app_settings.environ)
    endpoint = endpoint if endpoint.endswith("/messages") else endpoint + "/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": str(spec.get("anthropic_version") or "2023-06-01"),
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "SillyTavernTelegramBridge/1.0",
    }
    headers.update(spec.get("extra_headers") or {})
    request = urllib.request.Request(endpoint, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")  # noqa: S310 -- Request is opened only through DNS-pinned strict_urlopen
    with (
        observed_request(
            body,
            wire_messages,
            selection=context_model or actual_model,
            route=endpoint,
            transport="anthropic_messages",
            phase="initial",
            request_bytes=len(request.data),
        ),
        strict_urlopen(
            request, timeout=240 if request_timeout is None else request_timeout, environ=app_settings.environ
        ) as response,
        UsageCapture(observed_usage_callback(usage_callback), flavor="anthropic") as usage,
    ):
        usage.final = False
        parts = []
        stop_reason = None
        for raw_line in provider_response_lines(response):
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].lstrip())
            except json.JSONDecodeError:
                continue
            usage.observe(event)
            if event.get("type") == "message_stop" or (event.get("delta") or {}).get("stop_reason"):
                usage.final = True
            if event.get("type") == "message_stop":
                break
            delta = event.get("delta") or {}
            if "stop_reason" in delta:
                stop_reason = delta["stop_reason"]
            if delta.get("type") == "text_delta" and delta.get("text"):
                parts.append(str(delta["text"]))
        report_finish_reason(stop_reason)
        content = "".join(parts).strip()
        if not content:
            raise RuntimeError("Anthropic Messages returned no visible content")
        return content


def opencode_muse_headers(session_id: str, *, app_settings: AppSettings) -> dict[str, str]:
    base62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    digest = hashlib.sha256(f"opencode\\0{session_id}".encode("utf-8")).digest()
    session_key = f"ses_{digest[:6].hex()}" + "".join(base62[value % 62] for value in digest[6:20])
    request_digest = hashlib.sha256(f"opencode-request\\0{session_key}\\0{time.time_ns()}".encode("utf-8")).digest()
    request_id = f"msg_{request_digest[:6].hex()}" + "".join(base62[value % 62] for value in request_digest[6:20])
    version = app_settings.environ.get("OPENCODE_CLIENT_VERSION", "1.18.31")
    return {
        "Authorization": "",
        "x-opencode-session": session_key,
        "x-opencode-request": request_id,
        "x-opencode-client": "cli",
        "User-Agent": f"opencode/{version}",
        "Origin": "https://opencode.ai",
        "Referer": "https://opencode.ai/",
        "HTTP-Referer": "https://opencode.ai/",
        "X-Title": "opencode",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


OPENCODE_FINGERPRINT_TOOLS = ("bash", "glob", "grep", "read")


def _opencode_tool_name(tool: object) -> str:
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return function["name"].strip()
    name = tool.get("name")
    return name.strip() if isinstance(name, str) else ""


def _merge_opencode_responses_tools(body: dict) -> None:
    """Add the OpenCode Free fingerprint without discarding caller tools."""
    tools = body.get("tools")
    if not isinstance(tools, list):
        tools = []
        body["tools"] = tools
    present = {_opencode_tool_name(tool) for tool in tools}
    for name in OPENCODE_FINGERPRINT_TOOLS:
        if name in present:
            continue
        tools.append(
            {
                "type": "function",
                "name": name,
                "description": "This tool is currently unavailable and must not be used.",
                "parameters": {"type": "object", "properties": {}},
            }
        )
        present.add(name)
    body["tool_choice"] = "auto"


def _opencode_json_text(payload: dict) -> str:
    output_text = payload.get("output_text")
    if output_text:
        return str(output_text).strip()
    chunks = []
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                chunks.append(str(content["text"]))
    return "".join(chunks).strip()


def _opencode_responses_text(raw: str, *, usage_callback: UsageCallback | None = None) -> str:
    """Read both JSON and the SSE stream required by OpenCode Free."""
    with UsageCapture(observed_usage_callback(usage_callback), flavor="openai") as usage:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            usage.observe(payload)
            report_response_completion(payload)
            return _opencode_json_text(payload)

        usage.final = False
        chunks = []
        completed_text = ""
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].lstrip()
            if data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            usage.observe(event)
            report_response_completion(event)
            if event.get("type") == "response.completed":
                usage.final = True
            delta = event.get("delta")
            if isinstance(delta, str):
                chunks.append(delta)
            response = event.get("response")
            if isinstance(response, dict):
                completed_text = completed_text or _opencode_json_text(response)
        return "".join(chunks).strip() or completed_text


def opencode_muse_generate(
    actual_model: str,
    messages: list[dict],
    settings: dict[str, object],
    spec: dict,
    session_id: str,
    request_timeout: float | None = None,
    *,
    app_settings: AppSettings,
    usage_callback: UsageCallback | None = None,
    context_observer: Callable[[dict[str, object]], None] | None = None,
    context_model: str = "",
) -> str:
    endpoint = str(spec.get("api_endpoint") or spec.get("api") or "https://opencode.ai/zen/v1").rstrip("/")
    validate_provider_endpoint(endpoint, environ=app_settings.environ)
    inputs = []
    for message in messages:
        content = message.get("content")
        parts: list[dict[str, object]] = []
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "image_url":
                    image_url = item.get("image_url")
                    if isinstance(image_url, dict) and image_url.get("url"):
                        parts.append({"type": "input_image", "image_url": str(image_url["url"])})
                else:
                    parts.append({"type": "input_text", "text": str(item.get("text") or "")})
        else:
            parts.append({"type": "input_text", "text": str(content or "")})
        inputs.append({"role": str(message.get("role") or "user"), "content": parts})
    requested = int(settings.get("max_tokens") or 0)
    body = {
        "model": actual_model,
        "input": inputs,
        "tools": [],
        "stream": True,
        "store": False,
        "max_output_tokens": max(3000, requested),
        "reasoning": {"effort": "low"},
    }
    # OpenCode Free verifies the official client fingerprint on both Responses
    # and Chat Completions. The latest 9router fixes (#4132/#4188/#4215)
    # require the quartet even when the caller supplied no tools.
    _merge_opencode_responses_tools(body)
    wire_messages = list(inputs)
    if body.get("tools"):
        wire_messages.append({"role": "system", "content": json.dumps(body["tools"], ensure_ascii=False)})
    check_attempt_budget(
        wire_messages,
        context_model or actual_model,
        requested,
        transport="opencode_muse",
        app_settings=app_settings,
        context_observer=context_observer,
        stage="muse",
    )
    request = urllib.request.Request(  # noqa: S310 -- Request is opened only through DNS-pinned strict_urlopen
        endpoint + "/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={**opencode_muse_headers(session_id, app_settings=app_settings), "Accept": "text/event-stream"},
        method="POST",
    )
    with observed_request(
        body,
        inputs,
        selection=context_model or actual_model,
        route=endpoint,
        transport="opencode_muse",
        phase="initial",
        request_bytes=len(request.data),
    ):
        with strict_urlopen(
            request, timeout=240 if request_timeout is None else request_timeout, environ=app_settings.environ
        ) as response:
            raw = read_provider_response(response).decode("utf-8", "replace")
        output_text = _opencode_responses_text(raw, usage_callback=usage_callback)
    if not output_text:
        raise RuntimeError("OpenCode Muse returned no assistant content")
    return output_text


def generate_provider_text(
    model_router: ModelRouter,
    api_key: str,
    model: str,
    messages: list[dict],
    session_id: str = "telegram",
    settings: dict[str, object] | None = None,
    stream_callback=None,
    cancel_event=None,
    force_non_stream: bool = False,
    request_timeout: float | None = None,
    _recovery_attempt: int = 0,
    *,
    app_settings: AppSettings,
    usage_callback: UsageCallback | None = None,
    context_observer: Callable[[dict[str, object]], None] | None = None,
    context_model: str = "",
) -> str:
    """Generate through the selected bridge provider adapter."""
    route = model_router.route(model)
    provider_id = route.provider_id
    actual_model = route.model_id
    spec = dict(route.spec)
    transport = str(spec.get("transport") or "chat_completions")
    generation = dict(GENERATION_DEFAULTS)
    generation.update(settings or {})
    selected_model = context_model or f"{provider_id}::{actual_model}"
    check_attempt_budget(
        messages,
        selected_model,
        int(generation["max_tokens"]),
        transport=transport,
        app_settings=app_settings,
        context_observer=context_observer,
        stage="routed",
    )
    if transport == "openai_codex":
        return generate_codex_response(
            actual_model,
            messages,
            generation,
            spec,
            session_id,
            request_timeout=request_timeout,
            stream_callback=stream_callback,
            cancel_event=cancel_event,
            app_settings=app_settings,
            usage_callback=usage_callback,
            context_observer=context_observer,
            context_model=selected_model,
        )
    if transport == "opencode_muse":
        return opencode_muse_generate(
            actual_model,
            messages,
            generation,
            spec,
            session_id,
            request_timeout=request_timeout,
            app_settings=app_settings,
            usage_callback=usage_callback,
            context_observer=context_observer,
            context_model=selected_model,
        )
    if transport == "anthropic_messages":
        anthropic_key = _resolve_provider_credential(
            spec, api_key, "ANTHROPIC_API_KEY", "Anthropic", app_settings=app_settings
        )
        return anthropic_generate(
            anthropic_key,
            actual_model,
            messages,
            generation,
            spec,
            session_id,
            request_timeout=request_timeout,
            app_settings=app_settings,
            usage_callback=usage_callback,
            context_observer=context_observer,
            context_model=selected_model,
        )
    if transport not in {"chat_completions", "openai", "openai_compatible"}:
        raise RuntimeError(f"Provider transport '{transport}' is not supported")
    endpoint_base = str(spec.get("api_endpoint") or spec.get("api") or "").strip().rstrip("/")
    if not endpoint_base:
        raise RuntimeError("Provider endpoint is missing; configure api_endpoint in the private provider catalog")
    validate_provider_endpoint(endpoint_base, environ=app_settings.environ)
    endpoint = endpoint_base + "/chat/completions"
    request_key = _resolve_provider_credential(spec, api_key, "LLM_API_KEY", "provider", app_settings=app_settings)
    is_streaming = bool(spec.get("streaming") or spec.get("stream")) and not force_non_stream
    body = {
        "model": actual_model,
        "messages": messages,
        "temperature": float(generation["temperature"]),
        "max_tokens": int(generation["max_tokens"]),
        "top_p": float(generation["top_p"]),
        "frequency_penalty": float(generation["frequency_penalty"]),
        "presence_penalty": float(generation["presence_penalty"]),
        "stream": is_streaming,
        **structured_json_options(generation, provider_id, endpoint_base, actual_model),
    }
    if is_streaming and usage_callback is not None and spec.get("stream_usage", True):
        body["stream_options"] = {"include_usage": True}
    stops = _parse_stop_sequences(generation.get("stop_sequences"))
    if stops:
        body["stop"] = stops[:4]
    reasoning_budget = int(generation.get("reasoning_budget") or 0)
    if reasoning_budget > 0:
        body["reasoning"] = {"max_tokens": reasoning_budget}
    elif (urllib.parse.urlparse(endpoint_base).hostname or "").casefold() == "openrouter.ai":
        body["reasoning"] = {"enabled": False}
    headers = {
        "Authorization": f"Bearer {request_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if is_streaming else "application/json",
        "HTTP-Referer": "https://sillytavern.local",
        "X-Title": "SillyTavern Telegram Bridge",
    }
    headers.update(spec.get("extra_headers") or {})
    check_attempt_budget(
        body["messages"],
        selected_model,
        int(body["max_tokens"]),
        transport=transport,
        app_settings=app_settings,
        context_observer=context_observer,
        stage="chat",
    )
    request = urllib.request.Request(  # noqa: S310 -- Request is opened only through DNS-pinned strict_urlopen
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with (
        observed_request(
            body,
            body["messages"],
            selection=selected_model,
            route=endpoint,
            transport="chat_completions",
            phase="recovery" if _recovery_attempt else "initial",
            request_bytes=len(request.data),
        ),
        strict_urlopen(
            request,
            timeout=(240 if is_streaming else 180) if request_timeout is None else request_timeout,
            environ=app_settings.environ,
        ) as response,
    ):
        if not is_streaming:
            with UsageCapture(observed_usage_callback(usage_callback)) as usage:
                result = json.loads(read_provider_response(response).decode("utf-8"))
                usage.observe(result)
            choices = _openai_response_choices(result)
            finish_reason = choices[0].get("finish_reason") if choices else None
            report_finish_reason(finish_reason)
            content = choices[0].get("message", {}).get("content") if choices else None
            if not content:
                if finish_reason == "length" and generation.get("json_once") is not True and _recovery_attempt < 2:
                    recovered = _recovery_settings(generation)
                    if recovered:
                        return generate_provider_text(
                            model_router,
                            api_key,
                            model,
                            messages,
                            session_id=session_id,
                            settings=recovered,
                            force_non_stream=True,
                            request_timeout=request_timeout,
                            _recovery_attempt=_recovery_attempt + 1,
                            app_settings=app_settings,
                            usage_callback=usage_callback,
                            context_observer=context_observer,
                            context_model=selected_model,
                        )
                warn_missing_assistant_content(provider_id, actual_model, response, choices, finish_reason, result)
                raise RuntimeError("backend returned no assistant content")
            content = str(content).strip()
            if finish_reason != "length" or generation.get("json_once") is True:
                return content
            segments = [content]
            continuation_messages = list(body["messages"])
            for _attempt in range(3):
                continuation_messages.extend(
                    [
                        {"role": "assistant", "content": segments[-1]},
                        {
                            "role": "user",
                            "content": _CONTINUATION_INSTRUCTION,
                        },
                    ]
                )
                continuation_body = dict(body)
                continuation_body["messages"] = continuation_messages
                reading_started = False
                try:
                    check_attempt_budget(
                        continuation_messages,
                        selected_model,
                        int(continuation_body["max_tokens"]),
                        transport=transport,
                        app_settings=app_settings,
                        context_observer=context_observer,
                        stage="continuation",
                    )
                    continuation_request = urllib.request.Request(  # noqa: S310 -- opened through strict_urlopen
                        endpoint,
                        data=json.dumps(continuation_body).encode("utf-8"),
                        headers=headers,
                        method="POST",
                    )
                    with (
                        observed_request(
                            continuation_body,
                            continuation_messages,
                            selection=selected_model,
                            route=endpoint,
                            transport="chat_completions",
                            phase="continuation",
                            request_bytes=len(continuation_request.data),
                        ),
                        strict_urlopen(
                            continuation_request,
                            timeout=180 if request_timeout is None else request_timeout,
                            environ=app_settings.environ,
                        ) as continuation_response,
                    ):
                        reading_started = True
                        with UsageCapture(observed_usage_callback(usage_callback)) as usage:
                            continuation_result = json.loads(
                                read_provider_response(continuation_response).decode("utf-8")
                            )
                            usage.observe(continuation_result)
                    continuation_choices = _openai_response_choices(continuation_result)
                    continuation = (
                        continuation_choices[0].get("message", {}).get("content") if continuation_choices else None
                    )
                    continuation_reason = continuation_choices[0].get("finish_reason") if continuation_choices else None
                    report_finish_reason(continuation_reason)
                except Exception:
                    if usage_callback is not None and not reading_started:
                        usage_callback(TokenUsage(final=False))
                    logging.warning("Automatic continuation failed after %s segment(s)", len(segments), exc_info=True)
                    break
                if not continuation:
                    logging.warning("Automatic continuation returned no content after %s segment(s)", len(segments))
                    break
                segments.append(str(continuation).strip())
                if continuation_reason != "length":
                    break
            return " ".join(segment for segment in segments if segment)

        content, finish_reason, cancelled = _read_openai_stream_segment(
            response,
            prefix="",
            stream_callback=stream_callback,
            cancel_event=cancel_event,
            usage_callback=usage_callback,
        )
        if not content:
            can_recover = (
                finish_reason == "length"
                and _recovery_attempt < 2
                and not cancelled
                and not (cancel_event is not None and cancel_event.is_set())
            )
            if can_recover:
                recovered = _recovery_settings(generation)
                if recovered:
                    return generate_provider_text(
                        model_router,
                        api_key,
                        model,
                        messages,
                        session_id=session_id,
                        settings=recovered,
                        stream_callback=stream_callback,
                        cancel_event=cancel_event,
                        force_non_stream=False,
                        request_timeout=request_timeout,
                        _recovery_attempt=_recovery_attempt + 1,
                        app_settings=app_settings,
                        usage_callback=usage_callback,
                        context_observer=context_observer,
                        context_model=selected_model,
                    )
            raise RuntimeError(f"{provider_id} returned no visible content (finish_reason={finish_reason})")

        segments = [content]
        if finish_reason != "length" or cancelled or (cancel_event is not None and cancel_event.is_set()):
            return content

        continuation_messages = list(messages)
        for _attempt in range(_MAX_VISIBLE_CONTINUATIONS):
            if cancel_event is not None and cancel_event.is_set():
                break

            continuation_messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": segments[-1],
                    },
                    {
                        "role": "user",
                        "content": _CONTINUATION_INSTRUCTION,
                    },
                ]
            )
            continuation_body = dict(body)
            continuation_body["messages"] = continuation_messages
            reading_started = False
            try:
                check_attempt_budget(
                    continuation_messages,
                    selected_model,
                    int(continuation_body["max_tokens"]),
                    transport=transport,
                    app_settings=app_settings,
                    context_observer=context_observer,
                    stage="continuation",
                )
                continuation_request = urllib.request.Request(  # noqa: S310 -- opened through strict_urlopen
                    endpoint,
                    data=json.dumps(continuation_body).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with (
                    observed_request(
                        continuation_body,
                        continuation_messages,
                        selection=selected_model,
                        route=endpoint,
                        transport="chat_completions",
                        phase="continuation",
                        request_bytes=len(continuation_request.data),
                    ),
                    strict_urlopen(
                        continuation_request,
                        timeout=240 if request_timeout is None else request_timeout,
                        environ=app_settings.environ,
                    ) as continuation_response,
                ):
                    prefix = " ".join(segment for segment in segments if segment)
                    reading_started = True
                    (
                        continuation,
                        continuation_reason,
                        continuation_cancelled,
                    ) = _read_openai_stream_segment(
                        continuation_response,
                        prefix=prefix,
                        stream_callback=stream_callback,
                        cancel_event=cancel_event,
                        usage_callback=usage_callback,
                    )
            except Exception:
                if usage_callback is not None and not reading_started:
                    usage_callback(TokenUsage(final=False))
                logging.warning(
                    "Automatic continuation failed after %s segment(s)",
                    len(segments),
                    exc_info=True,
                )
                break

            if not continuation:
                logging.warning(
                    "Automatic continuation returned no content after %s segment(s)",
                    len(segments),
                )
                break

            segments.append(continuation)
            if (
                continuation_cancelled
                or (cancel_event is not None and cancel_event.is_set())
                or continuation_reason != "length"
            ):
                break

        return " ".join(segment for segment in segments if segment)

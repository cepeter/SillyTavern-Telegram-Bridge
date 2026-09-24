"""Infrastructure adapters for configured model-provider HTTP transports."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.parse
import urllib.request

from bridge.common import DEFAULT_PROVIDER_URL
from bridge.config import DEFAULT_MAX_TOKENS, GENERATION_DEFAULTS
from bridge.model_router import ModelRouter
from bridge.network_security import strict_urlopen, validate_provider_endpoint

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

def _stream_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(str(item.get("text") or item.get("content") or "") for item in value if isinstance(item, dict))
    return ""


def _openai_response_choices(result: object) -> list[dict]:
    """Return OpenAI-compatible choices from standard or wrapped responses."""
    if not isinstance(result, dict):
        return []

    top_level = result.get("choices")
    if isinstance(top_level, list) and top_level:
        return [
            choice
            for choice in top_level
            if isinstance(choice, dict)
        ]

    data = result.get("data")
    if isinstance(data, dict):
        nested = data.get("choices")
        if isinstance(nested, list):
            return [
                choice
                for choice in nested
                if isinstance(choice, dict)
            ]

    if isinstance(top_level, list):
        return [
            choice
            for choice in top_level
            if isinstance(choice, dict)
        ]
    return []

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


def _join_visible_stream(prefix: str, segment: str) -> str:
    return " ".join(
        part
        for part in (str(prefix or "").strip(), str(segment or "").strip())
        if part
    )


def _read_openai_stream_segment(
    response,
    *,
    prefix: str,
    stream_callback,
    cancel_event,
) -> tuple[str, str | None, bool]:
    parts: list[str] = []
    finish_reason: str | None = None
    last_emit = 0.0
    cancelled = False

    for raw_line in response:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        line = raw_line.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].lstrip()
        if payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in event.get("choices", []):
            delta = choice.get("delta") or {}
            text_delta = _stream_text(delta.get("content"))
            if text_delta:
                parts.append(text_delta)
            if choice.get("finish_reason"):
                finish_reason = str(choice["finish_reason"])
        if (
            stream_callback
            and parts
            and time.monotonic() - last_emit >= 0.5
        ):
            stream_callback(
                _join_visible_stream(prefix, "".join(parts))
            )
            last_emit = time.monotonic()

    content = "".join(parts).strip()
    if stream_callback and content:
        stream_callback(_join_visible_stream(prefix, content))
    if cancel_event is not None and cancel_event.is_set():
        cancelled = True
    return content, finish_reason, cancelled

def _parse_stop_sequences(raw: object) -> list[str]:
    return [item for item in str(raw or "").split("\n") if item]

def _resolve_provider_credential(spec: dict, api_key: str, default_env: str, label: str) -> str:
    """Prefer the provider-specific env key; fall back to the caller-supplied key."""
    configured_env = spec.get("api_key_env")
    key_env = str(configured_env or default_env)
    resolved = os.environ.get(key_env, "")
    if not resolved and (not configured_env or key_env == "LLM_API_KEY"):
        resolved = api_key
    if not resolved:
        raise RuntimeError(f"Missing {label} credential: {key_env}")
    return resolved

def anthropic_generate(api_key: str, actual_model: str, messages: list[dict], settings: dict[str, object], spec: dict, session_id: str, request_timeout: float | None = None) -> str:
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
        system_parts.append("## Opening character message\n" + (opening if isinstance(opening, str) else json.dumps(opening, ensure_ascii=False)))
    if not conversation:
        conversation = [{"role": "user", "content": "Begin the conversation."}]
    body = {"model": actual_model, "messages": conversation, "max_tokens": int(settings["max_tokens"]), "temperature": float(settings["temperature"]), "stream": True}
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    if float(settings.get("top_p", 1.0)) < 1.0:
        body["top_p"] = float(settings["top_p"])
    stops = _parse_stop_sequences(settings.get("stop_sequences"))
    if stops:
        body["stop_sequences"] = stops[:4]
    endpoint = str(spec.get("api_endpoint") or spec.get("api") or "").rstrip("/")
    validate_provider_endpoint(endpoint)
    endpoint = endpoint if endpoint.endswith("/messages") else endpoint + "/messages"
    headers = {"x-api-key": api_key, "anthropic-version": str(spec.get("anthropic_version") or "2023-06-01"), "Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": "SillyTavernTelegramBridge/1.0"}
    headers.update(spec.get("extra_headers") or {})
    request = urllib.request.Request(endpoint, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    with strict_urlopen(request, timeout=240 if request_timeout is None else request_timeout) as response:
        parts = []
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].lstrip())
            except json.JSONDecodeError:
                continue
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                parts.append(str(delta["text"]))
        content = "".join(parts).strip()
        if not content:
            raise RuntimeError("Anthropic Messages returned no visible content")
        return content

def opencode_muse_headers(session_id: str) -> dict[str, str]:
    base62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    digest = hashlib.sha256(f"opencode\\0{session_id}".encode("utf-8")).digest()
    session_key = f"ses_{digest[:6].hex()}" + "".join(base62[value % 62] for value in digest[6:20])
    request_digest = hashlib.sha256(f"opencode-request\\0{session_key}\\0{time.time_ns()}".encode("utf-8")).digest()
    request_id = f"msg_{request_digest[:6].hex()}" + "".join(base62[value % 62] for value in request_digest[6:20])
    version = os.environ.get("OPENCODE_CLIENT_VERSION", "1.18.31")
    return {"Authorization": "", "x-opencode-session": session_key, "x-opencode-request": request_id, "x-opencode-client": "cli", "User-Agent": f"opencode/{version}", "Origin": "https://opencode.ai", "Referer": "https://opencode.ai/", "HTTP-Referer": "https://opencode.ai/", "X-Title": "opencode", "Content-Type": "application/json", "Accept": "application/json"}

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
        tools.append({
            "type": "function",
            "name": name,
            "description": "This tool is currently unavailable and must not be used.",
            "parameters": {"type": "object", "properties": {}},
        })
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

def _opencode_responses_text(raw: str) -> str:
    """Read both JSON and the SSE stream required by OpenCode Free."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        return _opencode_json_text(payload)

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
        delta = event.get("delta")
        if isinstance(delta, str):
            chunks.append(delta)
        response = event.get("response")
        if isinstance(response, dict):
            completed_text = completed_text or _opencode_json_text(response)
    return "".join(chunks).strip() or completed_text

def opencode_muse_generate(actual_model: str, messages: list[dict], settings: dict[str, object], spec: dict, session_id: str, request_timeout: float | None = None) -> str:
    endpoint = str(spec.get("api_endpoint") or spec.get("api") or "https://opencode.ai/zen/v1").rstrip("/")
    validate_provider_endpoint(endpoint)
    inputs = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            content = "\\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
        inputs.append({"role": str(message.get("role") or "user"), "content": [{"type": "input_text", "text": str(content or "")}]})
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
    request = urllib.request.Request(
        endpoint + "/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={**opencode_muse_headers(session_id), "Accept": "text/event-stream"},
        method="POST",
    )
    with strict_urlopen(request, timeout=240 if request_timeout is None else request_timeout) as response:
        raw = response.read().decode("utf-8", "replace")
    output_text = _opencode_responses_text(raw)
    if not output_text:
        raise RuntimeError("OpenCode Muse returned no assistant content")
    return output_text

def generate_provider_text(model_router: ModelRouter, api_key: str, model: str, messages: list[dict], session_id: str = "telegram", settings: dict[str, object] | None = None, stream_callback=None, cancel_event=None, force_non_stream: bool = False, request_timeout: float | None = None, _recovery_attempt: int = 0) -> str:
    """Generate through the selected bridge provider adapter."""
    route = model_router.route(model)
    provider_id = route.provider_id
    actual_model = route.model_id
    spec = dict(route.spec)
    transport = str(spec.get("transport") or "chat_completions")
    generation = dict(GENERATION_DEFAULTS)
    generation.update(settings or {})
    if transport == "opencode_muse":
        return opencode_muse_generate(actual_model, messages, generation, spec, session_id, request_timeout=request_timeout)
    if transport == "anthropic_messages":
        anthropic_key = _resolve_provider_credential(spec, api_key, "ANTHROPIC_API_KEY", "Anthropic")
        return anthropic_generate(anthropic_key, actual_model, messages, generation, spec, session_id, request_timeout=request_timeout)
    if transport not in {"chat_completions", "openai", "openai_compatible"}:
        raise RuntimeError(f"Provider transport '{transport}' is not supported")
    endpoint_base = str(spec.get("api_endpoint") or spec.get("api") or DEFAULT_PROVIDER_URL.rsplit("/chat/completions", 1)[0]).rstrip("/")
    validate_provider_endpoint(endpoint_base)
    endpoint = endpoint_base + "/chat/completions"
    request_key = _resolve_provider_credential(spec, api_key, "LLM_API_KEY", "provider")
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
    }
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
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with strict_urlopen(request, timeout=(240 if is_streaming else 180) if request_timeout is None else request_timeout) as response:
        if not is_streaming:
            result = json.loads(response.read().decode("utf-8"))
            choices = _openai_response_choices(result)
            finish_reason = choices[0].get("finish_reason") if choices else None
            content = choices[0].get("message", {}).get("content") if choices else None
            if not content:
                if finish_reason == "length" and _recovery_attempt < 2:
                    recovered = _recovery_settings(generation)
                    if recovered:
                        return generate_provider_text(model_router, api_key, model, messages, session_id=session_id, settings=recovered, force_non_stream=True, request_timeout=request_timeout, _recovery_attempt=_recovery_attempt + 1)
                http_status = getattr(response, "status", None)
                if http_status is None:
                    getcode = getattr(response, "getcode", None)
                    http_status = getcode() if callable(getcode) else None
                logging.getLogger(__name__).warning(
                    "Provider response missing assistant content: "
                    "provider=%s model=%s http_status=%s choice_count=%s "
                    "finish_reason=%s response_keys=%s",
                    provider_id,
                    actual_model,
                    http_status,
                    len(choices),
                    finish_reason,
                    sorted(result.keys()),
                )
                raise RuntimeError("backend returned no assistant content")
            content = str(content).strip()
            if finish_reason != "length":
                return content

            segments = [content]
            continuation_messages = list(body["messages"])
            for _attempt in range(3):
                continuation_messages.extend([
                    {"role": "assistant", "content": segments[-1]},
                    {
                        "role": "user",
                        "content": _CONTINUATION_INSTRUCTION,
                    },
                ])
                continuation_body = dict(body)
                continuation_body["messages"] = continuation_messages
                continuation_request = urllib.request.Request(
                    endpoint,
                    data=json.dumps(continuation_body).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                try:
                    with strict_urlopen(continuation_request, timeout=180 if request_timeout is None else request_timeout) as continuation_response:
                        continuation_result = json.loads(continuation_response.read().decode("utf-8"))
                    continuation_choices = _openai_response_choices(continuation_result)
                    continuation = continuation_choices[0].get("message", {}).get("content") if continuation_choices else None
                    continuation_reason = continuation_choices[0].get("finish_reason") if continuation_choices else None
                except Exception:
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
        )
        if not content:
            can_recover = (
                finish_reason == "length"
                and _recovery_attempt < 2
                and not cancelled
                and not (
                    cancel_event is not None
                    and cancel_event.is_set()
                )
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
                    )
            raise RuntimeError(
                f"{provider_id} returned no visible content "
                f"(finish_reason={finish_reason})"
            )

        segments = [content]
        if (
            finish_reason != "length"
            or cancelled
            or (
                cancel_event is not None
                and cancel_event.is_set()
            )
        ):
            return content

        continuation_messages = list(messages)
        for _attempt in range(_MAX_VISIBLE_CONTINUATIONS):
            if (
                cancel_event is not None
                and cancel_event.is_set()
            ):
                break

            continuation_messages.extend([
                {
                    "role": "assistant",
                    "content": segments[-1],
                },
                {
                    "role": "user",
                    "content": _CONTINUATION_INSTRUCTION,
                },
            ])
            continuation_body = dict(body)
            continuation_body["messages"] = continuation_messages
            continuation_request = urllib.request.Request(
                endpoint,
                data=json.dumps(continuation_body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with strict_urlopen(
                    continuation_request,
                    timeout=(
                        240
                        if request_timeout is None
                        else request_timeout
                    ),
                ) as continuation_response:
                    prefix = " ".join(
                        segment
                        for segment in segments
                        if segment
                    )
                    (
                        continuation,
                        continuation_reason,
                        continuation_cancelled,
                    ) = _read_openai_stream_segment(
                        continuation_response,
                        prefix=prefix,
                        stream_callback=stream_callback,
                        cancel_event=cancel_event,
                    )
            except Exception:
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
                or (
                    cancel_event is not None
                    and cancel_event.is_set()
                )
                or continuation_reason != "length"
            ):
                break

        return " ".join(
            segment
            for segment in segments
            if segment
        )

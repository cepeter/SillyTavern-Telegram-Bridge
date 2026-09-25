"""Telegram HTTP transport, bounded file transfers and session-owned message delivery."""

from __future__ import annotations

import json
import logging
import re
import time
import urllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from bridge.limits import MAX_TELEGRAM_LENGTH, SYNC_MAX_BYTES
from bridge.panel_bindings import bind_panel_session
from bridge.request_types import RequestContext
from bridge.settings import AppSettings
from bridge.topic_scope import parse_topic_scope


def allowed_users(*, app_settings: AppSettings) -> set[str]:
    value = app_settings.environ.get("SILLYTAVERN_TELEGRAM_ALLOWED_USERS", app_settings.default_allowed_user)
    return {x.strip() for x in value.split(",") if x.strip()}


def telegram_request(token: str, method: str, payload: dict | None = None) -> dict:
    request_payload = dict(payload or {})
    if request_payload.get("chat_id") is not None:
        chat_id, thread_id = parse_topic_scope(str(request_payload["chat_id"]))
        request_payload["chat_id"] = chat_id
        if thread_id is not None and method in {
            "sendMessage",
            "sendPhoto",
            "sendVoice",
            "sendDocument",
            "sendChatAction",
        }:
            request_payload["message_thread_id"] = thread_id
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(request_payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    for attempt in range(3 if method == "sendMessage" else 1):
        try:
            with urllib.request.urlopen(req, timeout=65) as response:  # noqa: S310 -- fixed Telegram HTTPS endpoint; token is not a URL
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("description") or exc.reason
            except (OSError, ValueError, json.JSONDecodeError):
                detail = exc.reason
            if method == "sendMessage" and exc.code == 404 and attempt < 2:
                logging.warning("Telegram sendMessage returned 404; retrying (attempt %s/3)", attempt + 2)
                time.sleep(0.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Telegram {method} failed: {detail}") from exc
        if not result.get("ok"):
            detail = result.get("description") or "unknown Telegram error"
            if method == "sendMessage" and detail == "Not Found" and attempt < 2:
                logging.warning("Telegram sendMessage returned Not Found; retrying (attempt %s/3)", attempt + 2)
                time.sleep(0.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Telegram {method} failed: {detail}")
        break
    return result["result"]


def delete_pending_input_prompts(token: str, chat_id: str, state: dict) -> None:
    """Remove prompt messages created for a pending text-input transition."""
    message_ids = state.get("prompt_message_ids") or []
    if isinstance(message_ids, (int, str)):
        message_ids = [message_ids]
    for message_id in message_ids:
        try:
            telegram_request(
                token,
                "deleteMessage",
                {"chat_id": chat_id, "message_id": int(message_id)},
            )
        except Exception:
            logging.info(
                "Pending input prompt already unavailable",
                exc_info=True,
            )


def send_panel_request(token: str, method: str, payload: dict, *, request_context: RequestContext) -> dict:
    scoped_chat_id = str(payload.get("chat_id", "")) if payload.get("chat_id") is not None else ""
    result = telegram_request(token, method, payload)
    if payload.get("reply_markup") and method in {"sendMessage", "editMessageText"}:
        bound_message_id = result.get("message_id") if isinstance(result, dict) else None
        bound_message_id = bound_message_id or payload.get("message_id")
        if request_context.session_id and bound_message_id:
            bind_panel_session(
                request_context.db,
                scoped_chat_id,
                bound_message_id,
                request_context.session_id,
                request_context.actor_id,
            )
    return result


def send_panel_photo(
    token: str,
    chat_id: str,
    photo_path: Path,
    caption: str,
    reply_markup: dict,
    *,
    request_context: RequestContext,
) -> dict:
    """Send a local PNG as a session-owned Telegram photo panel."""
    real_chat_id, thread_id = parse_topic_scope(str(chat_id))
    raw = Path(photo_path).read_bytes()
    if not raw:
        raise ValueError("character photo is empty")
    boundary = f"----BridgePanelPhoto{time.time_ns()}"
    fields = [("chat_id", real_chat_id), ("caption", str(caption)[:1024]), ("reply_markup", json.dumps(reply_markup))]
    if thread_id is not None:
        fields.append(("message_thread_id", str(thread_id)))
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    chunks.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="character.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode("utf-8")
        + raw
        + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=65) as response:  # noqa: S310 -- fixed Telegram HTTPS endpoint
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok") or not isinstance(payload.get("result"), dict):
        detail = payload.get("description") or "unknown Telegram error"
        raise RuntimeError(f"Telegram sendPhoto failed: {detail}")
    result = payload["result"]
    message_id = result.get("message_id")
    if request_context.session_id and message_id:
        bind_panel_session(
            request_context.db,
            str(chat_id),
            message_id,
            request_context.session_id,
            request_context.actor_id,
        )
    return result


def download_telegram_file(token: str, file_id: str, max_bytes: int = SYNC_MAX_BYTES) -> bytes:
    file_info = telegram_request(token, "getFile", {"file_id": file_id})
    file_path = file_info.get("file_path")
    if not file_path:
        raise ValueError("Telegram did not return a file path")
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    with urllib.request.urlopen(urllib.request.Request(url), timeout=120) as response:
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"Telegram file exceeds {max_bytes // (1024 * 1024)} MB")
    return raw


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _semantic_boundary(text: str, start: int, end: int) -> int:
    """Choose the latest safe paragraph, sentence, or whitespace boundary."""
    segment = text[start:end]
    patterns = (r"\n\s*\n", r"\n", r"(?<=[.!?。！？])\s+", r"\s+")
    for pattern in patterns:
        candidates = []
        for match in re.finditer(pattern, segment):
            position = start + match.end()
            if position <= end and segment[: match.end()].count("```") % 2 == 0:
                candidates.append(position)
        if candidates:
            return max(candidates)
    return end


def split_telegram_text(text: str, limit: int = MAX_TELEGRAM_LENGTH) -> list[str]:
    """Split text semantically while respecting Telegram's UTF-16 limit."""
    text = str(text)
    if limit <= 0:
        raise ValueError("Telegram message limit must be positive")
    if _utf16_length(text) <= limit:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        units = 0
        hard_end = start
        for index in range(start, len(text)):
            width = _utf16_length(text[index])
            if units and units + width > limit:
                break
            units += width
            hard_end = index + 1
        if hard_end == start:
            hard_end = start + 1
        boundary = len(text) if hard_end == len(text) else _semantic_boundary(text, start, hard_end)
        if boundary <= start:
            boundary = hard_end
        chunks.append(text[start:boundary])
        start = boundary
    return chunks


def send_text(token: str, chat_id: str, text: str) -> list[int]:
    message_ids = []
    for chunk in split_telegram_text(text):
        result = telegram_request(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
        )
        if result.get("message_id") is not None:
            message_ids.append(int(result["message_id"]))
    return message_ids


def send_typing(token: str, chat_id: str) -> None:
    try:
        telegram_request(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"})
    except Exception:
        logging.debug("typing indicator failed", exc_info=True)


def answer_callback(token: str, callback_id: str, text: str) -> None:
    telegram_request(
        token,
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text[:200],
        },
    )

"""Per-session model response language selection."""
from __future__ import annotations

from bridge.common import (
    sqlite3,
)

from bridge.panel_utils import (
    panel_navigation,
    panel_page,
)

import re
from collections.abc import Callable

from bridge.delivery_port import DeliveryPort

RESPONSE_LANGUAGES = (
    ("auto", "Auto — match user"),
    ("id", "Bahasa Indonesia"),
    ("en", "English"),
    ("ja", "日本語"),
    ("zh", "中文"),
    ("ko", "한국어"),
    ("es", "Español"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("pt", "Português"),
    ("ru", "Русский"),
    ("ar", "العربية"),
    ("hi", "हिन्दी"),
    ("vi", "Tiếng Việt"),
    ("th", "ไทย"),
)

_LANGUAGE_ALIASES = {
    "automatic": "auto",
    "same": "auto",
    "user": "auto",
    "indonesian": "id",
    "bahasa": "id",
    "bahasa indonesia": "id",
    "japanese": "ja",
    "chinese": "zh",
    "korean": "ko",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "portuguese": "pt",
    "russian": "ru",
    "arabic": "ar",
    "hindi": "hi",
    "vietnamese": "vi",
    "thai": "th",
}

_LANGUAGE_NAMES = dict(RESPONSE_LANGUAGES)


def normalize_response_language(value: str | None) -> str:
    """Return a supported language code or raise ValueError."""
    normalized = str(value or "").strip().casefold()
    normalized = _LANGUAGE_ALIASES.get(normalized, normalized)
    if normalized not in _LANGUAGE_NAMES:
        raise ValueError("use auto, id, en, ja, zh, ko, es, fr, de, pt, ru, ar, hi, vi, or th")
    return normalized


def response_language_label(value: str | None) -> str:
    return _LANGUAGE_NAMES.get(normalize_response_language(value), _LANGUAGE_NAMES["auto"])


def response_language_instruction(value: str | None) -> str:
    language = normalize_response_language(value or "auto")
    if language == "auto":
        return "Reply in the same language as the user's latest message. Keep the language consistent unless the user explicitly requests a change."
    label = _LANGUAGE_NAMES[language]
    return (
        f"The selected output language is {label} ({language}). You MUST write all visible response text in {label}. "
        "Do not switch because the character card, persona, memory, World Info, examples, or conversation history uses another language. "
        "Only switch when the user's latest message explicitly asks for a different response language."
    )


def normalize_stt_language(value: str | None) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized in {"auto", "detect"}:
        return "auto"
    if not re.fullmatch(r"[a-z]{2,8}", normalized):
        raise ValueError("use auto or a 2–8 letter language code such as id, en, or ja")
    return normalized


def stt_language_label(value: str | None) -> str:
    language = normalize_stt_language(value or "auto")
    return _LANGUAGE_NAMES.get(language, f"User code: {language}")


def language_menu_markup(current: str, page: int = 0) -> dict:
    current = normalize_response_language(current or "auto")
    page_options, current_page, total_pages = panel_page(list(RESPONSE_LANGUAGES), page)
    rows = []
    for code, label in page_options:
        mark = "✅ " if code == current else ""
        rows.append([{"text": mark + label, "callback_data": "language:" + code}])
    navigation = panel_navigation("language", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "❌ Close", "callback_data": "language:cancel"}])
    return {"inline_keyboard": rows}


def send_language_menu(token: str, chat_id: str, current: str, message_id: int | None = None, page: int = 0, *, delivery_port: DeliveryPort, request_context) -> None:
    current = normalize_response_language(current or "auto")
    _options, current_page, total_pages = panel_page(list(RESPONSE_LANGUAGES), page)
    page_text = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"Model response language{page_text}\nCurrent: {response_language_label(current)}\nChoose the language for generated replies:"
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": language_menu_markup(current, page)}
    if message_id:
        payload["message_id"] = message_id
    delivery_port.send_panel_request(token, method, payload, request_context=request_context)


def set_response_language(db: sqlite3.Connection, chat_id: str, session_id: str, value: str, operation_id: int | str | None = None, *, update_session: Callable[..., object]) -> str:
    language = normalize_response_language(value)
    update_session(db, chat_id, session_id, operation_id=operation_id, operation_kind="language_select", response_language=language)
    return language


def handle_language_command(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], command_text: str, operation_id: int | str | None = None, *, delivery_port: DeliveryPort, update_session: Callable[..., object], request_context) -> None:
    parts = command_text.strip().split(None, 1)
    if len(parts) == 1 or parts[1].strip().casefold() in {"list", "status"}:
        send_language_menu(token, chat_id, session.get("response_language") or "auto", delivery_port=delivery_port, request_context=request_context)
        return
    try:
        language = set_response_language(db, chat_id, session["session_id"], parts[1], operation_id=operation_id, update_session=update_session)
    except ValueError as exc:
        delivery_port.send_text(token, chat_id, f"Invalid response language: {exc}")
        return
    delivery_port.send_text(token, chat_id, f"Model response language set to: {response_language_label(language)}.")

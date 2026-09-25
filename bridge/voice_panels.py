"""Canonical voice panels owner."""

from __future__ import annotations

import sqlite3

from bridge.cards import send_panel_message
from bridge.config import STT_DEFAULT_MODEL
from bridge.language import RESPONSE_LANGUAGES, normalize_stt_language, stt_language_label
from bridge.metadata import get_meta
from bridge.panel_utils import panel_navigation, panel_page


def send_voice_menu(
    token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None, *, request_context
) -> None:
    current = get_meta(db, f"voice_mode:{chat_id}", "off")
    rows = [
        [
            {"text": ("✅ " if current == "tts" else "") + "Voice replies on", "callback_data": "enum:voice:on"},
            {"text": ("✅ " if current != "tts" else "") + "Voice replies off", "callback_data": "enum:voice:off"},
        ],
        [{"text": "❌ Close", "callback_data": "enum:close"}],
    ]
    send_panel_message(
        token,
        chat_id,
        f"Automatic voice replies: {'on' if current == 'tts' else 'off'}",
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_voice_input_menu(
    token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None, *, request_context
) -> None:
    mode = get_meta(db, f"stt_mode:{chat_id}", "on")
    model = get_meta(db, f"stt_model:{chat_id}", STT_DEFAULT_MODEL)
    language = normalize_stt_language(get_meta(db, f"stt_language:{chat_id}", "auto"))
    rows = [
        [
            {"text": ("✅ " if mode == "on" else "") + "Transcription on", "callback_data": "enum:stt:on"},
            {"text": ("✅ " if mode == "off" else "") + "Transcription off", "callback_data": "enum:stt:off"},
        ],
        [{"text": f"Model: {model}", "callback_data": "enum:stt:model"}],
        [{"text": f"Language: {stt_language_label(language)}", "callback_data": "enum:stt:language"}],
        [{"text": "❌ Close", "callback_data": "enum:close"}],
    ]
    send_panel_message(
        token,
        chat_id,
        f"Voice input transcription: {mode}\nModel: {model}\nLanguage: {stt_language_label(language)}",
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_stt_language_menu(
    token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None, page: int = 0, *, request_context
) -> None:
    current = normalize_stt_language(get_meta(db, f"stt_language:{chat_id}", "auto"))
    options = list(RESPONSE_LANGUAGES)
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for code, label in page_options:
        mark = "✅ " if code == current else ""
        rows.append([{"text": mark + label, "callback_data": "enum:sttlanguage:" + code}])
    navigation = panel_navigation("enum:sttlanguagepage", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "✏️ User input", "callback_data": "enum:stt:language_input"}])
    rows.append(
        [{"text": "⬅️ Back", "callback_data": "enum:stt:back"}, {"text": "❌ Close", "callback_data": "enum:close"}]
    )
    send_panel_message(
        token,
        chat_id,
        (
            "Choose voice input language (page "
            f"""{current_page + 1}"""
            "/"
            f"""{total_pages}"""
            "). Auto detects the language; User input accepts a custom 2–8 letter "
            "code."
        ),
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_stt_model_menu(
    token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None, *, request_context
) -> None:
    current = get_meta(db, f"stt_model:{chat_id}", STT_DEFAULT_MODEL)
    rows = [
        [{"text": ("✅ " if model == current else "") + model, "callback_data": "enum:sttmodel:" + model}]
        for model in ("tiny", "base", "small")
    ]
    rows.append(
        [{"text": "⬅️ Back", "callback_data": "enum:stt:back"}, {"text": "❌ Close", "callback_data": "enum:close"}]
    )
    send_panel_message(
        token, chat_id, "Choose STT model:", {"inline_keyboard": rows}, message_id, request_context=request_context
    )

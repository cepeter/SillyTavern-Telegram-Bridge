"""Canonical settings panels owner."""

from __future__ import annotations

import sqlite3

from bridge.cards import send_panel_message
from bridge.config import REASONING_LEVELS
from bridge.generation_settings import get_generation_settings
from bridge.humanizer_settings import humanizer_enabled, session_humanizer
from bridge.metadata import get_meta


def send_settings_menu(
    token: str, chat_id: str, db: sqlite3.Connection, session_id: str, message_id: int | None = None, *, request_context
) -> None:
    settings = get_generation_settings(db, chat_id, session_id)
    levels = list(REASONING_LEVELS.items())
    current = int(settings.get("reasoning_budget", 0))
    current_label = next((label.title() for label, budget in levels if budget == current), "Custom")
    humanizer_on = humanizer_enabled(session_humanizer(db, chat_id, session_id))
    rows = [
        [
            {
                "text": ("✅ " if budget == current else "") + f"Reasoning: {label.title()}",
                "callback_data": f"enum:settings:reasoning:{label}",
            }
        ]
        for label, budget in levels
    ]
    rows.append([{"text": "✏️ Custom reasoning budget", "callback_data": "enum:settings:input:reasoning_budget"}])
    rows.append(
        [
            {"text": "✏️ Temperature", "callback_data": "enum:settings:input:temperature"},
            {"text": "✏️ Max tokens", "callback_data": "enum:settings:input:max_tokens"},
        ]
    )
    rows.append(
        [
            {"text": "✏️ Top P", "callback_data": "enum:settings:input:top_p"},
            {"text": "✏️ Frequency penalty", "callback_data": "enum:settings:input:frequency_penalty"},
        ]
    )
    rows.append(
        [
            {"text": "✏️ Presence penalty", "callback_data": "enum:settings:input:presence_penalty"},
            {"text": "✏️ Stop sequences", "callback_data": "enum:settings:input:stop_sequences"},
        ]
    )
    rows.append(
        [
            {
                "text": ("✅ " if humanizer_on else "") + "Humanizer: On",
                "callback_data": "enum:humanizer:on",
            },
            {
                "text": ("" if humanizer_on else "✅ ") + "Humanizer: Off",
                "callback_data": "enum:humanizer:off",
            },
        ]
    )
    rows.append(
        [
            {"text": "↩️ Reset all settings", "callback_data": "enum:settings:reset"},
            {"text": "❌ Close", "callback_data": "enum:close"},
        ]
    )
    stop_sequences = settings.get("stop_sequences") or ""
    if isinstance(stop_sequences, (list, tuple)):
        stop_label = ", ".join(str(value) for value in stop_sequences) if stop_sequences else "none"
    else:
        stop_label = str(stop_sequences) or "none"
    current_values = (
        f"temperature={settings['temperature']}, max_tokens={settings['max_tokens']}, "
        f"top_p={settings['top_p']}, frequency_penalty={settings['frequency_penalty']}, "
        f"presence_penalty={settings['presence_penalty']}, stop_sequences={stop_label}, "
        f"humanizer={'on' if humanizer_on else 'off'}"
    )
    send_panel_message(
        token,
        chat_id,
        (
            "Generation settings — current session\nActive reasoning: "
            f"""{current_label}"""
            " ("
            f"""{current}"""
            " budget)\nCurrent values: "
            f"""{current_values}"""
            "\n\nTap a reasoning level below to apply it.\nTap a field to enter its "
            "value in the next message. Send /cancel to leave it unchanged.\nFields: "
            "temperature, max_tokens, top_p, frequency_penalty, presence_penalty, "
            "stop_sequences.\n\nHumanizer rewrites replies to remove AI-sounding "
            "patterns. It runs after generation and is off by default."
        ),
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_stream_menu(
    token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None, *, request_context
) -> None:
    current = get_meta(db, f"stream_mode:{chat_id}", "on")
    rows = [
        [
            {"text": ("✅ " if current == "on" else "") + "Streaming on", "callback_data": "enum:stream:on"},
            {"text": ("✅ " if current == "off" else "") + "Streaming off", "callback_data": "enum:stream:off"},
        ],
        [{"text": "❌ Close", "callback_data": "enum:close"}],
    ]
    send_panel_message(
        token,
        chat_id,
        f"Live response streaming: {current}",
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )

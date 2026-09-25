"""Canonical preset panels owner."""

from __future__ import annotations

import sqlite3

from bridge.callback_tokens import dynamic_callback_token
from bridge.cards import send_panel_message
from bridge.generation_settings import preset_names
from bridge.panel_utils import panel_label, panel_page


def send_preset_menu(
    token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None, page: int = 0, *, request_context
) -> None:
    options = preset_names(db, chat_id)
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for name in page_options:
        callback_token = dynamic_callback_token("preset", name, chat_id, db=request_context.db)
        rows.append(
            [
                {"text": "📋 " + panel_label(name), "callback_data": "enum:presetuse:" + callback_token},
                {"text": "🗑️", "callback_data": "enum:presetdel:" + callback_token},
            ]
        )
    if total_pages > 1:
        navigation = []
        if current_page > 0:
            navigation.append({"text": "⬅️ Previous", "callback_data": f"enum:presetpage:{current_page - 1}"})
        if current_page < total_pages - 1:
            navigation.append({"text": "Next ➡️", "callback_data": f"enum:presetpage:{current_page + 1}"})
        rows.append(navigation)
    rows.append([{"text": "💾 Save preset", "callback_data": "enum:preset:save"}])
    rows.append([{"text": "❌ Close", "callback_data": "enum:close"}])
    send_panel_message(
        token,
        chat_id,
        (
            "Choose a preset to apply (page "
            f"""{current_page + 1}"""
            "/"
            f"""{total_pages}"""
            "). Use Save preset for the two-step name input."
        ),
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_preset_delete_confirm(
    token: str, chat_id: str, name: str, message_id: int | None = None, *, request_context
) -> None:
    callback_token = dynamic_callback_token("preset", name, chat_id, db=request_context.db)
    markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Confirm delete", "callback_data": "enum:presetdelconfirm:" + callback_token},
                {"text": "❌ Cancel", "callback_data": "enum:preset:back"},
            ]
        ]
    }
    send_panel_message(
        token,
        chat_id,
        f"Delete preset '{panel_label(name)}'? This cannot be undone.",
        markup,
        message_id,
        request_context=request_context,
    )


def send_preset_delete_menu(
    token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None, page: int = 0, *, request_context
) -> None:
    options = preset_names(db, chat_id)
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [
        [
            {
                "text": panel_label(name),
                "callback_data": "enum:presetdel:"
                + dynamic_callback_token("preset", name, chat_id, db=request_context.db),
            }
        ]
        for name in page_options
    ]
    if total_pages > 1:
        navigation = []
        if current_page > 0:
            navigation.append({"text": "⬅️ Previous", "callback_data": f"enum:presetdeletepage:{current_page - 1}"})
        if current_page < total_pages - 1:
            navigation.append({"text": "Next ➡️", "callback_data": f"enum:presetdeletepage:{current_page + 1}"})
        rows.append(navigation)
    rows.append(
        [{"text": "⬅️ Back", "callback_data": "enum:preset:back"}, {"text": "❌ Close", "callback_data": "enum:close"}]
    )
    send_panel_message(
        token,
        chat_id,
        "Choose a preset to delete:",
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )

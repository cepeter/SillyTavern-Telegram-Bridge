"""Canonical world panels owner."""

from __future__ import annotations

import logging
from pathlib import Path

from bridge.callback_tokens import dynamic_callback_token
from bridge.card_content import active_world_files, world_file_paths
from bridge.cards import send_panel_message
from bridge.panel_utils import panel_label, panel_navigation, panel_page


def send_world_menu(
    token: str, chat_id: str, current_world: str, message_id: int | None = None, page: int = 0, *, request_context
) -> None:
    selected = set(active_world_files(current_world, app_settings=request_context.app_settings))
    options = [(path.name, path.stem) for path in world_file_paths(app_settings=request_context.app_settings)]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for name, label in page_options:
        callback_token = dynamic_callback_token("world", name, chat_id, db=request_context.db)
        mark = "✅ " if name in selected else ""
        rows.append(
            [
                {"text": mark + panel_label(label), "callback_data": "world:" + callback_token},
                {"text": "🗑️", "callback_data": "worlddelete:" + callback_token},
            ]
        )
    navigation = panel_navigation("world", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "📤 Upload World JSON", "callback_data": "world:upload"}])
    rows.append(
        [
            {"text": "🚫 Clear all World Info", "callback_data": "world:off"},
            {"text": "✅ Close", "callback_data": "world:done"},
        ]
    )
    selected_label = ", ".join(Path(name).stem for name in selected) if selected else "off"
    page_label = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"Active World Info: {selected_label}{page_label}\nTap lorebooks to toggle them:"
    try:
        send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id, request_context=request_context)
    except RuntimeError as exc:
        if "not modified" in str(exc).casefold():
            logging.info("Panel already shows the requested state")
            return
        raise

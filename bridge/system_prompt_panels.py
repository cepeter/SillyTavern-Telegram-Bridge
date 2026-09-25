"""Canonical system prompt panels owner."""

from __future__ import annotations

from bridge.card_content import get_system_prompt_choice, system_prompt_callback_token, system_prompt_choices
from bridge.cards import send_panel_message
from bridge.panel_utils import panel_navigation, panel_page
from bridge.settings import AppSettings


def _system_prompt_key(current: str, *, app_settings: AppSettings) -> str:
    if current and current in dict(system_prompt_choices(app_settings=app_settings)):
        return current
    for key, _name in system_prompt_choices(app_settings=app_settings):
        if get_system_prompt_choice(key, app_settings=app_settings) == current:
            return key
    return ""


def system_prompt_menu_markup(current: str, page: int = 0, *, app_settings: AppSettings) -> dict:
    current_key = _system_prompt_key(current, app_settings=app_settings)
    options = system_prompt_choices(app_settings=app_settings)
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for key, name in page_options:
        mark = "✅ " if key == current_key else ""
        rows.append([{"text": mark + name, "callback_data": "systemprompt:" + system_prompt_callback_token(key)}])
    navigation = panel_navigation("systemprompt", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            {"text": "🚫 Off", "callback_data": "systemprompt:off"},
            {"text": "❌ Close", "callback_data": "systemprompt:cancel"},
        ]
    )
    return {"inline_keyboard": rows}


def send_system_prompt_menu(
    token: str, chat_id: str, current: str, message_id: int | None = None, page: int = 0, *, request_context
) -> None:
    current_key = _system_prompt_key(current, app_settings=request_context.app_settings)
    labels = dict(system_prompt_choices(app_settings=request_context.app_settings))
    current_label = labels.get(current_key, "off")
    text = f"System Prompt choice\nCurrent: {current_label}\nChoose a TXT prompt:"
    send_panel_message(
        token,
        chat_id,
        text,
        system_prompt_menu_markup(current, page, app_settings=request_context.app_settings),
        message_id,
        request_context=request_context,
    )

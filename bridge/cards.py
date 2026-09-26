"""Persona and Telegram-facing card and panel orchestration.

Content, callback-token state, and pure panel helpers live in focused modules.
"""

from __future__ import annotations

from pathlib import Path

import bridge.limits as _limits
from bridge.callback_tokens import dynamic_callback_token
from bridge.callback_tokens import resolve_dynamic_callback_token as resolve_dynamic_callback_token
from bridge.card_content import active_world_files as active_world_files
from bridge.card_content import build_system_prompt as build_system_prompt
from bridge.card_content import build_world_info as build_world_info
from bridge.card_content import card_fields as card_fields
from bridge.card_content import card_fields_from_file, character_card_paths, character_display_name, safe_character_path
from bridge.card_content import encode_world_files as encode_world_files
from bridge.card_content import get_system_prompt_choice as get_system_prompt_choice
from bridge.card_content import load_system_prompts as load_system_prompts
from bridge.card_content import parse_png_chara_bytes as parse_png_chara_bytes
from bridge.card_content import read_png_chara as read_png_chara
from bridge.card_content import replace_macros as replace_macros
from bridge.card_content import safe_world_path as safe_world_path
from bridge.card_content import system_prompt_callback_token as system_prompt_callback_token
from bridge.card_content import system_prompt_choices as system_prompt_choices
from bridge.card_content import system_prompt_label as system_prompt_label
from bridge.card_content import world_file_paths as world_file_paths
from bridge.character_quality import RANK_TIERS, character_rank
from bridge.panel_utils import panel_label, panel_message_request, panel_navigation, panel_page
from bridge.persona_service import PersonaService
from bridge.request_types import RequestContext
from bridge.telegram import send_panel_request


def send_panel_message(
    token: str,
    chat_id: str,
    text: str,
    reply_markup: dict,
    message_id: int | None = None,
    *,
    request_context: RequestContext,
) -> None:
    """Send a panel message, or edit the existing one in place."""
    method, payload = panel_message_request(
        chat_id,
        text,
        reply_markup,
        message_id,
    )
    send_panel_request(
        token,
        method,
        payload,
        request_context=request_context,
    )


# Bot-owned Telegram custom-emoji set: sttb_ranks_by_SillyTavernPunzmeBot
_CHARACTER_RANK_CUSTOM_EMOJI_IDS: dict[str, str] = {
    "S": "6176891226302718197",
    "A": "6176955294329871952",
    "B": "6178981105849344346",
    "C": "6177219258724917819",
    "D": "6176733025477337215",
}


def character_rank_button(rank: str | None) -> dict[str, str]:
    """Build the silent rank-column button using the bot-owned animated rank set."""
    tier = str(rank or "").strip().upper()
    if tier not in RANK_TIERS:
        return {"text": "—", "callback_data": "character:rank:unranked"}
    return {
        "text": tier,
        "callback_data": f"character:rank:{tier}",
        "icon_custom_emoji_id": _CHARACTER_RANK_CUSTOM_EMOJI_IDS[tier],
    }


def send_persona_menu(
    token: str,
    chat_id: str,
    current_persona: str,
    message_id: int | None = None,
    page: int = 0,
    *,
    persona_service: PersonaService,
    request_context: RequestContext,
) -> None:
    personas = persona_service.list()
    options = [
        (persona_id, str(persona.get("name") or persona_id))
        for persona_id, persona in list(personas.items())[: _limits.CATALOG_MAX_ITEMS]
    ]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for persona_id, label in page_options:
        mark = "✅ " if persona_id == current_persona else ""
        callback_token = dynamic_callback_token("persona", persona_id, chat_id, db=request_context.db)
        rows.append(
            [
                {"text": mark + label, "callback_data": "persona:" + callback_token},
                {"text": "🗑️", "callback_data": "persona:delete:" + callback_token},
            ]
        )
    navigation = panel_navigation("persona", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "🚫 Persona off", "callback_data": "persona:off"}])
    if current_persona and current_persona in personas:
        rows.append([{"text": "✏️ Edit current persona", "callback_data": "persona:edit"}])
    rows.append([{"text": "➕ Create persona", "callback_data": "persona:create"}])
    rows.append([{"text": "❌ Cancel", "callback_data": "persona:cancel"}])
    page_label = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    text = (
        "Current Persona: "
        f"""{(persona_service.name(current_persona) if current_persona else "off")}"""
        f"""{page_label}"""
        "\nChoose a persona:"
    )
    send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id, request_context=request_context)


def send_character_menu(
    token: str,
    chat_id: str,
    current_character: str,
    message_id: int | None = None,
    page: int = 0,
    *,
    request_context: RequestContext,
) -> None:
    options = [
        (path.name, character_display_name(path, app_settings=request_context.app_settings))
        for path in character_card_paths(app_settings=request_context.app_settings)
    ]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for filename, label in page_options:
        mark = "✅ " if filename == current_character else ""
        callback_token = dynamic_callback_token("character", filename, chat_id, db=request_context.db)
        card_path = safe_character_path(filename, app_settings=request_context.app_settings)
        protected = (
            filename == current_character
            or filename == request_context.app_settings.default_character_file
            or (card_path is not None and card_path.resolve() == request_context.app_settings.card_file.resolve())
        )
        action = (
            {"text": "🔒", "callback_data": "character:protected"}
            if protected
            else {"text": "🗑️", "callback_data": "characterdelete:" + callback_token}
        )
        rank = character_rank(request_context.db, filename, app_settings=request_context.app_settings)
        rows.append(
            [
                character_rank_button(rank),
                {
                    "text": mark + panel_label(label),
                    "callback_data": "character:" + callback_token,
                },
                action,
            ]
        )
    navigation = panel_navigation("character", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            {"text": "ℹ️ Character info", "callback_data": "character:info"},
            {"text": "🔄 Refresh", "callback_data": "character:menu"},
        ]
    )
    rows.append([{"text": "⚡ Optimizer", "callback_data": "character:optimize"}])
    rows.append([{"text": "📤 Upload character card", "callback_data": "character:upload"}])
    rows.append([{"text": "❌ Cancel", "callback_data": "character:cancel"}])
    current_label = current_character
    if safe_character_path(current_character, app_settings=request_context.app_settings):
        current_label = card_fields_from_file(current_character, app_settings=request_context.app_settings)["name"]
    page_label = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"Current character: {current_label}{page_label}\nChoose a character card:"
    try:
        send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id, request_context=request_context)
    except RuntimeError as exc:
        if message_id is not None and "not modified" in str(exc).casefold():
            return
        raise


def send_character_info_menu(
    token: str, chat_id: str, message_id: int | None = None, page: int = 0, *, request_context: RequestContext
) -> None:
    options = [
        (path.name, character_display_name(path, app_settings=request_context.app_settings))
        for path in character_card_paths(app_settings=request_context.app_settings)
    ]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [
        [
            {
                "text": label,
                "callback_data": "characterinfo:"
                + dynamic_callback_token("character", filename, chat_id, db=request_context.db),
            }
        ]
        for filename, label in page_options
    ]
    navigation = panel_navigation("characterinfo", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            {"text": "⬅️ Back", "callback_data": "character:menu"},
            {"text": "❌ Close", "callback_data": "character:cancel"},
        ]
    )
    text = f"Choose a character for info (page {current_page + 1}/{total_pages}):"
    send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id, request_context=request_context)


def send_character_delete_menu(
    token: str,
    chat_id: str,
    active_character: str,
    message_id: int | None = None,
    page: int = 0,
    *,
    request_context: RequestContext,
) -> None:
    options = [
        (path.name, character_display_name(path, app_settings=request_context.app_settings))
        for path in character_card_paths(app_settings=request_context.app_settings)
        if path.name != active_character
    ]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [
        [
            {
                "text": label,
                "callback_data": "characterdelete:"
                + dynamic_callback_token("character", filename, chat_id, db=request_context.db),
            }
        ]
        for filename, label in page_options
    ]
    navigation = panel_navigation("characterdelete", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            {"text": "⬅️ Back", "callback_data": "character:menu"},
            {"text": "❌ Close", "callback_data": "character:cancel"},
        ]
    )
    text = f"Choose a non-active character to delete (page {current_page + 1}/{total_pages}):"
    send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id, request_context=request_context)


def send_character_delete_confirm(
    token: str, chat_id: str, filename: str, message_id: int | None = None, *, request_context: RequestContext
) -> None:
    token_value = dynamic_callback_token("character", filename, chat_id, db=request_context.db)
    payload: dict = {
        "chat_id": chat_id,
        "text": f"Delete {Path(filename).stem}? The card file will be removed; verified backups are kept.",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirm delete", "callback_data": "characterdeleteconfirm:" + token_value},
                    {"text": "❌ Cancel", "callback_data": "character:menu"},
                ]
            ]
        },
    }
    send_panel_message(
        token, chat_id, payload["text"], payload["reply_markup"], message_id, request_context=request_context
    )


def send_session_menu(
    token: str,
    chat_id: str,
    sessions: list[dict[str, str]],
    current_id: str,
    message_id: int | None = None,
    page: int = 0,
    *,
    request_context: RequestContext,
) -> None:
    options = [(session["session_id"], session["title"] or session["session_id"]) for session in sessions]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for session_id, label in page_options:
        mark = "✅ " if session_id == current_id else ""
        delete_token = dynamic_callback_token("session", session_id, chat_id, db=request_context.db)
        action = (
            {"text": "🔒", "callback_data": "session:protected"}
            if session_id == current_id
            else {"text": "🗑️", "callback_data": "sessiondelete:" + delete_token}
        )
        rows.append(
            [
                {"text": mark + panel_label(label), "callback_data": "session:" + session_id},
                action,
            ]
        )
    navigation = panel_navigation("session", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "➕ New session", "callback_data": "session:new"}])
    rows.append([{"text": "❌ Cancel", "callback_data": "session:cancel"}])
    page_label = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    text = (
        "Current session: "
        f"""{current_id}"""
        f"""{page_label}"""
        "\nChoose a session, create a new one, or delete an inactive session with "
        "its session-scoped Hindsight documents."
    )
    send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id, request_context=request_context)

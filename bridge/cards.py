"""Persona and Telegram-facing card/panel compatibility shell.

Content, callback-token state, and pure panel helpers live in ordinary
modules. This file remains exec-loaded until the Phase 7 UI migration.
"""
from __future__ import annotations

from pathlib import Path
import logging

from bridge import config as _config
from bridge.callback_tokens import (
    dynamic_callback_token,
    resolve_dynamic_callback_token,
)
from bridge.card_content import (
    active_world_files,
    build_system_prompt,
    build_world_info,
    card_fields,
    card_fields_from_file,
    character_card_paths,
    character_display_name,
    encode_world_files,
    get_system_prompt_choice,
    load_system_prompts,
    parse_png_chara_bytes,
    read_png_chara,
    replace_macros,
    safe_character_path,
    safe_world_path,
    system_prompt_callback_token,
    system_prompt_choices,
    system_prompt_label,
    world_file_paths,
)
from bridge.panel_utils import (
    panel_label,
    panel_navigation,
    panel_page,
)

def get_persona(persona_id: str) -> dict[str, str] | None:
    return load_personas().get(persona_id)


def default_persona_id() -> str:
    """Resolve the native default Persona without exposing a private identity."""
    try:
        personas = load_personas()
        settings = _native_settings()
        power_user = settings.get("power_user") if isinstance(settings, dict) else {}
        configured = str((power_user or {}).get("default_persona") or "").strip()
        if configured in personas:
            return configured
    except Exception:
        logging.warning("Could not resolve the native default Persona", exc_info=True)
    return ""


def persona_name(persona_id: str) -> str:
    persona = get_persona(persona_id)
    return str(persona.get("name") or "") if persona else ""




def send_panel_message(token: str, chat_id: str, text: str, reply_markup: dict, message_id: int | None = None) -> None:
    """Send a panel message, or edit the existing one in place."""
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_persona_menu(token: str, chat_id: str, current_persona: str, message_id: int | None = None, page: int = 0, *, persona_service=None) -> None:
    persona_service = resolve_persona_service(persona_service)
    personas = persona_service.list()
    options = [(persona_id, str(persona.get("name") or persona_id)) for persona_id, persona in list(personas.items())[:_config.CATALOG_MAX_ITEMS]]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for persona_id, label in page_options:
        mark = "✅ " if persona_id == current_persona else ""
        callback_token = dynamic_callback_token("persona", persona_id, chat_id)
        rows.append([
            {"text": mark + label, "callback_data": "persona:" + callback_token},
            {"text": "🗑️", "callback_data": "persona:delete:" + callback_token},
        ])
    navigation = panel_navigation("persona", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "🚫 Persona off", "callback_data": "persona:off"}])
    if current_persona and current_persona in personas:
        rows.append([{"text": "✏️ Edit current persona", "callback_data": "persona:edit"}])
    rows.append([{"text": "➕ Create persona", "callback_data": "persona:create"}])
    rows.append([{"text": "❌ Cancel", "callback_data": "persona:cancel"}])
    page_label = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"Current Persona: {persona_service.name(current_persona) if current_persona else 'off'}{page_label}\nChoose a persona:"
    send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id)


def send_character_menu(token: str, chat_id: str, current_character: str, message_id: int | None = None, page: int = 0) -> None:
    options = [(path.name, character_display_name(path)) for path in character_card_paths()]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for filename, label in page_options:
        mark = "✅ " if filename == current_character else ""
        callback_token = dynamic_callback_token("character", filename, chat_id)
        card_path = safe_character_path(filename)
        protected = filename == current_character or filename == _config.DEFAULT_CHARACTER_FILE or (card_path is not None and card_path.resolve() == _config.CARD_FILE.resolve())
        action = {"text": "🔒", "callback_data": "character:protected"} if protected else {"text": "🗑️", "callback_data": "characterdelete:" + callback_token}
        rows.append([
            {"text": mark + panel_label(label), "callback_data": "character:" + callback_token},
            action,
        ])
    navigation = panel_navigation("character", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "ℹ️ Character info", "callback_data": "character:info"}, {"text": "🔄 Refresh", "callback_data": "character:menu"}])
    rows.append([{"text": "📤 Upload character card", "callback_data": "character:upload"}])
    rows.append([{"text": "❌ Cancel", "callback_data": "character:cancel"}])
    current_label = current_character
    if safe_character_path(current_character):
        current_label = card_fields_from_file(current_character)["name"]
    page_label = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"Current character: {current_label}{page_label}\nChoose a character card:"
    try:
        send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id)
    except RuntimeError as exc:
        if message_id is not None and "not modified" in str(exc).casefold():
            return
        raise


def send_character_info_menu(token: str, chat_id: str, message_id: int | None = None, page: int = 0) -> None:
    options = [(path.name, character_display_name(path)) for path in character_card_paths()]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [[{"text": label, "callback_data": "characterinfo:" + dynamic_callback_token("character", filename, chat_id)}] for filename, label in page_options]
    navigation = panel_navigation("characterinfo", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "⬅️ Back", "callback_data": "character:menu"}, {"text": "❌ Close", "callback_data": "character:cancel"}])
    text = f"Choose a character for info (page {current_page + 1}/{total_pages}):"
    send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id)


def send_character_delete_menu(token: str, chat_id: str, active_character: str, message_id: int | None = None, page: int = 0) -> None:
    options = [(path.name, character_display_name(path)) for path in character_card_paths() if path.name != active_character]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [[{"text": label, "callback_data": "characterdelete:" + dynamic_callback_token("character", filename, chat_id)}] for filename, label in page_options]
    navigation = panel_navigation("characterdelete", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "⬅️ Back", "callback_data": "character:menu"}, {"text": "❌ Close", "callback_data": "character:cancel"}])
    text = f"Choose a non-active character to delete (page {current_page + 1}/{total_pages}):"
    send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id)


def send_character_delete_confirm(token: str, chat_id: str, filename: str, message_id: int | None = None) -> None:
    token_value = dynamic_callback_token("character", filename, chat_id)
    payload = {"chat_id": chat_id, "text": f"Delete {Path(filename).stem}? The card file will be removed; verified backups are kept.", "reply_markup": {"inline_keyboard": [[{"text": "✅ Confirm delete", "callback_data": "characterdeleteconfirm:" + token_value}, {"text": "❌ Cancel", "callback_data": "character:menu"}]]}}
    send_panel_message(token, chat_id, payload["text"], payload["reply_markup"], message_id)


def send_session_menu(token: str, chat_id: str, sessions: list[dict[str, str]], current_id: str, message_id: int | None = None, page: int = 0) -> None:
    options = [(session["session_id"], session["title"] or session["session_id"]) for session in sessions]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for session_id, label in page_options:
        mark = "✅ " if session_id == current_id else ""
        delete_token = dynamic_callback_token("session", session_id, chat_id)
        action = {"text": "🔒", "callback_data": "session:protected"} if session_id == current_id else {"text": "🗑️", "callback_data": "sessiondelete:" + delete_token}
        rows.append([
            {"text": mark + panel_label(label), "callback_data": "session:" + session_id},
            action,
        ])
    navigation = panel_navigation("session", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "➕ New session", "callback_data": "session:new"}])
    rows.append([{"text": "❌ Cancel", "callback_data": "session:cancel"}])
    page_label = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"Current session: {current_id}{page_label}\nChoose a session, create a new one, or delete an inactive session with its session-scoped Hindsight documents."
    send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id)


# Explicit late imports replace transitional dependency injection.
from bridge.persona_sync import (
    _native_settings,
    load_personas,
    resolve_persona_service,
)
from bridge.telegram import telegram_request

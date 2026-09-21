from __future__ import annotations

from bridge.ordinary_dependencies import bind_module_dependencies as _bind_module_dependencies

import json
import logging
from pathlib import Path

HELP_DETAILS_FILE = Path(__file__).with_name("help_details.json")


def _load_command_details() -> dict:
    """Load editable Help descriptions from the adjacent JSON data file."""
    try:
        payload = json.loads(HELP_DETAILS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Could not load Help details JSON: %s", exc)
        return {}
    if not isinstance(payload, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in payload.items()):
        logging.warning("Help details JSON must contain a string-to-string object")
        return {}
    return payload


COMMAND_DETAILS = _load_command_details()

HELP_CATEGORY_TITLES = {
    "basic": "Start & Sessions",
    "characters": "Characters & Prompts",
    "generation": "Replies & Settings",
    "memory_rag": "Memory & Files",
    "voice_group": "Voice & Groups",
}

HELP_CATEGORY_INTROS = {
    "basic": "Start a conversation, check what's active, or manage your sessions.",
    "characters": "Pick the character, persona, lore, system prompt, or reply language for this session.",
    "generation": "Control how replies are generated, edited, continued, streamed, and retried.",
    "memory_rag": "Manage memory, summaries, and Data Bank search for this chat.",
    "voice_group": "Use voice features and manage multi-character group chats in Forum Topics.",
}


def help_markup(category: str | None = None, command_index: int | None = None, page: int = 0) -> dict:
    """Build category, paginated command, detail, Back, and Close keyboards."""
    if category is not None and category in HELP_CATEGORIES and command_index is not None:
        return {"inline_keyboard": [[{"text": "⬅️ Back", "callback_data": f"help:{category}"}, {"text": "❌ Close", "callback_data": "help:close"}]]}
    if category is not None and category in HELP_CATEGORIES:
        options, current_page, total_pages = panel_page(HELP_CATEGORIES[category], page)
        offset = current_page * 8
        rows = [[{"text": command, "callback_data": f"help:cmd:{category}:{offset + index}"}] for index, (command, _summary) in enumerate(options)]
        navigation = []
        if current_page > 0:
            navigation.append({"text": "⬅️ Previous", "callback_data": f"help:cmdpage:{category}:{current_page - 1}"})
        if current_page < total_pages - 1:
            navigation.append({"text": "Next ➡️", "callback_data": f"help:cmdpage:{category}:{current_page + 1}"})
        if navigation:
            rows.append(navigation)
        rows.append([{"text": "⬅️ Categories", "callback_data": "help:menu"}, {"text": "❌ Close", "callback_data": "help:close"}])
        return {"inline_keyboard": rows}
    return {"inline_keyboard": [
        [{"text": "💬 Start & Sessions", "callback_data": "help:basic"}, {"text": "🎭 Characters & Prompts", "callback_data": "help:characters"}],
        [{"text": "⚙️ Replies & Settings", "callback_data": "help:generation"}, {"text": "🧠 Memory & Files", "callback_data": "help:memory_rag"}],
        [{"text": "🎙️ Voice & Groups", "callback_data": "help:voice_group"}],
        [{"text": "❌ Close", "callback_data": "help:close"}],
    ]}


def help_text(category: str | None = None, command_index: int | None = None, page: int = 0) -> str:
    """Render a help category page or one command's detailed information."""
    if category is not None and category in HELP_CATEGORIES:
        if command_index is not None and 0 <= command_index < len(HELP_CATEGORIES[category]):
            command, summary = HELP_CATEGORIES[category][command_index]
            return (f"Help — {command}\n\nWhat it does:\n{command_detail(command, summary)}\n\n"
                    f"Use Back to return to {HELP_CATEGORY_TITLES.get(category, category)}.")
        _options, current_page, total_pages = panel_page(HELP_CATEGORIES[category], page)
        suffix = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
        title = HELP_CATEGORY_TITLES.get(category, category.replace("_", " / ").title())
        intro = HELP_CATEGORY_INTROS.get(category, "Choose a command to see what it does.")
        return f"Help — {title}{suffix}\n\n{intro}\n\nTap a command below to see what it does and what it changes."
    return ("Help — SillyTavern Telegram Bridge\n\n"
            "Choose a topic below. Each topic lists related commands.\n"
            "Tap a command to see what it does, then use Back or Close.\n\n"
            "For a quick answer, use /help <command>.")


def command_detail(command: str, summary: str) -> str:
    """Return the detailed help text for one registered command entry."""
    return COMMAND_DETAILS.get(command, summary)


def normalize_help_command(text: str) -> str | None:
    """Return the requested help topic, or None for non-Help messages."""
    parts = str(text or "").strip().split(None, 1)
    if not parts:
        return None
    command = parts[0].casefold()
    if command.startswith("/help@"):
        command = "/help"
    if command != "/help":
        return None
    return parts[1].strip() if len(parts) > 1 else ""


def _help_command_target(requested: str) -> tuple[str, int] | None:
    normalized = "/" + str(requested or "").strip().lstrip("/").casefold()
    if normalized == "/":
        return None
    base_match = None
    for category, entries in HELP_CATEGORIES.items():
        for index, (command, _summary) in enumerate(entries):
            command_normalized = command.casefold()
            if command_normalized == normalized:
                return category, index
            if base_match is None and command_normalized.split()[0].split("|", 1)[0] == normalized:
                base_match = (category, index)
    return base_match


def send_help_command(token: str, chat_id: str, text: str, message_id: int | None = None) -> bool:
    """Render a Help panel for an exact Help command without text fallback."""
    requested = normalize_help_command(text)
    if requested is None:
        return False
    target = _help_command_target(requested) if requested else None
    if target is None:
        send_help_menu(token, chat_id, message_id=message_id)
    else:
        send_help_menu(token, chat_id, target[0], message_id, target[1])
    return True


def is_help_callback(data: str) -> bool:
    return str(data or "").startswith("help:")


def handle_help_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
    """Handle paginated help categories, command details, Back, and Close."""
    if data.startswith("help:cmdpage:"):
        parts = data.split(":")
        if len(parts) != 4 or parts[2] not in HELP_CATEGORIES:
            answer_callback(token, str(callback.get("id", "")), "Help page expired")
            return True
        try:
            page = int(parts[3])
            _options, current_page, total_pages = panel_page(HELP_CATEGORIES[parts[2]], page)
        except (TypeError, ValueError):
            answer_callback(token, str(callback.get("id", "")), "Help page expired")
            return True
        if current_page != page or page < 0 or page >= total_pages:
            answer_callback(token, str(callback.get("id", "")), "Help page expired")
            return True
        answer_callback(token, str(callback.get("id", "")), "Help")
        send_help_menu(token, chat_id, parts[2], message.get("message_id"), page=page)
        return True
    if data.startswith("help:cmd:"):
        parts = data.split(":")
        if len(parts) != 4 or parts[2] not in HELP_CATEGORIES:
            answer_callback(token, str(callback.get("id", "")), "Help choice expired")
            return True
        try:
            command_index = int(parts[3])
        except ValueError:
            answer_callback(token, str(callback.get("id", "")), "Help choice expired")
            return True
        if not 0 <= command_index < len(HELP_CATEGORIES[parts[2]]):
            answer_callback(token, str(callback.get("id", "")), "Help choice expired")
            return True
        answer_callback(token, str(callback.get("id", "")), "Help")
        send_help_menu(token, chat_id, parts[2], message.get("message_id"), command_index)
        return True
    if data.startswith("help:"):
        category = data.split(":", 1)[1]
        if category == "close":
            close_panel_message(token, chat_id, message)
            try:
                answer_callback(token, str(callback.get("id", "")), "Closed")
            except Exception:
                logging.debug("Could not acknowledge closed help panel", exc_info=True)
        elif category == "menu":
            answer_callback(token, str(callback.get("id", "")), "Help")
            send_help_menu(token, chat_id, None, message.get("message_id"))
        elif category in HELP_CATEGORIES:
            answer_callback(token, str(callback.get("id", "")), "Help")
            send_help_menu(token, chat_id, category, message.get("message_id"))
        else:
            answer_callback(token, str(callback.get("id", "")), "Help")
            send_help_menu(token, chat_id, None, message.get("message_id"))
        return True
    return False


_bind_module_dependencies(__name__, globals())

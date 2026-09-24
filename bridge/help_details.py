"""Load editable Help descriptions from the adjacent JSON data file."""
from __future__ import annotations

import json
import logging
from pathlib import Path

HELP_DETAILS_FILE = Path(__file__).with_name("help_details.json")


HELP_CATEGORIES = {
    "basic": [
        ("/start", "Show the character greeting when Persona, World Info, and System Prompt are all enabled; otherwise show what's off and how to fix it."),
        ("/help", "Open this guide. Use /help <command> to jump straight to one command."),
        ("/cancel", "Cancel the current scoped input step without applying a change."),
        ("/status", "Show a formatted read-only session status message in Telegram."),
        ("/new", "Name and create a fresh isolated session, then switch to it."),
        ("/reset", "Open a confirmation panel to clear only the active session and its Hindsight memory. Other sessions stay untouched."),
        ("/session", "Switch between sessions, create new ones, or delete inactive ones — deletion removes session data and its Hindsight documents."),
        ("/sync", "Open session-scoped Live API Sync controls; synchronization uses the local SillyTavern API only, not chat files or JSONL transfer."),
        ("/update", "Check the latest GitHub release. If you're already current, nothing happens. Otherwise a confirmation panel lets you update and restart."),
    ],
    "characters": [
        ("/providers", "Choose Story or Utility provider/model. Supported OpenAI-compatible, Anthropic, and OpenCode transports can generate; catalog-only entries stay view-only."),
        ("/providers refresh", "Open the provider panel and refresh discoverable model catalogs."),
        ("/providers health", "Open the provider panel and run health checks. Providers without /models use a bounded streaming chat probe."),
        ("/character", "Open the character panel — pick a card, view info, delete safely, or get upload guidance."),
        ("/persona", "Choose, create, edit, or disable a Persona. Delete only targets inactive, unreferenced ones."),
        ("/world", "Open World Info selection — activate or disable one or more lorebooks."),
        ("/note", "Open the Author's Note panel. Off clears it; User input waits for your next message."),
        ("/systemprompt", "Open the native JSON/TXT System Prompt picker. The prompt body stays private."),
        ("/language", "Open the reply-language panel for this session."),
        ("/language <language>", "Set the session reply language directly, or use auto to follow the user's language."),
        ("/expression", "Open native expression controls for the active character."),
        ("/imagine", "Open scoped image-prompt input when an image provider is configured."),
        ("/imagine <prompt>", "Generate an image immediately from a 1–4,000 character prompt."),
    ],
    "generation": [
        ("/settings", "Open this session's generation panel — reasoning level, temperature, tokens, and sampling values."),
        ("/stream", "Open the streaming on/off panel. Typed /stream on or /stream off still opens the panel; use its buttons to apply the change."),
        ("/preset", "Open the preset panel — apply, save, or delete generation setting presets."),
        ("/macro", "Open scoped input for a SillyTavern macro preview."),
        ("/macro <text>", "Preview supported SillyTavern macros immediately against the supplied text."),
        ("/stscript", "Open the safe STscript panel for allowlisted bridge actions only."),
        ("/regen", "Generate a new response variant for the latest user turn."),
        ("/swipe", "Browse stored response variants and keep the one you like."),
        ("/branch", "Open the response branch selector for the active session."),
        ("/continue", "Continue the latest assistant response from where it stopped."),
        ("/edit", "Open scoped input for replacement text for the latest user turn."),
        ("/edit <text>", "Replace the latest user turn immediately and regenerate from the new text."),
        ("/retry", "Retry the latest failed character response without creating a duplicate turn."),
        ("/prompt", "Open the read-only prompt inspector with budget, memory/RAG, and group-context sections."),
        ("/prompt text", "Send the plain-text prompt diagnostic output without opening the inspector panel."),
    ],
    "memory_rag": [
        ("/memory", "Open Hindsight memory controls. Recall is always limited to the active session."),
        ("/memory search <query>", "Search Hindsight memory for the active session and show up to five matching remembered facts."),
        ("/memory curated", "Open the curated-memory panel to view durable distilled facts."),
        ("/memory curated refresh", "Refresh curated durable memory immediately with the configured Utility model."),
        ("/remember", "Open scoped input for one explicit long-term fact."),
        ("/remember <fact>", "Store one explicit long-term fact immediately in active-session Hindsight memory."),
        ("/summarize", "Open a confirmation panel before regenerating the active-session summary."),
        ("/databank", "Open Data Bank RAG controls for status, listing, search, removal, and reindexing."),
        ("/databank search <query>", "Search active Data Bank document versions for matching chunks."),
        ("/databank versions <filename>", "List stored versions of one Data Bank filename and show which version is active."),
        ("/databank activate <filename> <version>", "Atomically activate a stored Data Bank version for retrieval."),
        ("/databank reindex [filename]", "Rebuild embeddings for all active documents, or only the named document."),
        ("/databank remove <filename> confirm", "Delete every stored version of one Data Bank filename after explicit confirmation."),
    ],
    "voice_group": [
        ("/voice", "Open automatic quote-driven TTS controls. Typed on/off forms also open this panel rather than changing state directly."),
        ("/voice_input", "Open transcription, STT model, and language controls."),
        ("/voice_input language", "Open the STT language panel — Auto, a fixed code, or User input."),
        ("/group", "Open Forum Topic group controls, including Director mode."),
        ("/group status", "Show current group state, mode, members, and speaker inside a Forum Topic."),
        ("/group add <character>", "Add a character to the current Forum Topic group, up to six members."),
        ("/group remove <character>", "Remove a character from the current Forum Topic group."),
        ("/group speak <character>", "Force one existing group member to be the next speaker."),
        ("/group mode <mode>", "Set round_robin, contextual, director, manual, or autonomous turn mode."),
        ("/group on|off", "Enable or disable the configured Forum Topic group directly."),
        ("/group next", "Advance the group speaker index to the next configured member."),
        ("/group goal", "View the hidden, session-local Director objective for this Forum Topic group."),
        ("/group goal <objective>", "Set or replace the hidden Director objective without adding it to the transcript."),
        ("/group goal clear", "Clear the hidden Director objective for the active Forum Topic group."),
        ("/scene", "Show the active session's structured scene-state panel."),
        ("/scene refresh", "Rebuild structured scene state immediately with the configured Utility model."),
        ("/scene clear", "Clear the active session's stored structured scene state without changing the transcript."),
    ],
}


def _load_command_details() -> dict:
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


HELP_ALIASES = {
    "/stream on": "/stream",
    "/stream off": "/stream",
    "/preset list": "/preset",
    "/preset use": "/preset",
    "/preset delete": "/preset",
    "/settings reasoning": "/settings",
    "/language list": "/language",
    "/language status": "/language",
    "/memory on": "/memory",
    "/memory off": "/memory",
    "/memory status": "/memory",
    "/memory scope": "/memory",
    "/memory search": "/memory search <query>",
    "/memory curated status": "/memory curated",
    "/databank on": "/databank",
    "/databank off": "/databank",
    "/databank status": "/databank",
    "/databank list": "/databank",
    "/databank search": "/databank search <query>",
    "/databank versions": "/databank versions <filename>",
    "/databank activate": "/databank activate <filename> <version>",
    "/databank reindex": "/databank reindex [filename]",
    "/databank remove": "/databank remove <filename> confirm",
    "/voice status": "/voice",
    "/voice on": "/voice",
    "/voice off": "/voice",
    "/voice tts": "/voice",
    "/voice_input status": "/voice_input",
    "/voice_input on": "/voice_input",
    "/voice_input off": "/voice_input",
    "/group list": "/group status",
    "/group goal status": "/group goal",
    "/group goal off": "/group goal clear",
    "/group goal none": "/group goal clear",
    "/scene status": "/scene",
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


def _help_pattern_matches(pattern: str, requested: str) -> bool:
    pattern_tokens = pattern.casefold().split()
    requested_tokens = requested.casefold().split()
    if not pattern_tokens or not requested_tokens:
        return False

    request_index = 0
    for pattern_index, token in enumerate(pattern_tokens):
        is_placeholder = token.startswith("<") and token.endswith(">")
        is_optional = token.startswith("[") and token.endswith("]")
        if is_placeholder:
            # Placeholder text is descriptive rather than literal. Allow Help
            # lookup at the prefix itself ("group add") or with arguments.
            return request_index >= pattern_index
        if is_optional:
            return request_index >= pattern_index
        if request_index >= len(requested_tokens):
            return False
        alternatives = token.split("|")
        if requested_tokens[request_index] not in alternatives:
            return False
        request_index += 1

    return request_index == len(requested_tokens)


def _help_command_target(requested: str) -> tuple[str, int] | None:
    normalized = "/" + str(requested or "").strip().lstrip("/").casefold()
    if normalized == "/":
        return None
    normalized = HELP_ALIASES.get(normalized, normalized)

    entries = [
        (category, index, command.casefold())
        for category, category_entries in HELP_CATEGORIES.items()
        for index, (command, _summary) in enumerate(category_entries)
    ]
    for category, index, command in entries:
        if command == normalized:
            return category, index
    for category, index, command in entries:
        if _help_pattern_matches(command, normalized):
            return category, index

    base = normalized.split()[0]
    for category, index, command in entries:
        if command.split()[0].split("|", 1)[0] == base:
            return category, index
    return None


def send_help_menu(
    token: str,
    chat_id: str,
    category: str | None = None,
    message_id: int | None = None,
    command_index: int | None = None,
    page: int = 0,
    *,
    delivery_port: DeliveryPort,
    request_context,
) -> None:
    method = "editMessageText" if message_id else "sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": help_text(category, command_index, page),
        "reply_markup": help_markup(category, command_index, page),
    }
    if message_id:
        payload["message_id"] = message_id
    delivery_port.send_panel_request(
        token,
        method,
        payload,
        request_context=request_context,
    )


def send_help_command(
    token: str,
    chat_id: str,
    text: str,
    message_id: int | None = None,
    *,
    delivery_port: DeliveryPort,
    request_context,
) -> bool:
    """Render a Help panel for an exact Help command without text fallback."""
    requested = normalize_help_command(text)
    if requested is None:
        return False
    target = _help_command_target(requested) if requested else None
    if target is None:
        send_help_menu(
            token,
            chat_id,
            message_id=message_id,
            delivery_port=delivery_port,
            request_context=request_context,
        )
    else:
        send_help_menu(
            token,
            chat_id,
            target[0],
            message_id,
            target[1],
            delivery_port=delivery_port,
            request_context=request_context,
        )
    return True


def is_help_callback(data: str) -> bool:
    return str(data or "").startswith("help:")


def handle_help_callback(
    db,
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    message,
    session,
    session_id,
    operation_id,
    *,
    delivery_port: DeliveryPort,
    request_context,
):
    """Handle paginated help categories, command details, Back, and Close."""
    message_id = message.get("message_id")
    if data.startswith("help:cmdpage:"):
        parts = data.split(":")
        if len(parts) != 4 or parts[2] not in HELP_CATEGORIES:
            answer_callback(token, str(callback.get("id", "")), "Help page expired")
            return True
        try:
            page = int(parts[3])
            _options, current_page, total_pages = panel_page(
                HELP_CATEGORIES[parts[2]],
                page,
            )
        except (TypeError, ValueError):
            answer_callback(token, str(callback.get("id", "")), "Help page expired")
            return True
        if current_page != page or page < 0 or page >= total_pages:
            answer_callback(token, str(callback.get("id", "")), "Help page expired")
            return True
        answer_callback(token, str(callback.get("id", "")), "Help")
        send_help_menu(
            token,
            chat_id,
            parts[2],
            message_id,
            page=page,
            delivery_port=delivery_port,
            request_context=request_context,
        )
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
        send_help_menu(
            token,
            chat_id,
            parts[2],
            message_id,
            command_index,
            delivery_port=delivery_port,
            request_context=request_context,
        )
        return True
    if data.startswith("help:"):
        category = data.split(":", 1)[1]
        if category == "close":
            close_panel_message(db, token, chat_id, message)
            try:
                answer_callback(token, str(callback.get("id", "")), "Closed")
            except Exception:
                logging.debug(
                    "Could not acknowledge closed help panel",
                    exc_info=True,
                )
        elif category == "menu":
            answer_callback(token, str(callback.get("id", "")), "Help")
            send_help_menu(
                token,
                chat_id,
                None,
                message_id,
                delivery_port=delivery_port,
                request_context=request_context,
            )
        elif category in HELP_CATEGORIES:
            answer_callback(token, str(callback.get("id", "")), "Help")
            send_help_menu(
                token,
                chat_id,
                category,
                message_id,
                delivery_port=delivery_port,
                request_context=request_context,
            )
        else:
            answer_callback(token, str(callback.get("id", "")), "Help")
            send_help_menu(
                token,
                chat_id,
                None,
                message_id,
                delivery_port=delivery_port,
                request_context=request_context,
            )
        return True
    return False


# Explicit late imports replace transitional dependency injection.
from bridge.callbacks import close_panel_message
from bridge.delivery_port import DeliveryPort
from bridge.panel_utils import panel_page

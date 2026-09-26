"""Character-card opening greeting helpers."""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import time

from bridge.card_content import replace_macros
from bridge.conversation_lifecycle import (
    ALREADY_STARTED,
    conversation_state,
    is_group_conversation,
    lifecycle_key,
    mark_started,
)
from bridge.metadata import get_meta, set_meta
from bridge.response_delivery import persist_assistant_delivery_ids
from bridge.sqlite_store import write_transaction
from bridge.telegram_output import telegram_safe_output
from bridge.limits import CARD_FIELD_MAX_CHARS
from bridge.operations import begin_operation, operation_was_applied, record_operation
from bridge.panel_utils import PANEL_PAGE_SIZE, panel_page
from bridge.settings import AppSettings
from bridge.telegram import send_panel_request, send_text

_GREETING_PREVIEW_MAX_CHARS = 3200


def greeting_options(fields: dict) -> list[str]:
    """Return the primary greeting followed by bounded alternate greetings."""
    options = [str(fields.get("first_mes") or "")]
    raw = fields.get("alternate_greetings") or "[]"
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        values = []
    if isinstance(values, list):
        options.extend(str(value or "") for value in values[:20])
    return [value[:CARD_FIELD_MAX_CHARS].strip() for value in options if value[:CARD_FIELD_MAX_CHARS].strip()]


def greeting_choice_label(index: int) -> str:
    return "Default" if int(index) == 0 else f"Alternate {int(index)}"


def render_greeting(fields: dict, user_name: str, index: int, *, app_settings: AppSettings) -> str:
    options = greeting_options(fields)
    selected_index = int(index)
    if selected_index < 0 or selected_index >= len(options):
        return ""
    return replace_macros(options[selected_index], fields, user_name, app_settings=app_settings).strip()


def send_greeting_menu(
    token: str,
    chat_id: str,
    fields: dict,
    user_name: str,
    message_id: int | None = None,
    selected_index: int = 0,
    page: int | None = None,
    *,
    request_context,
) -> bool:
    """Show the opening-message chooser and preview the selected greeting."""
    state = conversation_state(request_context.db, chat_id, request_context.session_id)
    if state.started and not is_group_conversation(request_context.db, chat_id, request_context.session_id):
        send_text(token, chat_id, ALREADY_STARTED)
        return False
    epoch = state.epoch
    options = greeting_options(fields)
    if not options:
        send_text(token, chat_id, "This character has no opening greeting.")
        return False

    selected_index = int(selected_index)
    if selected_index < 0 or selected_index >= len(options):
        selected_index = 0
    target_page = selected_index // PANEL_PAGE_SIZE if page is None else max(0, int(page))
    indexed_options = list(enumerate(options))
    page_options, current_page, total_pages = panel_page(indexed_options, target_page)

    rows = []
    for index, _greeting in page_options:
        label = greeting_choice_label(index)
        mark = "✅ " if index == selected_index else ""
        rows.append(
            [
                {
                    "text": mark + label,
                    "callback_data": f"greeting:preview:{index}:{epoch}",
                }
            ]
        )

    navigation = []
    if current_page > 0:
        navigation.append(
            {
                "text": "⬅️ Previous",
                "callback_data": f"greeting:page:{current_page - 1}:{selected_index}:{epoch}",
            }
        )
    if current_page < total_pages - 1:
        navigation.append(
            {
                "text": "Next ➡️",
                "callback_data": f"greeting:page:{current_page + 1}:{selected_index}:{epoch}",
            }
        )
    if navigation:
        rows.append(navigation)

    rows.append(
        [
            {
                "text": "▶️ Start with this greeting",
                "callback_data": f"greeting:use:{selected_index}:{epoch}",
            }
        ]
    )
    rows.append(
        [
            {
                "text": "❌ Cancel",
                "callback_data": "greeting:cancel",
            }
        ]
    )

    preview = render_greeting(fields, user_name, selected_index, app_settings=request_context.app_settings)
    if len(preview) > _GREETING_PREVIEW_MAX_CHARS:
        preview = preview[: _GREETING_PREVIEW_MAX_CHARS - 1].rstrip() + "…"
    page_label = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    text = (
        f"Opening message — {fields.get('name') or 'Character'}{page_label}\n"
        f"Selected: {greeting_choice_label(selected_index)}\n\n"
        f"Preview:\n{preview or '[empty after macro rendering]'}"
    )
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": rows},
    }
    method = "editMessageText" if message_id is not None else "sendMessage"
    if message_id is not None:
        payload["message_id"] = message_id
    try:
        send_panel_request(token, method, payload, request_context=request_context)
    except RuntimeError as exc:
        if "not modified" in str(exc).casefold():
            logging.info("Greeting panel already shows the requested state")
            return True
        raise
    return True


def send_character_greeting(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    fields: dict,
    session_id: str,
    user_name: str,
    index: int | None = 0,
    operation_id=None,
    operation_kind: str = "greeting",
    *,
    app_settings: AppSettings,
    expected_epoch: int | None = None,
) -> bool:
    """Commit opening plus started state together, then deliver the committed text."""
    if db.in_transaction:
        raise ValueError("Greeting delivery requires no enclosing write transaction")
    group = is_group_conversation(db, chat_id, session_id)
    state = conversation_state(db, chat_id, session_id)
    if expected_epoch is not None and state.epoch != expected_epoch:
        return False
    opening_key = lifecycle_key("opening", chat_id, session_id)
    with write_transaction(db):
        state = conversation_state(db, chat_id, session_id)
        if expected_epoch is not None and state.epoch != expected_epoch:
            return False
        if operation_id is not None and operation_was_applied(db, operation_id):
            return False
        rowid = None
        if state.started and not group:
            opening = json.loads(get_meta(db, opening_key, "") or "{}")
            if operation_id is None or str(opening.get("operation_id")) != str(operation_id):
                return False
            row = db.execute(
                "SELECT content,telegram_message_ids FROM messages WHERE rowid=? AND chat_id=? AND session_id=? AND role='assistant'",
                (opening.get("rowid"), chat_id, session_id),
            ).fetchone()
            if row is None:
                return False
            if json.loads(row[1] or "[]"):
                record_operation(db, operation_id, operation_kind)
                return False
            rowid, greeting = int(opening["rowid"]), str(row[0])
        else:
            options = greeting_options(fields)
            if not options:
                return False
            selected_index = random.randrange(len(options)) if index is None else int(index)  # noqa: S311 -- greeting selection
            greeting = telegram_safe_output(
                render_greeting(fields, user_name, selected_index, app_settings=app_settings)
            )
            if not greeting:
                return False
            cursor = db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (chat_id, session_id, "assistant", greeting, time.time()),
            )
            rowid = int(cursor.lastrowid)
            if not group:
                if not mark_started(db, chat_id, session_id, state.epoch):
                    raise ValueError("Opening state changed")
                set_meta(
                    db, opening_key, json.dumps({"rowid": rowid, "operation_id": operation_id, "epoch": state.epoch})
                )
    message_ids = send_text(token, chat_id, greeting)
    persist_assistant_delivery_ids(db, rowid, message_ids)
    record_operation(db, operation_id, operation_kind)
    return True

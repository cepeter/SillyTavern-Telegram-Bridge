"""Telegram rendering for durable choices; SQLite remains the authority."""

from __future__ import annotations

import html
import logging
import sqlite3

from bridge.conversation_lifecycle import conversation_state
from bridge.light_novel_repository import ChoiceSet, bind_choice_panel, latest_choice_set, load_choice_set
from bridge.light_novel_service import current_choice_story
from bridge.metadata import get_meta
from bridge.request_types import RequestContext
from bridge.settings import AppSettings
from bridge.sqlite_store import write_transaction
from bridge.telegram import send_panel_request, telegram_request


def hide_choice_panels(token: str, chat_id: str, message_ids: list[int]) -> None:
    for message_id in message_ids:
        try:
            telegram_request(
                token,
                "editMessageReplyMarkup",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": {"inline_keyboard": []},
                },
            )
        except Exception:
            logging.info("Could not hide superseded choices; durable callbacks remain invalid")


def render_choices(db: sqlite3.Connection, token: str, record: ChoiceSet, *, app_settings: AppSettings) -> bool:
    current = load_choice_set(db, record.nonce)
    if current is None or current_choice_story(db, current) is None:
        return False
    if get_meta(db, f"active_session:{current.chat_id}", "default") != current.session_id:
        return False
    if current.generation_status == "ready":
        choice_lines = [
            f"<b>{index + 1}.</b> {html.escape(choice, quote=False)}" for index, choice in enumerate(current.choices)
        ]
        text = "What will you do?\n\n" + "\n\n".join(choice_lines) + "\n\nYou may also type your own reply."
        rows = [
            [
                {"text": str(index + 1), "callback_data": f"lnchoice:{current.nonce}:{index}"}
                for index in range(len(current.choices))
            ],
            [{"text": "⏭ Next Scene", "callback_data": f"lnnext:{current.nonce}"}],
        ]
    else:
        text = "Choices are not available yet. Your story is saved. Retry choices or type your own reply."
        rows = [[{"text": "Retry Choices", "callback_data": f"lnretry:{current.nonce}"}]]
    context = RequestContext(db, current.session_id, current.actor_id, app_settings=app_settings)
    payload: dict = {"chat_id": current.chat_id, "text": text, "reply_markup": {"inline_keyboard": rows}}
    if current.generation_status == "ready":
        payload["parse_mode"] = "HTML"
    message_id = current.panel_message_id
    method = "sendMessage"
    if message_id is not None:
        method = "editMessageText"
        payload["message_id"] = message_id
    try:
        result = send_panel_request(token, method, payload, request_context=context)
    except RuntimeError as exc:
        detail = str(exc).casefold()
        if message_id is not None and "not modified" in detail:
            return True
        if message_id is None or "message to edit not found" not in detail:
            raise
        payload.pop("message_id", None)
        result = send_panel_request(token, "sendMessage", payload, request_context=context)
    sent_id = result.get("message_id") if isinstance(result, dict) else None
    sent_id = int(sent_id or message_id or 0)
    if not sent_id:
        return False
    with write_transaction(db):
        bound = bind_choice_panel(db, current.nonce, sent_id)
    if not bound:
        hide_choice_panels(token, current.chat_id, [sent_id])
    return bound


def send_light_novel_menu(token: str, chat_id: str, session: dict, *, request_context: RequestContext) -> None:
    db = request_context.db
    state = conversation_state(db, chat_id, session["session_id"])
    mode = f"Light Novel — {state.strategy.upper()}" if state.mode == "lightnovel" else "Normal"
    text = f"Conversation mode: {mode}\nSession: {session['title']}"
    rows = []
    if state.started:
        text += "\n\nThe session has started. Use /reset or /new before changing its mode."
        if state.mode == "lightnovel":
            current = latest_choice_set(db, chat_id, session["session_id"])
            if current and current_choice_story(db, current) is not None:
                render_choices(db, token, current, app_settings=request_context.app_settings)
            else:
                text += "\nContinue with a typed reply to receive new choices."
    else:
        text += (
            "\n\nA: Story + choices in one request.\nB: Story then Utility choices.\nC: Story then Story-model choices."
            "\nChoice count is random, 2–4 each turn. Configure Character, Persona, World an"
            "d System Prompt with /character, then use /start."
        )
        for strategy, label in [
            ("a", "A — Story Inline"),
            ("b", "B — Utility Model"),
            ("c", "C — Story Second Pass"),
            ("normal", "Normal"),
        ]:
            rows.append([{"text": label, "callback_data": f"novelmode:{state.epoch}:{strategy}"}])
    rows.append([{"text": "Close", "callback_data": "enum:close"}])
    send_panel_request(
        token,
        "sendMessage",
        {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": rows}},
        request_context=request_context,
    )

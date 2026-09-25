"""Canonical macro commands owner."""

from __future__ import annotations

import sqlite3

from bridge.card_content import replace_macros
from bridge.persona_sync import persona_name
from bridge.reset_panel import reset_confirmation_request
from bridge.telegram import send_panel_request, send_text


def send_stscript_menu(token: str, chat_id: str, message_id: int | None = None, *, request_context) -> None:
    """Show the allowlisted STscript actions without accepting arbitrary scripts."""
    payload = {
        "chat_id": chat_id,
        "text": "Safe STscript actions:\n\nReset clears only the active session after confirmation.",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "♻️ Reset", "callback_data": "enum:stscript:reset"}],
                [{"text": "❌ Close", "callback_data": "enum:stscript:cancel"}],
            ]
        },
    }
    method = "editMessageText" if message_id else "sendMessage"
    if message_id:
        payload["message_id"] = message_id
    send_panel_request(token, method, payload, request_context=request_context)


def handle_macro_command(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    fields: dict,
    command_text: str,
    *,
    request_context,
) -> None:
    parts = command_text.split(None, 1)
    if parts[0].casefold() == "/macro":
        raw = parts[1] if len(parts) > 1 else ""
        send_text(
            token,
            chat_id,
            replace_macros(
                raw,
                fields,
                persona_name(session["persona_id"], app_settings=request_context.app_settings)
                if session["persona_id"]
                else "user",
                app_settings=request_context.app_settings,
            ),
        )
        return
    script = parts[1].strip() if len(parts) > 1 else ""
    action, _, _argument = script.partition(" ")
    if action.casefold() == "reset":
        method, payload = reset_confirmation_request(chat_id)
        send_panel_request(
            token,
            method,
            payload,
            request_context=request_context,
        )
    else:
        send_text(token, chat_id, "Use /stscript to open the safe Reset action panel.")

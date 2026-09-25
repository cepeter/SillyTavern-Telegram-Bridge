"""Canonical note panels owner."""

from __future__ import annotations

from bridge.telegram import send_panel_request


def send_note_menu(
    token: str, chat_id: str, current_note: str, message_id: int | None = None, *, request_context
) -> None:
    state = "on" if str(current_note or "").strip() else "off"
    text = (
        f"Author's Note — {state}\nCurrent length: {len(str(current_note or '').strip())} characters\nChoose an action:"
    )
    method = "editMessageText" if message_id else "sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "🚫 Off", "callback_data": "note:off"},
                    {"text": "✏️ User input", "callback_data": "note:input"},
                ],
                [{"text": "❌ Close", "callback_data": "note:cancel"}],
            ]
        },
    }
    if message_id:
        payload["message_id"] = message_id
    send_panel_request(token, method, payload, request_context=request_context)

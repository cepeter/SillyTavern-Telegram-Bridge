"""Pure presentation data for the active-session reset confirmation panel."""

from __future__ import annotations

RESET_CONFIRMATION_TEXT = (
    "Reset active session and purge its memory?\n\n"
    "This will:\n"
    "• Reset only the active session conversation.\n"
    "• Delete Hindsight memories for this active session only.\n"
    "• Delete session SQLite data, and session documents.\n\n"
    "This cannot be undone."
)


def reset_confirmation_request(
    chat_id: str,
    message_id: int | None = None,
) -> tuple[str, dict]:
    method = "editMessageText" if message_id else "sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": RESET_CONFIRMATION_TEXT,
        "reply_markup": {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ Confirm active-session reset",
                        "callback_data": "reset:confirm",
                    }
                ],
                [
                    {
                        "text": "❌ Cancel",
                        "callback_data": "reset:cancel",
                    }
                ],
            ]
        },
    }
    if message_id:
        payload["message_id"] = message_id
    return method, payload

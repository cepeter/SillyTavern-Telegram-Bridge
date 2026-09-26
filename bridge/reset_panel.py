"""Pure presentation data for the active-session reset confirmation panel."""

from __future__ import annotations

RESET_CONFIRMATION_TEXT = (
    "Reset active session and purge its memory?\n\n"
    "This will:\n"
    "• Delete this session's stored conversation, response variants, summary, and curated memory.\n"
    "• Purge Hindsight documents for this active session.\n"
    "• Attempt to delete bridge-generated Telegram replies from this session.\n"
    "• Keep your own Telegram messages, chat-scoped RAG, and this session identity.\n"
    "• Use /new when you need a completely new session.\n\n"
    "If Hindsight cleanup fails, no local session data will be deleted.\n\n"
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

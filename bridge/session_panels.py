"""Session deletion views; session mutations belong to the lifecycle service."""

from __future__ import annotations

from bridge.callback_tokens import dynamic_callback_token
from bridge.panel_utils import panel_label, panel_message_request, panel_navigation, panel_page
from bridge.telegram import send_panel_request


def send_session_delete_menu(
    token: str,
    chat_id: str,
    sessions: list[dict[str, str]],
    active_id: str,
    message_id: int | None = None,
    page: int = 0,
    *,
    request_context,
) -> None:
    options = [
        (item["session_id"], item["title"] or item["session_id"])
        for item in sessions
        if item["session_id"] != active_id
    ]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [
        [
            {
                "text": panel_label(label),
                "callback_data": "sessiondelete:"
                + dynamic_callback_token("session", session_id, chat_id, db=request_context.db),
            }
        ]
        for session_id, label in page_options
    ]
    navigation = panel_navigation("sessiondelete", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append(
        [{"text": "⬅️ Back", "callback_data": "session:back"}, {"text": "❌ Close", "callback_data": "session:cancel"}]
    )
    text = (
        "Choose an inactive session to delete (page "
        f"""{current_page + 1}"""
        "/"
        f"""{total_pages}"""
        "). Session-scoped Hindsight documents are deleted; memories from other "
        "sessions remain."
    )
    method, payload = panel_message_request(
        chat_id,
        text,
        {"inline_keyboard": rows},
        message_id,
    )
    send_panel_request(
        token,
        method,
        payload,
        request_context=request_context,
    )


def send_session_delete_confirm(
    token: str, chat_id: str, session_id: str, title: str, message_id: int | None = None, *, request_context
) -> None:
    token_value = dynamic_callback_token("session", session_id, chat_id, db=request_context.db)
    payload = {
        "chat_id": chat_id,
        "text": (
            "Delete session '"
            f"""{panel_label(title)}"""
            "'?\n\nThis removes its SQLite transcript, variants, summary, generation "
            "settings, group state, failed turns, session record, and session-scoped "
            "Hindsight documents. Memories from other sessions remain. The active "
            "session cannot be deleted. Cleanup fails closed if Hindsight is "
            "unavailable. This cannot be undone."
        ),
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "✅ Confirm delete", "callback_data": "sessiondeleteconfirm:" + token_value},
                    {"text": "❌ Cancel", "callback_data": "session:back"},
                ]
            ]
        },
    }
    method, panel_payload = panel_message_request(
        chat_id,
        payload["text"],
        payload["reply_markup"],
        message_id,
    )
    send_panel_request(
        token,
        method,
        panel_payload,
        request_context=request_context,
    )

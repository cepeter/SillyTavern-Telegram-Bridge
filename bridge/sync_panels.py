"""Canonical sync panels owner."""

from __future__ import annotations

import time

from bridge.cards import send_panel_message


def sync_status_text(
    db,
    chat_id,
    session,
    *,
    sync_service,
):
    """Render Live API Sync status for the active session."""
    status = sync_service.status(
        db,
        chat_id,
        session["session_id"],
    )
    if status.last_synced_at:
        timestamp = time.strftime(
            "%Y-%m-%d %H:%M:%S %Z",
            time.localtime(status.last_synced_at),
        )
        last = f"{status.last_direction} at {timestamp}"
    else:
        last = "never"
    enabled = "on" if status.realtime_enabled else "off"
    configured = "configured" if status.api_configured else "not configured"
    return (
        "Live Sync\n\n"
        "Live Sync uses the local SillyTavern API.\n"
        f"Session: {status.session_id}\n"
        f"Messages: {status.message_count}\n"
        f"Sync ID: {status.sync_id}\n"
        f"Last sync: {last}\n\n"
        f"Live API sync: {enabled} ({configured})"
    )


def send_sync_menu(
    token,
    chat_id,
    db,
    session,
    message_id=None,
    *,
    sync_service,
    request_context,
):
    """Send or edit the Live API Sync panel."""
    payload = {
        "chat_id": chat_id,
        "text": sync_status_text(
            db,
            chat_id,
            session,
            sync_service=sync_service,
        ),
        "reply_markup": {
            "inline_keyboard": [
                [
                    {
                        "text": "🚀 Realtime API: toggle",
                        "callback_data": "sync:realtime",
                    }
                ],
                [
                    {
                        "text": "🔁 Sync now",
                        "callback_data": "sync:now",
                    }
                ],
                [
                    {
                        "text": "🔄 Refresh status",
                        "callback_data": "sync:status",
                    }
                ],
                [
                    {
                        "text": "❌ Close",
                        "callback_data": "sync:close",
                    }
                ],
            ]
        },
    }
    send_panel_message(
        token,
        chat_id,
        payload["text"],
        payload["reply_markup"],
        message_id,
        request_context=request_context,
    )

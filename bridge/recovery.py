"""Temporary Live Sync UI compatibility loaded before Phase 6H cleanup."""

def sync_status_text(db: sqlite3.Connection, chat_id: str, session: dict[str, str], *, sync_service=None) -> str:
    """Render Live API Sync status for the active session."""
    sync_service = resolve_sync_service(sync_service)
    status = sync_service.status(db, chat_id, session["session_id"])
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
    return ("Live Sync\n\n"
            "Live Sync uses the local SillyTavern API.\n"
            f"Session: {status.session_id}\n"
            f"Messages: {status.message_count}\n"
            f"Sync ID: {status.sync_id}\n"
            f"Last sync: {last}\n\n"
            f"Live API sync: {enabled} ({configured})")


def send_sync_menu(token: str, chat_id: str, db: sqlite3.Connection, session: dict[str, str], message_id: int | None = None, *, sync_service=None) -> None:
    """Send or edit the Live API Sync panel."""
    sync_service = resolve_sync_service(sync_service)
    payload = {
        "chat_id": chat_id,
        "text": sync_status_text(
            db,
            chat_id,
            session,
            sync_service=sync_service,
        ),
        "reply_markup": {"inline_keyboard": [
            [{"text": "🚀 Realtime API: toggle", "callback_data": "sync:realtime"}],
            [{"text": "🔁 Sync now", "callback_data": "sync:now"}],
            [{"text": "🔄 Refresh status", "callback_data": "sync:status"}],
            [{"text": "❌ Close", "callback_data": "sync:close"}],
        ]},
    }
    send_panel_message(token, chat_id, payload["text"], payload["reply_markup"], message_id)


def handle_sync_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id, *, sync_service=None):
    """Handle Live API Sync callbacks."""
    if not data.startswith("sync:"):
        return False
    sync_service = resolve_sync_service(sync_service)
    action = data.split(":", 1)[1]
    message_id = message.get("message_id")
    if action == "close":
        answer_callback(token, str(callback.get("id", "")), "Closed")
        close_panel_message(token, chat_id, callback)
    elif action in {"menu", "status"}:
        answer_callback(token, str(callback.get("id", "")), "Sync status")
        send_sync_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            sync_service=sync_service,
        )
    elif action == "realtime":
        result = sync_service.toggle_realtime(db, chat_id, session_id)
        answer_callback(token, str(callback.get("id", "")), result[:200])
        send_sync_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            sync_service=sync_service,
        )
    elif action == "now":
        result = sync_service.sync_now(db, chat_id, session_id)
        answer_callback(token, str(callback.get("id", "")), result[:200])
        send_sync_menu(
            token,
            chat_id,
            db,
            session,
            message_id,
            sync_service=sync_service,
        )
    else:
        answer_callback(token, str(callback.get("id", "")), "Unknown sync action")
    return True

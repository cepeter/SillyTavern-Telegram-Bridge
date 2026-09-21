"""Temporary Live Sync UI compatibility loaded before Phase 6H cleanup."""


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

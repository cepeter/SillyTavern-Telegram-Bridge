from __future__ import annotations

from bridge.ordinary_dependencies import bind_module_dependencies as _bind_module_dependencies

def close_panel_message(token: str, chat_id: str, callback: dict) -> None:
    message = callback.get("message") or callback
    message_id = message.get("message_id")
    try:
        telegram_request(token, "deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    except Exception:
        logging.info("Panel delete failed; replacing it with a closed marker", exc_info=True)
        try:
            telegram_request(token, "editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": "Panel closed.", "reply_markup": {"inline_keyboard": []}})
        except Exception:
            logging.warning("Panel close fallback failed", exc_info=True)
    panel_db = None
    owns_connection = False
    try:
        panel_db = db_connection_context()
        owns_connection = panel_db is None
        panel_db = panel_db or db_connect()
        panel_db.execute("DELETE FROM panel_sessions WHERE chat_id=? AND message_id=?", (str(chat_id), str(message_id)))
        panel_db.commit()
    except Exception:
        logging.debug("Could not remove closed panel binding", exc_info=True)
    finally:
        if owns_connection and panel_db is not None:
            panel_db.close()


def discard_panel_binding(db: sqlite3.Connection, chat_id: str, message_id: int | str | None) -> None:
    if message_id is None:
        return
    db.execute("DELETE FROM panel_sessions WHERE chat_id=? AND message_id=?", (str(chat_id), str(message_id)))
    db.commit()


def is_session_scoped_panel_callback(data: str) -> bool:
    return data.startswith(("character", "persona", "session", "world", "systemprompt", "language", "note", "reset", "sync", "status:", "prompt:", "scene:", "goal:", "curated:", "summary:", "swipe:", "expression:", "update:", "models", "provider", "model", "group", "groupchars", "groupmode", "enum:settings", "enum:preset", "enum:rag", "enum:stt"))


def process_callback(db: sqlite3.Connection, token: str, callback: dict, operation_id: int | None = None, *, services=None) -> None:
    sender = str((callback.get("from") or {}).get("id", ""))
    message = callback.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    data = str(callback.get("data") or "")
    if not chat_id:
        return
    answer_callback = globals()["answer_callback"]
    if callback.get("_queued"):
        def answer_callback(*_args, **_kwargs):
            return None
    message_id = message.get("message_id")
    bound_session_id = panel_session_for_message(db, chat_id, message_id) if message_id else None
    bound_owner_id = panel_owner_for_message(db, chat_id, message_id) if message_id else ""
    if message_id and bound_owner_id and sender != bound_owner_id:
        feedback = "This panel belongs to another user"
        if callback.get("_queued"):
            send_text(token, chat_id, feedback)
        else:
            answer_callback(token, str(callback.get("id", "")), feedback)
        return
    if message_id and is_session_scoped_panel_callback(data) and not bound_session_id:
        feedback = "Panel expired; reopen it"
        discard_panel_binding(db, chat_id, message_id)
        if callback.get("_queued"):
            send_text(token, chat_id, feedback)
        else:
            answer_callback(token, str(callback.get("id", "")), feedback)
        close_panel_message(token, chat_id, callback)
        return
    session = load_session(db, chat_id, bound_session_id, DEFAULT_MODEL) if bound_session_id else ensure_session(db, chat_id, DEFAULT_MODEL)
    session_id = session["session_id"]
    set_panel_session_context(session_id)
    persona_service = (
        getattr(services, "persona", None)
        if services is not None else None
    )
    sync_service = (
        getattr(services, "sync", None)
        if services is not None else None
    )

    if handle_primary_panel_callback(
        db,
        token,
        callback,
        answer_callback,
        data,
        chat_id,
        message,
        session,
        session_id,
        operation_id,
        sync_service=sync_service,
    ):
        return
    if handle_entity_panel_callback(
        db,
        token,
        callback,
        answer_callback,
        data,
        chat_id,
        message,
        session,
        session_id,
        operation_id,
        persona_service=persona_service,
    ):
        return
    if data.startswith("enum:"):
        handle_enum_callback(db, token, chat_id, session, data, message)
        return
    if data.startswith("group:") or data.startswith("groupchars:") or data.startswith("groupmode:"):
        if parse_topic_scope(chat_id)[1] is None:
            answer_callback(token, str(callback.get("id", "")), "Forum Topic required")
            remove_inline_keyboard(token, callback)
            return
        if data.startswith("groupchars:") and not data.startswith("groupchars:page:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                resolved = resolve_dynamic_callback_token(parts[2], "group_character", chat_id)
                data = f"groupchars:{parts[1]}:{resolved or ''}"
        handle_group_panel_callback(db, token, chat_id, session, data, message, operation_id, sender_id=sender)
        return
    handle_provider_model_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id)


_bind_module_dependencies(__name__, globals())

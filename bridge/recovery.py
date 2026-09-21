"""Late-loaded crash-recovery corrections for durable bridge operations.

The runtime intentionally executes domain modules in one shared namespace.  This
module is loaded last so the durability-sensitive functions below replace their
legacy implementations without holding SQLite writer transactions across slow
provider or Telegram I/O.
"""





def _operation_payload_key(operation_id):
    return f"operation_payload:{operation_id}"


def _set_operation_payload(db, operation_id, payload):
    if operation_id is None:
        return
    def write():
        db.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            (_operation_payload_key(operation_id), json.dumps(payload, separators=(",", ":"))),
        )
        db.commit()
    run_write_txn(db, write)


def _get_operation_payload(db, operation_id):
    if operation_id is None:
        return {}
    raw = get_meta(db, _operation_payload_key(operation_id), "")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _finish_operation(db, operation_id, kind):
    if operation_id is None:
        return
    def write():
        record_operation(db, operation_id, kind)
        db.execute("DELETE FROM meta WHERE key=?", (_operation_payload_key(operation_id),))
        db.commit()
    run_write_txn(db, write)


def _message_ids_from_rows(rows):
    result = []
    for legacy_id, encoded_ids in rows:
        if legacy_id not in {None, ""}:
            result.append(str(legacy_id))
        try:
            decoded = json.loads(encoded_ids or "[]")
        except (TypeError, json.JSONDecodeError):
            decoded = []
        if isinstance(decoded, list):
            result.extend(str(item) for item in decoded if item not in {None, ""})
    return list(dict.fromkeys(result))


def _outgoing_ids_after(db, chat_id, session_id, rowid):
    rows = db.execute(
        "SELECT telegram_message_id,telegram_message_ids FROM messages WHERE chat_id=? AND session_id=? AND role='assistant' AND rowid>? ORDER BY rowid",
        (chat_id, session_id, int(rowid)),
    ).fetchall()
    return _message_ids_from_rows(rows)


def _delete_stored_telegram_ids(token, chat_id, message_ids):
    for message_id in message_ids or []:
        try:
            telegram_request(token, "deleteMessage", {"chat_id": chat_id, "message_id": int(message_id)})
        except Exception:
            logging.info("Recovery cleanup could not delete Telegram message %s", message_id, exc_info=True)


def _selected_variant_index(db, chat_id, session_id, user_rowid):
    row = db.execute(
        "SELECT variant_index FROM response_variants WHERE chat_id=? AND session_id=? AND user_rowid=? AND selected=1 ORDER BY id DESC LIMIT 1",
        (chat_id, session_id, int(user_rowid)),
    ).fetchone()
    return int(row[0]) if row else 1


def _latest_user_row(db, chat_id, session_id):
    return db.execute(
        "SELECT rowid,content FROM messages WHERE chat_id=? AND session_id=? AND role='user' ORDER BY rowid DESC LIMIT 1",
        (chat_id, session_id),
    ).fetchone()


def _latest_assistant_row(db, chat_id, session_id):
    return db.execute(
        "SELECT rowid,content FROM messages WHERE chat_id=? AND session_id=? AND role='assistant' ORDER BY rowid DESC LIMIT 1",
        (chat_id, session_id),
    ).fetchone()


def _prepare_delivery_recovery(db, token, chat_id, assistant_rowid, operation_id):
    # If Telegram accepted the previous attempt before the process crashed,
    # remove that recorded delivery before re-sending so recovery converges to
    # one visible assistant response.
    try:
        delete_outgoing_message_row(db, token, chat_id, int(assistant_rowid))
    except Exception:
        logging.info("Recovery could not clear the current assistant delivery", exc_info=True)
    payload = _get_operation_payload(db, operation_id)
    _delete_stored_telegram_ids(token, chat_id, payload.get("old_message_ids") or [])


def _generate_rendered_reply(db, token, api_key, session, chat_id, messages, query, rag_bundle):
    """Generate for the session model, append RAG citations, render the session language."""
    session_id = session["session_id"]
    send_typing(token, chat_id)
    settings = get_generation_settings(db, chat_id, session_id)
    reply = generate_text(
        api_key,
        session["model_id"],
        messages,
        session_id=f"telegram:{chat_id}:{session_id}",
        settings=settings,
    )
    reply += rag_citation_footer(db, chat_id, query, rag_bundle)
    return render_session_response(api_key, session, reply, chat_id, settings)


def _begin_durable_operation(db, operation_id, kind, deliver_recovered):
    """Shared phase guard for durable operations.

    Returns True when the caller should run the normal path. Applied operations
    are skipped, and locally-committed operations are delivered by the
    caller-supplied hook before finishing the operation.
    """
    if operation_id is None:
        return True
    phase = operation_phase(db, operation_id)
    if phase == "applied":
        return False
    if phase == "local_committed":
        deliver_recovered()
        return False
    return begin_operation(db, operation_id, kind)





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

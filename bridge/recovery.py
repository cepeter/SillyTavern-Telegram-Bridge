"""Late-loaded crash-recovery corrections for durable bridge operations.

The runtime intentionally executes domain modules in one shared namespace.  This
module is loaded last so the durability-sensitive functions below replace their
legacy implementations without holding SQLite writer transactions across slow
provider or Telegram I/O.
"""

import contextvars

_OPERATION_CONTEXT = contextvars.ContextVar("bridge_operation_id", default=None)
_ORIGINAL_PROCESS_MESSAGE = process_message



def begin_operation(db, operation_id, kind):
    """Persist the prepared marker immediately; never keep a writer lock over I/O."""
    if operation_id is None:
        return True
    def write():
        now = time.time()
        cursor = db.execute(
            "INSERT OR IGNORE INTO operations(operation_id,kind,state,created_at,updated_at) VALUES(?,?, 'in_progress',?,?)",
            (str(operation_id), kind, now, now),
        )
        if cursor.rowcount == 1:
            db.commit()
            return True
        return False
    inserted = run_write_txn(db, write)
    if inserted:
        return True
    return not operation_was_applied(db, operation_id)


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


def regenerate_last(db, token, api_key, session, fields, chat_id, operation_id=None):
    session_id = session["session_id"]

    def deliver_recovered_regen():
        user_row = _latest_user_row(db, chat_id, session_id)
        assistant_row = _latest_assistant_row(db, chat_id, session_id)
        if not user_row or not assistant_row:
            raise RuntimeError("regen recovery state is incomplete")
        _prepare_delivery_recovery(db, token, chat_id, assistant_row[0], operation_id)
        variant = _selected_variant_index(db, chat_id, session_id, user_row[0])
        send_reply(token, chat_id, f"♻️ Regenerated response (variant {variant})\n\n{assistant_row[1]}", db, session_id, int(assistant_row[0]))
        _finish_operation(db, operation_id, "regen")

    if not _begin_durable_operation(db, operation_id, "regen", deliver_recovered_regen):
        return

    rows = db.execute(
        "SELECT rowid,role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid",
        (chat_id, session_id),
    ).fetchall()
    last_user_index = next((i for i in range(len(rows) - 1, -1, -1) if rows[i][1] == "user"), None)
    if last_user_index is None:
        send_text(token, chat_id, "Tidak ada pesan user untuk di-regenerate.")
        return
    user_text = rows[last_user_index][2]
    history_rows = [(row[1], row[2]) for row in rows[:last_user_index]]
    rag_bundle = rag_retrieval_bundle(db, chat_id, user_text)
    messages = build_chat_messages(
        session,
        fields,
        user_text,
        history_rows,
        memory_context=recall_memory_context(db, chat_id, session, fields, user_text),
        session_summary=session_summary_for_prompt(db, chat_id, session),
        rag_context=rag_context_for_prompt(db, chat_id, user_text, rag_bundle),
    )
    reply = _generate_rendered_reply(db, token, api_key, session, chat_id, messages, user_text, rag_bundle)
    last_user_rowid = int(rows[last_user_index][0])
    old_message_ids = _outgoing_ids_after(db, chat_id, session_id, last_user_rowid)
    _set_operation_payload(db, operation_id, {"old_message_ids": old_message_ids, "user_rowid": last_user_rowid})

    def persist_regeneration():
        db.execute("DELETE FROM messages WHERE chat_id=? AND session_id=? AND rowid>?", (chat_id, session_id, last_user_rowid))
        assistant_cursor = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (chat_id, session_id, "assistant", reply, time.time()),
        )
        assistant_rowid = int(assistant_cursor.lastrowid)
        variant = save_response_variant(db, chat_id, session_id, user_text, reply, user_rowid=last_user_rowid, commit=False)
        if operation_id is not None:
            set_operation_phase(db, operation_id, "regen", "local_committed")
        db.commit()
        return assistant_rowid, variant

    assistant_rowid, variant = run_write_txn(db, persist_regeneration)

    _delete_stored_telegram_ids(token, chat_id, old_message_ids)
    retain_session_memory(db, chat_id, session, fields)
    send_reply(token, chat_id, f"♻️ Regenerated response (variant {variant})\n\n{reply}", db, session_id, assistant_rowid)
    _finish_operation(db, operation_id, "regen")


def continue_last(db, token, api_key, session, fields, chat_id, operation_id=None):
    session_id = session["session_id"]

    def deliver_recovered_continue():
        assistant_row = _latest_assistant_row(db, chat_id, session_id)
        if not assistant_row:
            raise RuntimeError("continue recovery state is incomplete")
        _prepare_delivery_recovery(db, token, chat_id, assistant_row[0], operation_id)
        send_reply(token, chat_id, f"↪️ Continued response\n\n{assistant_row[1]}", db, session_id, int(assistant_row[0]))
        _finish_operation(db, operation_id, "continue")

    if not _begin_durable_operation(db, operation_id, "continue", deliver_recovered_continue):
        return

    rows = db.execute(
        "SELECT rowid,role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid",
        (chat_id, session_id),
    ).fetchall()
    assistant_row = next((row for row in reversed(rows) if row[1] == "assistant"), None)
    if assistant_row is None:
        send_text(token, chat_id, "Belum ada response untuk dilanjutkan.")
        return
    instruction = "Continue the previous assistant response from its exact ending. Do not repeat any existing text. Output only the continuation."
    history_rows = [(row[1], row[2]) for row in rows]
    rag_bundle = rag_retrieval_bundle(db, chat_id, instruction)
    messages = build_chat_messages(
        session,
        fields,
        instruction,
        history_rows,
        memory_context=recall_memory_context(db, chat_id, session, fields, instruction),
        session_summary=session_summary_for_prompt(db, chat_id, session),
        rag_context=rag_context_for_prompt(db, chat_id, instruction, rag_bundle),
    )
    reply = _generate_rendered_reply(db, token, api_key, session, chat_id, messages, instruction, rag_bundle)
    combined = assistant_row[2].rstrip() + " " + reply.lstrip()
    old_message_ids = _message_ids_from_rows(
        db.execute("SELECT telegram_message_id,telegram_message_ids FROM messages WHERE rowid=?", (int(assistant_row[0]),)).fetchall()
    )
    _set_operation_payload(db, operation_id, {"old_message_ids": old_message_ids, "assistant_rowid": int(assistant_row[0])})

    def persist_continuation():
        db.execute("UPDATE messages SET content=? WHERE rowid=?", (combined, assistant_row[0]))
        user_row = next((row for row in reversed(rows) if row[1] == "user" and row[0] < assistant_row[0]), None)
        if user_row:
            db.execute(
                "UPDATE response_variants SET response=? WHERE chat_id=? AND session_id=? AND user_rowid=? AND selected=1",
                (combined, chat_id, session_id, int(user_row[0])),
            )
        if operation_id is not None:
            set_operation_phase(db, operation_id, "continue", "local_committed")
        db.commit()

    run_write_txn(db, persist_continuation)

    _prepare_delivery_recovery(db, token, chat_id, assistant_row[0], operation_id)
    retain_session_memory(db, chat_id, session, fields)
    send_reply(token, chat_id, f"↪️ Continued response\n\n{combined}", db, session_id, int(assistant_row[0]))
    _finish_operation(db, operation_id, "continue")


def regenerate_edited_turn(db, token, api_key, session, fields, chat_id, user_rowid, new_text, operation_id=None):
    session_id = session["session_id"]

    def deliver_recovered_edit():
        user_row = _latest_user_row(db, chat_id, session_id)
        assistant_row = _latest_assistant_row(db, chat_id, session_id)
        if not user_row or not assistant_row:
            raise RuntimeError("edit recovery state is incomplete")
        _prepare_delivery_recovery(db, token, chat_id, assistant_row[0], operation_id)
        send_reply(token, chat_id, f"✏️ Edited message regenerated.\n\n{assistant_row[1]}", db, session_id, int(assistant_row[0]))
        _finish_operation(db, operation_id, "edit")

    if not _begin_durable_operation(db, operation_id, "edit", deliver_recovered_edit):
        return

    rows = db.execute(
        "SELECT rowid,role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid",
        (chat_id, session_id),
    ).fetchall()
    target_index = next((i for i, row in enumerate(rows) if int(row[0]) == int(user_rowid) and row[1] == "user"), None)
    if target_index is None:
        raise ValueError("Telegram message is not a user turn in the active session")
    history_rows = [(row[1], row[2]) for row in rows[:target_index]]
    memory_context = recall_memory_context(db, chat_id, session, fields, new_text)
    _covered_summary, covered_until = get_session_summary(db, chat_id, session_id)
    session_summary = "" if covered_until >= int(user_rowid) else session_summary_for_prompt(db, chat_id, session)
    rag_bundle = rag_retrieval_bundle(db, chat_id, new_text)
    messages = build_chat_messages(
        session,
        fields,
        new_text,
        history_rows,
        memory_context=memory_context,
        session_summary=session_summary,
        rag_context=rag_context_for_prompt(db, chat_id, new_text, rag_bundle),
    )
    reply = _generate_rendered_reply(db, token, api_key, session, chat_id, messages, new_text, rag_bundle)
    old_message_ids = _outgoing_ids_after(db, chat_id, session_id, int(user_rowid))
    _set_operation_payload(db, operation_id, {"old_message_ids": old_message_ids, "user_rowid": int(user_rowid)})

    def persist_edit():
        # Keep summary invalidation, transcript mutation, selected variant, and
        # the local_committed marker in one short SQLite transaction.
        db.execute("DELETE FROM session_summaries WHERE chat_id=? AND session_id=?", (chat_id, session_id))
        db.execute("UPDATE messages SET content=? WHERE rowid=?", (new_text, int(user_rowid)))
        db.execute("DELETE FROM messages WHERE chat_id=? AND session_id=? AND rowid>?", (chat_id, session_id, int(user_rowid)))
        assistant_cursor = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (chat_id, session_id, "assistant", reply, time.time()),
        )
        assistant_rowid = int(assistant_cursor.lastrowid)
        save_response_variant(db, chat_id, session_id, new_text, reply, user_rowid=int(user_rowid), commit=False)
        if operation_id is not None:
            set_operation_phase(db, operation_id, "edit", "local_committed")
        db.commit()
        return assistant_rowid

    assistant_rowid = run_write_txn(db, persist_edit)

    _delete_stored_telegram_ids(token, chat_id, old_message_ids)
    retain_session_memory(db, chat_id, session, fields)
    send_reply(token, chat_id, f"✏️ Edited message regenerated.\n\n{reply}", db, session_id, assistant_rowid)
    _finish_operation(db, operation_id, "edit")


def _operation_command(text):
    parts = str(text or "").strip().split(None, 1)
    if not parts:
        return ""
    command = parts[0].casefold()
    if command.startswith("@") and len(parts) > 1:
        command = parts[1].split(None, 1)[0].casefold()
    if command.startswith("/") and "@" in command:
        command = command.split("@", 1)[0]
    return command


def process_message(db, token, api_key, model, fields, chat_id, text, telegram_message_id=None, queued_session_id=None, operation_id=None):
    # Command-specific local_committed recovery must run before the legacy
    # generic recovery shortcut, otherwise cleanup/variant work is skipped.
    if operation_id is not None and operation_phase(db, operation_id) == "local_committed":
        command = _operation_command(text)
        session = load_session(db, chat_id, queued_session_id, model) if queued_session_id else ensure_session(db, chat_id, model)
        session_fields = card_fields_from_file(session["character_file"])
        if command == "/regen":
            return regenerate_last(db, token, api_key, session, session_fields, chat_id, operation_id=operation_id)
        if command == "/continue":
            return continue_last(db, token, api_key, session, session_fields, chat_id, operation_id=operation_id)
        if command == "/edit":
            edited_text = str(text or "").strip().split(None, 1)
            edited_text = edited_text[1].strip() if len(edited_text) > 1 else ""
            return edit_last_user(db, token, api_key, session, session_fields, chat_id, edited_text, operation_id=operation_id)

    context_token = _OPERATION_CONTEXT.set(operation_id)
    try:
        return _ORIGINAL_PROCESS_MESSAGE(
            db,
            token,
            api_key,
            model,
            fields,
            chat_id,
            text,
            telegram_message_id,
            queued_session_id=queued_session_id,
            operation_id=operation_id,
        )
    finally:
        _OPERATION_CONTEXT.reset(context_token)


def sync_status_text(db: sqlite3.Connection, chat_id: str, session: dict[str, str]) -> str:
    """Render Live API Sync status for the active session."""
    binding = ensure_sync_binding(db, chat_id, session["session_id"])
    count = db.execute("SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"])).fetchone()[0]
    if binding["last_synced_at"]:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(float(binding["last_synced_at"])))
        last = f"{binding['last_direction']} at {timestamp}"
    else:
        last = "never"
    return ("Live Sync\n\n"
            "Live Sync uses the local SillyTavern API.\n"
            f"Session: {session['session_id']}\n"
            f"Messages: {count}\n"
            f"Sync ID: {binding['sync_id']}\n"
            f"Last sync: {last}\n\n{phase3_sync_status_line(db, chat_id, session['session_id'])}")


def send_sync_menu(token: str, chat_id: str, db: sqlite3.Connection, session: dict[str, str], message_id: int | None = None) -> None:
    """Send or edit the Live API Sync panel."""
    payload = {
        "chat_id": chat_id,
        "text": sync_status_text(db, chat_id, session),
        "reply_markup": {"inline_keyboard": [
            [{"text": "🚀 Realtime API: toggle", "callback_data": "sync:realtime"}],
            [{"text": "🔁 Sync now", "callback_data": "sync:now"}],
            [{"text": "🔄 Refresh status", "callback_data": "sync:status"}],
            [{"text": "❌ Close", "callback_data": "sync:close"}],
        ]},
    }
    send_panel_message(token, chat_id, payload["text"], payload["reply_markup"], message_id)


def handle_sync_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
    """Handle Live API Sync callbacks."""
    if not data.startswith("sync:"):
        return False
    action = data.split(":", 1)[1]
    message_id = message.get("message_id")
    if action == "close":
        answer_callback(token, str(callback.get("id", "")), "Closed")
        close_panel_message(token, chat_id, callback)
    elif action in {"menu", "status"}:
        answer_callback(token, str(callback.get("id", "")), "Sync status")
        send_sync_menu(token, chat_id, db, session, message_id)
    elif action == "realtime":
        result = phase3_toggle_realtime(db, chat_id, session_id)
        answer_callback(token, str(callback.get("id", "")), result[:200])
        send_sync_menu(token, chat_id, db, session, message_id)
    elif action == "now":
        try:
            result = phase3_sync_now(db, chat_id, session_id)
        except (SillyTavernApiError, ValueError) as exc:
            _phase3_disable(db, chat_id, session_id, str(exc))
            result = f"Live API unavailable: {exc}"
        answer_callback(token, str(callback.get("id", "")), result[:200])
        send_sync_menu(token, chat_id, db, session, message_id)
    else:
        answer_callback(token, str(callback.get("id", "")), "Unknown sync action")
    return True

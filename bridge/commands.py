from __future__ import annotations

from bridge.operation_recovery import (
    OperationRecovery as _OperationRecovery,
)


_COMMAND_OPERATION_RECOVERY = _OperationRecovery(
    operation_phase=lambda db, operation_id: operation_phase(
        db,
        operation_id,
    ),
    begin_operation=lambda db, operation_id, kind: begin_operation(
        db,
        operation_id,
        kind,
    ),
    record_operation=lambda db, operation_id, kind: record_operation(
        db,
        operation_id,
        kind,
    ),
    run_write_txn=lambda db, operation: run_write_txn(
        db,
        operation,
    ),
    get_meta=lambda db, key, default="": get_meta(
        db,
        key,
        default,
    ),
    telegram_request=lambda token, method, payload: telegram_request(
        token,
        method,
        payload,
    ),
    delete_outgoing_message_row=(
        lambda db, token, chat_id, rowid:
        delete_outgoing_message_row(
            db,
            token,
            chat_id,
            rowid,
        )
    ),
    log_info=lambda message, *args, **kwargs: logging.info(
        message,
        *args,
        **kwargs,
    ),
)



def process_image_message(db: sqlite3.Connection, token: str, api_key: str, session: dict, fields: dict, chat_id: str, caption: str, image_bytes: bytes, mime_type: str = "image/jpeg", telegram_message_id: int | None = None, *, memory_service=None, persona_service=None) -> None:
    memory_service = resolve_memory_service(memory_service)
    caption = caption.strip()[:12000] or "Please analyze this image in the context of the conversation."
    group_turn = group_current_speaker(db, chat_id, session, caption)
    group_context = ""
    if group_turn:
        fields = card_fields_from_file(group_turn[0])
        group_context = group_prompt_context(db, chat_id, session, group_turn[0])
    image_data_uri = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    rows = db.execute("SELECT role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid", (chat_id, session["session_id"])).fetchall()
    history_rows = [(row[0], row[1]) for row in rows[-MAX_HISTORY_MESSAGES:]]
    rag_bundle = rag_retrieval_bundle(db, chat_id, caption)
    memory_prompt = memory_service.prompt_context(
        db,
        chat_id,
        session,
        fields,
        caption,
    )
    memory_context = memory_prompt.recall
    session_summary = memory_prompt.summary
    messages = build_chat_messages(session, fields, caption, history_rows, image_data_uri=image_data_uri, memory_context=memory_context, session_summary=session_summary, rag_context=rag_context_for_prompt(db, chat_id, caption, rag_bundle), group_context=group_context, persona_service=persona_service)
    send_typing(token, chat_id)
    reply = generate_text(api_key, session["model_id"], messages, session_id=f"telegram:{chat_id}:{session['session_id']}", settings=get_generation_settings(db, chat_id, session["session_id"]))
    reply += rag_citation_footer(db, chat_id, caption, rag_bundle)
    reply = render_session_response(api_key, session, reply, chat_id, get_generation_settings(db, chat_id, session["session_id"]))
    stored_reply = reply if group_turn and group_turn[1].get("mode") == "autonomous" else (f"{fields['name']}: {reply}" if group_turn else reply)
    stored_text = f"[Image input] {caption}"
    with write_transaction(db):
        db.execute("INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) VALUES(?,?,?,?,?,?)", (chat_id, session["session_id"], "user", stored_text, str(telegram_message_id) if telegram_message_id is not None else None, time.time()))
        assistant_cursor = db.execute("INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)", (chat_id, session["session_id"], "assistant", stored_reply, time.time()))
        assistant_rowid = assistant_cursor.lastrowid
        save_response_variant(
            db,
            chat_id,
            session["session_id"],
            stored_text,
            stored_reply,
            commit=False,
        )
        if group_turn:
            advance_group_turn(db, chat_id, session["session_id"])
    memory_service.retain(db, chat_id, session, fields)
    send_reply(token, chat_id, stored_reply, db, session["session_id"], assistant_rowid)



def regenerate_edited_turn(
    db: sqlite3.Connection,
    token: str,
    api_key: str,
    session: dict[str, str],
    fields: dict[str, str],
    chat_id: str,
    user_rowid: int,
    new_text: str,
    operation_id: int | str | None = None,
    *,
    memory_service=None,
    persona_service=None,
) -> None:
    memory_service = resolve_memory_service(memory_service)
    session_id = session["session_id"]

    def deliver_recovered_edit():
        user_row = _COMMAND_OPERATION_RECOVERY.latest_user_row(
            db,
            chat_id,
            session_id,
        )
        assistant_row = (
            _COMMAND_OPERATION_RECOVERY.latest_assistant_row(
                db,
                chat_id,
                session_id,
            )
        )
        if not user_row or not assistant_row:
            raise RuntimeError("edit recovery state is incomplete")
        _COMMAND_OPERATION_RECOVERY.prepare_delivery(
            db,
            token,
            chat_id,
            assistant_row[0],
            operation_id,
        )
        send_reply(
            token,
            chat_id,
            f"✏️ Edited message regenerated.\n\n{assistant_row[1]}",
            db,
            session_id,
            int(assistant_row[0]),
        )
        _COMMAND_OPERATION_RECOVERY.finish(
            db,
            operation_id,
            "edit",
        )

    if not _COMMAND_OPERATION_RECOVERY.begin_or_recover(
        db,
        operation_id,
        "edit",
        deliver_recovered_edit,
    ):
        return

    rows = db.execute(
        "SELECT rowid,role,content FROM messages "
        "WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid",
        (chat_id, session_id),
    ).fetchall()
    target_index = next(
        (
            i
            for i, row in enumerate(rows)
            if int(row[0]) == int(user_rowid)
            and row[1] == "user"
        ),
        None,
    )
    if target_index is None:
        raise ValueError(
            "Telegram message is not a user turn in the active session"
        )

    history_rows = [
        (row[1], row[2])
        for row in rows[:target_index]
    ]
    memory_prompt = memory_service.prompt_context(
        db,
        chat_id,
        session,
        fields,
        new_text,
        edited_user_rowid=int(user_rowid),
    )
    rag_bundle = rag_retrieval_bundle(
        db,
        chat_id,
        new_text,
    )
    messages = build_chat_messages(
        session,
        fields,
        new_text,
        history_rows,
        memory_context=memory_prompt.recall,
        session_summary=memory_prompt.summary,
        persona_service=persona_service,
        rag_context=rag_context_for_prompt(
            db,
            chat_id,
            new_text,
            rag_bundle,
        ),
    )
    send_typing(token, chat_id)
    generation_settings = get_generation_settings(
        db,
        chat_id,
        session_id,
    )
    reply = generate_text(
        api_key,
        session["model_id"],
        messages,
        session_id=f"telegram:{chat_id}:{session_id}",
        settings=generation_settings,
    )
    reply += rag_citation_footer(
        db,
        chat_id,
        new_text,
        rag_bundle,
    )
    reply = render_session_response(
        api_key,
        session,
        reply,
        chat_id,
        generation_settings,
    )
    old_message_ids = (
        _COMMAND_OPERATION_RECOVERY.outgoing_ids_after(
            db,
            chat_id,
            session_id,
            int(user_rowid),
        )
    )
    _COMMAND_OPERATION_RECOVERY.set_payload(
        db,
        operation_id,
        {
            "old_message_ids": old_message_ids,
            "user_rowid": int(user_rowid),
        },
    )

    def persist_edit():
        db.execute(
            "DELETE FROM session_summaries "
            "WHERE chat_id=? AND session_id=?",
            (chat_id, session_id),
        )
        db.execute(
            "UPDATE messages SET content=? WHERE rowid=?",
            (new_text, int(user_rowid)),
        )
        db.execute(
            "DELETE FROM messages "
            "WHERE chat_id=? AND session_id=? AND rowid>?",
            (chat_id, session_id, int(user_rowid)),
        )
        assistant_cursor = db.execute(
            "INSERT INTO messages("
            "chat_id,session_id,role,content,created_at"
            ") VALUES(?,?,?,?,?)",
            (
                chat_id,
                session_id,
                "assistant",
                reply,
                time.time(),
            ),
        )
        assistant_rowid = int(assistant_cursor.lastrowid)
        save_response_variant(
            db,
            chat_id,
            session_id,
            new_text,
            reply,
            user_rowid=int(user_rowid),
            commit=False,
        )
        if operation_id is not None:
            set_operation_phase(
                db,
                operation_id,
                "edit",
                "local_committed",
            )
        db.commit()
        return assistant_rowid

    assistant_rowid = run_write_txn(
        db,
        persist_edit,
    )
    _COMMAND_OPERATION_RECOVERY.delete_stored_telegram_ids(
        token,
        chat_id,
        old_message_ids,
    )
    memory_service.retain(
        db,
        chat_id,
        session,
        fields,
    )
    send_reply(
        token,
        chat_id,
        f"✏️ Edited message regenerated.\n\n{reply}",
        db,
        session_id,
        assistant_rowid,
    )
    _COMMAND_OPERATION_RECOVERY.finish(
        db,
        operation_id,
        "edit",
    )


def edit_last_user(db: sqlite3.Connection, token: str, api_key: str, session: dict[str, str], fields: dict[str, str], chat_id: str, new_text: str, operation_id: int | str | None = None, *, memory_service=None, persona_service=None) -> None:
    session_id = session["session_id"]
    rows = db.execute("SELECT rowid,role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid", (chat_id, session_id)).fetchall()
    last_user = next((row for row in reversed(rows) if row[1] == "user"), None)
    if last_user is None:
        send_text(token, chat_id, "Belum ada pesan user untuk diedit.")
        return
    regenerate_edited_turn(db, token, api_key, session, fields, chat_id, int(last_user[0]), new_text, operation_id=operation_id, memory_service=memory_service, persona_service=persona_service)


def edit_telegram_user_message(db: sqlite3.Connection, token: str, api_key: str, chat_id: str, message_id: int, new_text: str, default_model: str, operation_id: int | str | None = None, *, memory_service=None, persona_service=None) -> None:
    session = ensure_session(db, chat_id, default_model)
    fields = card_fields_from_file(session["character_file"])
    row = db.execute("SELECT rowid,session_id,role FROM messages WHERE chat_id=? AND telegram_message_id=? ORDER BY rowid DESC LIMIT 1", (chat_id, str(message_id))).fetchone()
    if row is None or row[2] != "user" or row[1] != session["session_id"]:
        send_text(token, chat_id, "Edited message was not found in the active session.")
        return
    if not new_text.strip():
        send_text(token, chat_id, "Edited message cannot be empty.")
        return
    regenerate_edited_turn(db, token, api_key, session, fields, chat_id, int(row[0]), new_text.strip()[:12000], operation_id=operation_id, memory_service=memory_service, persona_service=persona_service)


def send_stscript_menu(token: str, chat_id: str, message_id: int | None = None) -> None:
    """Show the allowlisted STscript actions without accepting arbitrary scripts."""
    payload = {"chat_id": chat_id, "text": "Safe STscript actions:\n\nReset clears only the active session after confirmation.", "reply_markup": {"inline_keyboard": [[{"text": "♻️ Reset", "callback_data": "enum:stscript:reset"}], [{"text": "❌ Close", "callback_data": "enum:stscript:cancel"}]]}}
    method = "editMessageText" if message_id else "sendMessage"
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_note_menu(token: str, chat_id: str, current_note: str, message_id: int | None = None) -> None:
    state = "on" if str(current_note or "").strip() else "off"
    text = f"Author's Note — {state}\nCurrent length: {len(str(current_note or '').strip())} characters\nChoose an action:"
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": [
        [{"text": "🚫 Off", "callback_data": "note:off"}, {"text": "✏️ User input", "callback_data": "note:input"}],
        [{"text": "❌ Close", "callback_data": "note:cancel"}],
    ]}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def handle_macro_command(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], fields: dict, command_text: str) -> None:
    parts = command_text.split(None, 1)
    if parts[0].casefold() == "/macro":
        raw = parts[1] if len(parts) > 1 else ""
        send_text(token, chat_id, replace_macros(raw, fields, persona_name(session["persona_id"]) if session["persona_id"] else "user"))
        return
    script = parts[1].strip() if len(parts) > 1 else ""
    action, _, argument = script.partition(" ")
    if action.casefold() == "reset":
        send_reset_confirmation_menu(token, chat_id)
    else:
        send_text(token, chat_id, "Use /stscript to open the safe Reset action panel.")


def apply_preset_action(db: sqlite3.Connection, token: str, chat_id: str, session_id: str, action: str, name: str) -> None:
    name = str(name).strip()
    if action not in {"use", "delete"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        send_text(token, chat_id, "Invalid preset panel action.")
        return
    if action == "use":
        settings = load_generation_preset(db, chat_id, name)
        if not settings:
            send_text(token, chat_id, f"Preset not found: {name}")
            return
        update_generation_settings(db, chat_id, session_id, **settings)
        send_text(token, chat_id, f"Preset applied to this session: {name}\n{format_generation_settings(get_generation_settings(db, chat_id, session_id))}")
        return
    send_text(token, chat_id, f"Preset deleted: {name}" if delete_generation_preset(db, chat_id, name) else f"Preset not found: {name}")


def prompt_diagnostics(db: sqlite3.Connection, chat_id: str, session: dict[str, str], fields: dict[str, str], *, memory_service=None) -> str:
    memory_service = resolve_memory_service(memory_service)
    message_count = db.execute("SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"])).fetchone()[0]
    summary, covered_until = memory_service.summary_status(db, chat_id, session["session_id"])
    docs = data_bank_documents(db, chat_id)
    group = group_state(db, chat_id, session["session_id"])
    return (f"Prompt inspector\nCharacter: {fields['name']}\nMessages: {message_count}\n"
            f"Context input budget: ~{context_input_budget_tokens()} tokens\nHistory candidates: {context_history_candidate_limit()} messages\nSession summary: {len(summary)} chars (through row {covered_until})\n"
            f"Hindsight: {memory_mode(db, chat_id)} / {memory_scope(db, chat_id)}\n"
            f"Data Bank: {rag_mode(db, chat_id)} / {len(docs)} documents\n"
            f"Group: {'on' if group['enabled'] else 'off'} / mode={group['mode']} / members={len(group['members'])}\n"
            "Macro support: char, user, random, pick, time, date, weekday\nWorld Info recursion: maximum 3 passes")


# Explicit late imports replace transitional dependency injection.
import base64
import logging
import re
import sqlite3
import time
from bridge.card_content import (
    card_fields_from_file,
    replace_macros,
)
from bridge.cards import persona_name
from bridge.common import MAX_HISTORY_MESSAGES
from bridge.context_compaction import (
    context_history_candidate_limit,
    context_input_budget_tokens,
)
from bridge.database import (
    begin_operation,
    delete_generation_preset,
    format_generation_settings,
    get_generation_settings,
    get_meta,
    load_generation_preset,
    operation_phase,
    record_operation,
    run_write_txn,
    set_operation_phase,
    update_generation_settings,
    write_transaction,
)
from bridge.generation import (
    build_chat_messages,
    generate_text,
    render_session_response,
    save_response_variant,
)
from bridge.group_core import (
    advance_group_turn,
    group_current_speaker,
    group_state,
)
from bridge.groups import group_prompt_context
from bridge.media import (
    delete_outgoing_message_row,
    send_reply,
    send_typing,
)
from bridge.memory import resolve_memory_service
from bridge.memory_backend import (
    memory_mode,
    memory_scope,
)
from bridge.message_commands import send_reset_confirmation_menu
from bridge.rag_core import (
    data_bank_documents,
    rag_citation_footer,
    rag_context_for_prompt,
    rag_mode,
    rag_retrieval_bundle,
)
from bridge.telegram import (
    ensure_session,
    send_text,
    telegram_request,
)

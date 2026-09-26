"""Canonical edit messages owner."""

from __future__ import annotations

import logging
import sqlite3
import time

from bridge.card_content import card_fields_from_file
from bridge.generation import build_chat_messages, render_session_response
from bridge.generation_settings import get_generation_settings
from bridge.light_novel_turn import begin_novel_turn
from bridge.memory_service import MemoryService
from bridge.metadata import get_meta
from bridge.operation_recovery import OperationRecovery as _OperationRecovery
from bridge.operations import begin_operation, operation_phase, record_operation, set_operation_phase
from bridge.persona_service import PersonaService
from bridge.provider_port import ProviderPort
from bridge.rag_service import RagService
from bridge.response_delivery import delete_outgoing_message_row, send_reply
from bridge.response_variants import save_response_variant
from bridge.session_core import load_session
from bridge.settings import AppSettings
from bridge.sqlite_store import write_transaction
from bridge.telegram import send_text, send_typing, telegram_request
from bridge.transcript_repository import native_edit_target

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
    write_transaction=write_transaction,
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
        lambda db, token, chat_id, rowid: delete_outgoing_message_row(
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
    provider_port: ProviderPort,
    memory_service: MemoryService,
    persona_service: PersonaService,
    app_settings: AppSettings,
    rag_service: RagService,
) -> None:
    session_id = session["session_id"]

    def deliver_recovered_edit():
        user_row = _COMMAND_OPERATION_RECOVERY.latest_user_row(
            db,
            chat_id,
            session_id,
        )
        assistant_row = _COMMAND_OPERATION_RECOVERY.latest_assistant_row(
            db,
            chat_id,
            session_id,
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
            app_settings=app_settings,
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
        "SELECT rowid,role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid",
        (chat_id, session_id),
    ).fetchall()
    target_index = next(
        (i for i, row in enumerate(rows) if int(row[0]) == int(user_rowid) and row[1] == "user"),
        None,
    )
    if target_index is None:
        raise ValueError("Telegram message is not a user turn in the active session")

    history_rows = [(row[1], row[2]) for row in rows[:target_index]]
    memory_prompt = memory_service.prompt_context(
        db,
        chat_id,
        session,
        fields,
        new_text,
        edited_user_rowid=int(user_rowid),
    )
    rag_bundle = rag_service.bundle(db, chat_id, new_text)
    messages = build_chat_messages(
        session,
        fields,
        new_text,
        history_rows,
        memory_context=memory_prompt.recall,
        session_summary=memory_prompt.summary,
        persona_service=persona_service,
        rag_context=rag_service.context_for_prompt(db, chat_id, new_text, rag_bundle),
        app_settings=app_settings,
    )
    send_typing(token, chat_id)
    generation_settings = get_generation_settings(
        db,
        chat_id,
        session_id,
    )
    novel_turn = begin_novel_turn(db, chat_id, session, "edit", operation_id)
    if novel_turn:
        messages = novel_turn.messages(messages, session.get("response_language") or "auto")
    reply = provider_port.generate(
        api_key,
        session["model_id"],
        messages,
        session_id=f"telegram:{chat_id}:{session_id}",
        settings=generation_settings,
    )
    if novel_turn:
        reply = novel_turn.extract(reply)
    reply += rag_service.citation_footer(db, chat_id, new_text, rag_bundle)
    reply = render_session_response(
        api_key,
        session,
        reply,
        chat_id,
        generation_settings,
        provider_port=provider_port,
    )
    old_message_ids = _COMMAND_OPERATION_RECOVERY.outgoing_ids_after(
        db,
        chat_id,
        session_id,
        int(user_rowid),
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
            "DELETE FROM session_summaries WHERE chat_id=? AND session_id=?",
            (chat_id, session_id),
        )
        db.execute(
            "UPDATE messages SET content=? WHERE rowid=?",
            (new_text, int(user_rowid)),
        )
        db.execute(
            "DELETE FROM messages WHERE chat_id=? AND session_id=? AND rowid>?",
            (chat_id, session_id, int(user_rowid)),
        )
        assistant_cursor = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (
                chat_id,
                session_id,
                "assistant",
                reply,
                time.time(),
            ),
        )
        assistant_rowid = int(assistant_cursor.lastrowid)
        if novel_turn:
            novel_turn.commit(db, assistant_rowid, reply)
        save_response_variant(db, chat_id, session_id, new_text, reply, user_rowid=int(user_rowid))
        if operation_id is not None:
            set_operation_phase(
                db,
                operation_id,
                "edit",
                "local_committed",
            )

        return assistant_rowid

    with write_transaction(db):
        assistant_rowid = persist_edit()
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
        app_settings=app_settings,
    )
    _COMMAND_OPERATION_RECOVERY.finish(
        db,
        operation_id,
        "edit",
    )


def edit_last_user(
    db: sqlite3.Connection,
    token: str,
    api_key: str,
    session: dict[str, str],
    fields: dict[str, str],
    chat_id: str,
    new_text: str,
    operation_id: int | str | None = None,
    *,
    provider_port: ProviderPort,
    memory_service: MemoryService,
    persona_service: PersonaService,
    app_settings: AppSettings,
    rag_service: RagService,
) -> None:
    session_id = session["session_id"]
    rows = db.execute(
        "SELECT rowid,role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid",
        (chat_id, session_id),
    ).fetchall()
    last_user = next((row for row in reversed(rows) if row[1] == "user"), None)
    if last_user is None:
        send_text(token, chat_id, "Belum ada pesan user untuk diedit.")
        return
    regenerate_edited_turn(
        db,
        token,
        api_key,
        session,
        fields,
        chat_id,
        int(last_user[0]),
        new_text,
        operation_id=operation_id,
        provider_port=provider_port,
        memory_service=memory_service,
        persona_service=persona_service,
        app_settings=app_settings,
        rag_service=rag_service,
    )


def edit_telegram_user_message(
    db: sqlite3.Connection,
    token: str,
    api_key: str,
    chat_id: str,
    message_id: int,
    new_text: str,
    default_model: str,
    operation_id: int | str | None = None,
    *,
    provider_port: ProviderPort,
    memory_service: MemoryService,
    persona_service: PersonaService,
    app_settings: AppSettings,
    rag_service: RagService,
) -> None:
    row = native_edit_target(db, chat_id, message_id)
    if row is None or row[2] != "user":
        send_text(token, chat_id, "Edited message was not found.")
        return
    if not new_text.strip():
        send_text(token, chat_id, "Edited message cannot be empty.")
        return
    session = load_session(db, chat_id, str(row[1]), default_model, app_settings=app_settings)
    fields = card_fields_from_file(session["character_file"], app_settings=app_settings)
    regenerate_edited_turn(
        db,
        token,
        api_key,
        session,
        fields,
        chat_id,
        int(row[0]),
        new_text.strip()[:12000],
        operation_id=operation_id,
        provider_port=provider_port,
        memory_service=memory_service,
        persona_service=persona_service,
        app_settings=app_settings,
        rag_service=rag_service,
    )

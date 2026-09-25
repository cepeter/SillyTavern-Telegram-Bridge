"""Canonical continuation owner."""

from __future__ import annotations

import sqlite3

from bridge.delivery_port import DeliveryPort
from bridge.generation import _generation_generate_rendered_reply, build_chat_messages
from bridge.generation_recovery import _generation_operation_recovery
from bridge.memory_service import MemoryService
from bridge.operations import set_operation_phase
from bridge.persona_service import PersonaService
from bridge.provider_port import ProviderPort
from bridge.rag_core import rag_context_for_prompt, rag_retrieval_bundle
from bridge.settings import AppSettings
from bridge.sqlite_store import write_transaction


def continue_last(
    db: sqlite3.Connection,
    token: str,
    api_key: str,
    session: dict[str, str],
    fields: dict[str, str],
    chat_id: str,
    operation_id: int | str | None = None,
    *,
    provider_port: ProviderPort,
    delivery_port: DeliveryPort,
    memory_service: MemoryService,
    persona_service: PersonaService,
    app_settings: AppSettings,
) -> None:
    session_id = session["session_id"]
    recovery = _generation_operation_recovery(delivery_port)

    def deliver_recovered_continue():
        assistant_row = recovery.latest_assistant_row(
            db,
            chat_id,
            session_id,
        )
        if not assistant_row:
            raise RuntimeError("continue recovery state is incomplete")
        recovery.prepare_delivery(
            db,
            token,
            chat_id,
            assistant_row[0],
            operation_id,
        )
        delivery_port.send_reply(
            token,
            chat_id,
            f"↪️ Continued response\n\n{assistant_row[1]}",
            db,
            session_id,
            int(assistant_row[0]),
        )
        recovery.finish(
            db,
            operation_id,
            "continue",
        )

    if not recovery.begin_or_recover(
        db,
        operation_id,
        "continue",
        deliver_recovered_continue,
    ):
        return

    rows = db.execute(
        "SELECT rowid,role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid",
        (chat_id, session_id),
    ).fetchall()
    assistant_row = next(
        (row for row in reversed(rows) if row[1] == "assistant"),
        None,
    )
    if assistant_row is None:
        delivery_port.send_text(
            token,
            chat_id,
            "Belum ada response untuk dilanjutkan.",
        )
        return

    instruction = (
        "Continue the previous assistant response from its exact ending. "
        "Do not repeat any existing text. Output only the continuation."
    )
    history_rows = [(row[1], row[2]) for row in rows]
    rag_bundle = rag_retrieval_bundle(db, chat_id, instruction, app_settings=app_settings)
    memory_prompt = memory_service.prompt_context(
        db,
        chat_id,
        session,
        fields,
        instruction,
    )
    messages = build_chat_messages(
        session,
        fields,
        instruction,
        history_rows,
        memory_context=memory_prompt.recall,
        session_summary=memory_prompt.summary,
        persona_service=persona_service,
        rag_context=rag_context_for_prompt(db, chat_id, instruction, rag_bundle, app_settings=app_settings),
        app_settings=app_settings,
    )
    reply = _generation_generate_rendered_reply(
        db,
        token,
        api_key,
        session,
        chat_id,
        messages,
        instruction,
        rag_bundle,
        provider_port=provider_port,
        delivery_port=delivery_port,
        app_settings=app_settings,
    )
    combined = assistant_row[2].rstrip() + " " + reply.lstrip()
    old_message_ids = recovery.message_ids_from_rows(
        db.execute(
            "SELECT telegram_message_id,telegram_message_ids FROM messages WHERE rowid=?",
            (int(assistant_row[0]),),
        ).fetchall()
    )
    recovery.set_payload(
        db,
        operation_id,
        {
            "old_message_ids": old_message_ids,
            "assistant_rowid": int(assistant_row[0]),
        },
    )

    def persist_continuation():
        db.execute(
            "UPDATE messages SET content=? WHERE rowid=?",
            (combined, assistant_row[0]),
        )
        user_row = next(
            (row for row in reversed(rows) if row[1] == "user" and row[0] < assistant_row[0]),
            None,
        )
        if user_row:
            db.execute(
                "UPDATE response_variants SET response=? "
                "WHERE chat_id=? AND session_id=? "
                "AND user_rowid=? AND selected=1",
                (
                    combined,
                    chat_id,
                    session_id,
                    int(user_row[0]),
                ),
            )
        if operation_id is not None:
            set_operation_phase(
                db,
                operation_id,
                "continue",
                "local_committed",
            )

    with write_transaction(db):
        persist_continuation()
    recovery.prepare_delivery(
        db,
        token,
        chat_id,
        assistant_row[0],
        operation_id,
    )
    memory_service.retain(
        db,
        chat_id,
        session,
        fields,
    )
    delivery_port.send_reply(
        token,
        chat_id,
        f"↪️ Continued response\n\n{combined}",
        db,
        session_id,
        int(assistant_row[0]),
    )
    recovery.finish(
        db,
        operation_id,
        "continue",
    )

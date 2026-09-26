"""Canonical regeneration owner."""

from __future__ import annotations

import sqlite3
import time

from bridge.delivery_port import DeliveryPort
from bridge.generation import _generation_generate_rendered_reply, build_chat_messages
from bridge.generation_recovery import _generation_operation_recovery
from bridge.light_novel_turn import begin_novel_turn
from bridge.memory_service import MemoryService
from bridge.operations import set_operation_phase
from bridge.persona_service import PersonaService
from bridge.provider_port import ProviderPort
from bridge.rag_service import RagService
from bridge.response_variants import save_response_variant
from bridge.settings import AppSettings
from bridge.sqlite_store import write_transaction


def regenerate_last(
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
    rag_service: RagService,
) -> None:
    session_id = session["session_id"]
    recovery = _generation_operation_recovery(delivery_port)

    def deliver_recovered_regen():
        user_row = recovery.latest_user_row(
            db,
            chat_id,
            session_id,
        )
        assistant_row = recovery.latest_assistant_row(
            db,
            chat_id,
            session_id,
        )
        if not user_row or not assistant_row:
            raise RuntimeError("regen recovery state is incomplete")
        recovery.prepare_delivery(
            db,
            token,
            chat_id,
            assistant_row[0],
            operation_id,
        )
        variant = recovery.selected_variant_index(
            db,
            chat_id,
            session_id,
            user_row[0],
        )
        delivery_port.send_reply(
            token,
            chat_id,
            f"♻️ Regenerated response (variant {variant})\n\n{assistant_row[1]}",
            db,
            session_id,
            int(assistant_row[0]),
        )
        recovery.finish(
            db,
            operation_id,
            "regen",
        )

    if not recovery.begin_or_recover(
        db,
        operation_id,
        "regen",
        deliver_recovered_regen,
    ):
        return

    rows = db.execute(
        "SELECT rowid,role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY created_at,rowid",
        (chat_id, session_id),
    ).fetchall()
    last_user_index = next(
        (i for i in range(len(rows) - 1, -1, -1) if rows[i][1] == "user"),
        None,
    )
    if last_user_index is None:
        delivery_port.send_text(
            token,
            chat_id,
            "Tidak ada pesan user untuk di-regenerate.",
        )
        return

    user_text = rows[last_user_index][2]
    history_rows = [(row[1], row[2]) for row in rows[:last_user_index]]
    rag_bundle = rag_service.bundle(db, chat_id, user_text)
    memory_prompt = memory_service.prompt_context(
        db,
        chat_id,
        session,
        fields,
        user_text,
    )
    messages = build_chat_messages(
        session,
        fields,
        user_text,
        history_rows,
        memory_context=memory_prompt.recall,
        session_summary=memory_prompt.summary,
        persona_service=persona_service,
        rag_context=rag_service.context_for_prompt(db, chat_id, user_text, rag_bundle),
        app_settings=app_settings,
    )
    novel_turn = begin_novel_turn(db, chat_id, session, "regen", operation_id)
    reply = _generation_generate_rendered_reply(
        db,
        token,
        api_key,
        session,
        chat_id,
        messages,
        user_text,
        rag_bundle,
        provider_port=provider_port,
        delivery_port=delivery_port,
        app_settings=app_settings,
        rag_service=rag_service,
        novel_turn=novel_turn,
    )
    last_user_rowid = int(rows[last_user_index][0])
    old_message_ids = recovery.outgoing_ids_after(
        db,
        chat_id,
        session_id,
        last_user_rowid,
    )
    recovery.set_payload(
        db,
        operation_id,
        {
            "old_message_ids": old_message_ids,
            "user_rowid": last_user_rowid,
        },
    )

    def persist_regeneration():
        db.execute(
            "DELETE FROM messages WHERE chat_id=? AND session_id=? AND rowid>?",
            (chat_id, session_id, last_user_rowid),
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
        variant = save_response_variant(db, chat_id, session_id, user_text, reply, user_rowid=last_user_rowid)
        if operation_id is not None:
            set_operation_phase(
                db,
                operation_id,
                "regen",
                "local_committed",
            )

        return assistant_rowid, variant

    with write_transaction(db):
        assistant_rowid, variant = persist_regeneration()
    recovery.delete_stored_telegram_ids(
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
    delivery_port.send_reply(
        token,
        chat_id,
        f"♻️ Regenerated response (variant {variant})\n\n{reply}",
        db,
        session_id,
        assistant_rowid,
    )
    recovery.finish(
        db,
        operation_id,
        "regen",
    )

"""Telegram update routing with explicit runtime dependencies."""
from __future__ import annotations
import logging
import sqlite3
import time
from pathlib import Path
from bridge.catalog import answer_callback
from bridge.common import topic_scope_from_message
from bridge.composition import BridgeServices, RequestContext
from bridge.database import run_write_txn, set_meta
from bridge.help import process_document_job
from bridge.help_details import handle_help_callback, is_help_callback, send_help_command
from bridge.job_service import JobSubmission
from bridge.media import process_voice_job
from bridge.telegram import ensure_session
from bridge.worker_orchestration import process_callback_job, process_edit_job, process_image_job, process_message_job

LONG_RUNNING_COMMANDS=("/retry","/regen","/continue","/edit","/summarize","/providers health","/providers refresh")

def is_long_running_command(text: str) -> bool:
    normalized=str(text).strip().casefold()
    return any(normalized==name or normalized.startswith(name+" ") for name in LONG_RUNNING_COMMANDS)

def complete_update(db: sqlite3.Connection, update_id: int, offset: int) -> None:
    def write():
        db.execute("INSERT OR IGNORE INTO processed_updates(update_id,processed_at) VALUES(?,?)",(update_id,time.time()))
        set_meta(db,"telegram_offset",str(offset))
    run_write_txn(db,write)

def route_update(services: BridgeServices, db: sqlite3.Connection, fields: dict, update: dict, offset: int, permitted: frozenset[str]) -> int:
    token=services.config.bot_token
    model=services.config.default_model
    update_id = int(update["update_id"])
    last_safe_offset = offset
    offset = max(offset, update_id + 1)
    if db.execute("SELECT 1 FROM processed_updates WHERE update_id=?", (update_id,)).fetchone():
        complete_update(db, update_id, offset)
        return offset
    callback = update.get("callback_query")
    if callback:
        sender = str((callback.get("from") or {}).get("id", ""))
        callback_message = callback.get("message") or {}
        callback_real_chat_id = str((callback_message.get("chat") or {}).get("id", ""))
        callback_chat_id = topic_scope_from_message(callback_real_chat_id, callback_message)
        if callback_chat_id:
            callback_message.setdefault("chat", {})["id"] = callback_chat_id
        if sender in permitted and callback_chat_id:
            if is_help_callback(str(callback.get("data") or "")):
                help_session_id = ensure_session(
                    db,
                    callback_chat_id,
                    model,
                )["session_id"]
                help_request_context = RequestContext(
                    db,
                    help_session_id,
                    sender,
                )
                handle_help_callback(
                    db,
                    token,
                    callback,
                    answer_callback,
                    str(callback.get("data") or ""),
                    callback_chat_id,
                    callback_message,
                    {},
                    help_session_id,
                    None,
                    delivery_port=services.delivery,
                    request_context=help_request_context,
                )
            else:
                callback["_queued"] = True
                callback_session = ensure_session(db, callback_chat_id, model)["session_id"]
                callback_message_id = int(callback_message.get("message_id") or 0)
                job_id = services.jobs.enqueue(
                    db,
                    update_id,
                    callback_chat_id,
                    callback_session,
                    callback_message_id,
                    "callback",
                    {
                        "callback": callback,
                        "model": model,
                        "actor_id": sender,
                    },
                )
                services.jobs.submit(
                    db,
                    job_id,
                    JobSubmission(
                        label="callback",
                        chat_id=callback_chat_id,
                        worker=process_callback_job,
                        args=(
                            services,
                            callback_chat_id,
                            callback,
                        ),
                    ),
                )
                answer_callback(token, str(callback.get("id", "")), "Queued")
            complete_update(db, update_id, offset)
        else:
            complete_update(db, update_id, offset)
        return offset
    edited_message = update.get("edited_message")
    if edited_message:
        edited_sender = str((edited_message.get("from") or {}).get("id", ""))
        edited_real_chat_id = str((edited_message.get("chat") or {}).get("id", ""))
        edited_chat_id = topic_scope_from_message(edited_real_chat_id, edited_message)
        edited_text = edited_message.get("text")
        if edited_sender in permitted and edited_chat_id and edited_text:
            edited_message_id = int(edited_message.get("message_id") or 0)
            edited_session_id = ensure_session(db, edited_chat_id, model)["session_id"]
            job_id = services.jobs.enqueue(
                db,
                update_id,
                edited_chat_id,
                edited_session_id,
                edited_message_id,
                "edit",
                {
                    "text": str(edited_text)[:12000],
                    "model": model,
                    "actor_id": edited_sender,
                },
            )
            services.jobs.submit(
                db,
                job_id,
                JobSubmission(
                    label="edit",
                    chat_id=edited_chat_id,
                    worker=process_edit_job,
                    args=(
                        services,
                        edited_chat_id,
                        edited_message_id,
                        str(edited_text)[:12000],
                        None,
                    ),
                ),
            )
            services.telegram.send_text(token, edited_chat_id, "✏️ Edit queued; previous branch will be preserved until regeneration succeeds.")
        complete_update(db, update_id, offset)
        return offset
    message = update.get("message") or {}
    sender = str((message.get("from") or {}).get("id", ""))
    real_chat_id = str((message.get("chat") or {}).get("id", ""))
    chat_id = topic_scope_from_message(real_chat_id, message)
    document = message.get("document")
    photos = message.get("photo") or []
    voice = message.get("voice") or message.get("audio")
    text = message.get("text")
    caption = str(message.get("caption") or "")
    if not chat_id:
        return offset
    if sender not in permitted:
        logging.warning("Rejected Telegram user %s", sender)
        services.telegram.send_text(token, chat_id, "This bot is private.")
        complete_update(db, update_id, offset)
        return offset
    if voice:
        message_id = int(message.get("message_id"))
        queued_session_id = ensure_session(db, chat_id, model)["session_id"]
        job_id = services.jobs.enqueue(
            db,
            update_id,
            chat_id,
            queued_session_id,
            message_id,
            "voice",
            {
                "voice": voice,
                "model": model,
                "resolve_active": True,
                "actor_id": sender,
            },
        )
        queued = services.jobs.submit(
            db,
            job_id,
            JobSubmission(
                label="voice",
                chat_id=chat_id,
                worker=process_voice_job,
                args=(
                    services,
                    fields,
                    chat_id,
                    voice,
                    message_id,
                    None,
                    None,
                ),
            ),
        )
        if queued:
            services.telegram.send_text(token, chat_id, "🎙️ Voice queued for transcription.")
        else:
            services.telegram.send_text(token, chat_id, "🎙️ Voice saved for processing after restart.")
        complete_update(db, update_id, offset)
        return offset
    if photos:
        largest = photos[-1]
        message_id = int(message.get("message_id"))
        queued_session_id = ensure_session(db, chat_id, model)["session_id"]
        job_id = services.jobs.enqueue(
            db,
            update_id,
            chat_id,
            queued_session_id,
            message_id,
            "image",
            {
                "file_id": str(largest.get("file_id", "")),
                "caption": caption,
                "file_size": int(largest.get("file_size") or 0),
                "model": model,
                "resolve_active": True,
                "actor_id": sender,
            },
        )
        queued = services.jobs.submit(
            db,
            job_id,
            JobSubmission(
                label="image",
                chat_id=chat_id,
                worker=process_image_job,
                args=(
                    services,
                    chat_id,
                    str(largest.get("file_id", "")),
                    caption,
                    int(largest.get("file_size") or 0),
                    message_id,
                    None,
                    None,
                ),
            ),
        )
        services.telegram.send_text(token, chat_id, "🖼️ Image queued for analysis." if queued else "🖼️ Image saved for processing after restart.")
        complete_update(db, update_id, offset)
        return offset
    if document and Path(str(document.get("file_name") or "")).suffix.casefold() != ".png" and str(document.get("mime_type") or "").startswith("image/"):
        message_id = int(message.get("message_id"))
        queued_session_id = ensure_session(db, chat_id, model)["session_id"]
        job_id = services.jobs.enqueue(
            db,
            update_id,
            chat_id,
            queued_session_id,
            message_id,
            "image",
            {
                "file_id": str(document.get("file_id", "")),
                "caption": caption,
                "file_size": int(document.get("file_size") or 0),
                "model": model,
                "resolve_active": True,
                "actor_id": sender,
            },
        )
        queued = services.jobs.submit(
            db,
            job_id,
            JobSubmission(
                label="image",
                chat_id=chat_id,
                worker=process_image_job,
                args=(
                    services,
                    chat_id,
                    str(document.get("file_id", "")),
                    caption,
                    int(document.get("file_size") or 0),
                    message_id,
                    None,
                    None,
                ),
            ),
        )
        services.telegram.send_text(token, chat_id, "🖼️ Image queued for analysis." if queued else "🖼️ Image saved for processing after restart.")
        complete_update(db, update_id, offset)
        return offset
    if document:
        message_id = int(message.get("message_id"))
        queued_session_id = ensure_session(db, chat_id, model)["session_id"]
        job_id = services.jobs.enqueue(
            db,
            update_id,
            chat_id,
            queued_session_id,
            message_id,
            "document",
            {
                "document": document,
                "model": model,
                "resolve_active": True,
                "actor_id": sender,
            },
        )
        queued = services.jobs.submit(
            db,
            job_id,
            JobSubmission(
                label="document",
                chat_id=chat_id,
                worker=process_document_job,
                args=(
                    services,
                    chat_id,
                    document,
                    message_id,
                    None,
                ),
            ),
        )
        services.telegram.send_text(token, chat_id, "📄 Document queued for character-card processing or Data Bank indexing." if queued else "📄 Document saved for processing after restart.")
        complete_update(db, update_id, offset)
        return offset
    if not text:
        complete_update(db, update_id, offset)
        return offset
    message_id = int(message.get("message_id"))
    queued_session_id = ensure_session(db, chat_id, model)["session_id"]
    request_context = RequestContext(db, queued_session_id, sender)
    if send_help_command(
        token,
        chat_id,
        str(text),
        delivery_port=services.delivery,
        request_context=request_context,
    ):
        complete_update(db, update_id, offset)
        return offset
    normalized_text = str(text).strip().casefold()
    is_plain_start = normalized_text == "start"
    if not str(text).lstrip().startswith("/") and not is_plain_start and not services.group.user_turn_allowed(db, chat_id, queued_session_id, sender):
        services.telegram.send_text(token, chat_id, "It is not your turn in manual group mode.")
        complete_update(db, update_id, offset)
        return offset
    if is_plain_start or is_long_running_command(str(text)):
        job_id = services.jobs.enqueue(
            db,
            update_id,
            chat_id,
            queued_session_id,
            message_id,
            "command",
            {
                "text": str(text),
                "model": model,
                "resolve_active": True,
                "actor_id": sender,
            },
        )
        queued = services.jobs.submit(
            db,
            job_id,
            JobSubmission(
                label="command",
                chat_id=chat_id,
                worker=process_message_job,
                args=(
                    services,
                    fields,
                    chat_id,
                    str(text),
                    message_id,
                    None,
                    None,
                ),
            ),
        )
        services.telegram.send_text(token, chat_id, "⏳ Command queued." if queued else "⏳ Command saved for execution after restart.")
    elif str(text).lstrip().startswith("/"):
        job_id = services.jobs.enqueue(
            db,
            update_id,
            chat_id,
            queued_session_id,
            message_id,
            "command",
            {
                "text": str(text),
                "model": model,
                "resolve_active": True,
                "actor_id": sender,
            },
        )
        services.jobs.submit(
            db,
            job_id,
            JobSubmission(
                label="command",
                chat_id=chat_id,
                worker=process_message_job,
                args=(
                    services,
                    fields,
                    chat_id,
                    str(text),
                    message_id,
                    None,
                    None,
                ),
            ),
        )
        services.telegram.send_text(token, chat_id, "⏳ Command queued.")
    else:
        job_id = services.jobs.enqueue(
            db,
            update_id,
            chat_id,
            queued_session_id,
            message_id,
            "generation",
            {
                "text": str(text),
                "model": model,
                "resolve_active": True,
                "actor_id": sender,
            },
        )
        queued = services.jobs.submit(
            db,
            job_id,
            JobSubmission(
                label="generation",
                chat_id=chat_id,
                worker=process_message_job,
                args=(
                    services,
                    fields,
                    chat_id,
                    str(text),
                    message_id,
                    None,
                    None,
                ),
            ),
        )
        services.telegram.send_text(token, chat_id, "⏳ Message queued for generation." if queued else "⏳ Message saved for generation after restart.")
    complete_update(db, update_id, offset)
    return offset

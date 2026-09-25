"""Telegram edited-message and ordinary-message ingress routing."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from bridge.composition import BridgeServices
from bridge.document_jobs import process_document_job
from bridge.help_details import send_help_command
from bridge.job_service import JobSubmission
from bridge.request_types import RequestContext
from bridge.topic_scope import topic_scope_from_message
from bridge.transcript_repository import native_edit_target
from bridge.voice_jobs import process_voice_job
from bridge.worker_orchestration import process_edit_job, process_image_job, process_message_job

LONG_RUNNING_COMMANDS = (
    "/retry",
    "/regen",
    "/continue",
    "/edit",
    "/summarize",
)


def is_long_running_command(text: str) -> bool:
    normalized = str(text).strip().casefold()
    return any(normalized == name or normalized.startswith(name + " ") for name in LONG_RUNNING_COMMANDS)


def route_edited_message_update(
    services: BridgeServices,
    db: sqlite3.Connection,
    edited_message: dict,
    update_id: int,
    permitted: frozenset[str],
) -> None:
    token = services.config.bot_token
    model = services.config.default_model
    edited_sender = str((edited_message.get("from") or {}).get("id", ""))
    edited_real_chat_id = str((edited_message.get("chat") or {}).get("id", ""))
    edited_chat_id = topic_scope_from_message(
        edited_real_chat_id,
        edited_message,
    )
    edited_text = edited_message.get("text")

    if edited_sender not in permitted or not edited_chat_id or not edited_text:
        return

    edited_message_id = int(edited_message.get("message_id") or 0)
    target = native_edit_target(db, edited_chat_id, edited_message_id)
    # Native Telegram edits target their original message, which may belong to
    # an inactive bridge session. Enforce the policy for that exact session.
    edited_session_id = (
        str(target[1])
        if target is not None and target[2] == "user"
        else services.session.ensure(db, edited_chat_id, model)["session_id"]
    )
    if not services.group.user_turn_allowed(db, edited_chat_id, edited_session_id, edited_sender):
        services.telegram.send_text(token, edited_chat_id, "It is not your turn in manual group mode.")
        return
    truncated_text = str(edited_text)[:12000]
    job_id = services.jobs.enqueue(
        db,
        update_id,
        edited_chat_id,
        edited_session_id,
        edited_message_id,
        "edit",
        {
            "text": truncated_text,
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
                truncated_text,
                None,
            ),
        ),
    )
    services.telegram.send_text(
        token,
        edited_chat_id,
        "✏️ Edit queued; previous branch will be preserved until regeneration succeeds.",
    )


def route_message_update(
    services: BridgeServices,
    db: sqlite3.Connection,
    fields: dict,
    message: dict,
    update_id: int,
    permitted: frozenset[str],
) -> bool:
    token = services.config.bot_token
    model = services.config.default_model
    sender = str((message.get("from") or {}).get("id", ""))
    real_chat_id = str((message.get("chat") or {}).get("id", ""))
    chat_id = topic_scope_from_message(real_chat_id, message)
    document = message.get("document")
    photos = message.get("photo") or []
    voice = message.get("voice") or message.get("audio")
    text = message.get("text")
    caption = str(message.get("caption") or "")

    if not chat_id:
        return False

    if sender not in permitted:
        logging.warning("Rejected Telegram user %s", sender)
        services.telegram.send_text(
            token,
            chat_id,
            "This bot is private.",
        )
        return True

    if voice:
        message_id = int(message.get("message_id"))
        queued_session_id = services.session.ensure(db, chat_id, model)["session_id"]
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
        services.telegram.send_text(
            token,
            chat_id,
            ("🎙️ Voice queued for transcription." if queued else "🎙️ Voice saved for processing after restart."),
        )
        return True

    if photos:
        largest = photos[-1]
        message_id = int(message.get("message_id"))
        queued_session_id = services.session.ensure(db, chat_id, model)["session_id"]
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
        services.telegram.send_text(
            token,
            chat_id,
            ("🖼️ Image queued for analysis." if queued else "🖼️ Image saved for processing after restart."),
        )
        return True

    if (
        document
        and Path(str(document.get("file_name") or "")).suffix.casefold() != ".png"
        and str(document.get("mime_type") or "").startswith("image/")
    ):
        message_id = int(message.get("message_id"))
        queued_session_id = services.session.ensure(db, chat_id, model)["session_id"]
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
        services.telegram.send_text(
            token,
            chat_id,
            ("🖼️ Image queued for analysis." if queued else "🖼️ Image saved for processing after restart."),
        )
        return True

    if document:
        message_id = int(message.get("message_id"))
        queued_session_id = services.session.ensure(db, chat_id, model)["session_id"]
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
        services.telegram.send_text(
            token,
            chat_id,
            (
                "📄 Document queued for character-card processing or Data Bank indexing."
                if queued
                else "📄 Document saved for processing after restart."
            ),
        )
        return True

    if not text:
        return True

    message_id = int(message.get("message_id"))
    queued_session_id = services.session.ensure(db, chat_id, model)["session_id"]
    request_context = RequestContext(db, queued_session_id, sender, app_settings=services.config)
    if send_help_command(
        token,
        chat_id,
        str(text),
        delivery_port=services.delivery,
        request_context=request_context,
    ):
        return True

    normalized_text = str(text).strip().casefold()
    is_plain_start = normalized_text == "start"

    if (
        not str(text).lstrip().startswith("/")
        and not is_plain_start
        and not services.group.user_turn_allowed(
            db,
            chat_id,
            queued_session_id,
            sender,
        )
    ):
        services.telegram.send_text(
            token,
            chat_id,
            "It is not your turn in manual group mode.",
        )
        return True

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
        services.telegram.send_text(
            token,
            chat_id,
            ("⏳ Command queued." if queued else "⏳ Command saved for execution after restart."),
        )
        return True

    if str(text).lstrip().startswith("/"):
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
        services.telegram.send_text(
            token,
            chat_id,
            "⏳ Command queued.",
        )
        return True

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
    services.telegram.send_text(
        token,
        chat_id,
        ("⏳ Message queued for generation." if queued else "⏳ Message saved for generation after restart."),
    )
    return True

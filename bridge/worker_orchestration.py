"""Durable worker execution and recovery orchestration."""

from __future__ import annotations

import json
import logging
import sqlite3

from bridge.background import chat_job_lock
from bridge.callback_dispatch import process_callback
from bridge.card_content import card_fields_from_file
from bridge.commands import edit_telegram_user_message, process_image_message
from bridge.composition import BridgeServices
from bridge.database import (
    clear_failed_turn,
    committed_assistant_for_message,
    native_edit_target,
    operation_phase,
    operation_was_applied,
    record_failed_turn,
    record_operation,
)
from bridge.help import process_document_job
from bridge.job_service import DurableJob, JobSubmission
from bridge.limits import IMAGE_MAX_BYTES
from bridge.media import process_voice_job, send_reply
from bridge.sqlite_store import run_write_txn


def process_message_job(
    services: BridgeServices,
    fields: dict,
    chat_id: str,
    text: str,
    message_id: int,
    queued_session_id: str | None = None,
    model_override: str | None = None,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    api_key = services.config.api_key
    model = model_override or services.config.default_model
    jobs = services.jobs
    with chat_job_lock(chat_id):
        db = services.db_factory()
        try:
            if job_id is not None and not jobs.start(db, job_id):
                return
            actor_id = jobs.actor_id(db, job_id)
            existing = committed_assistant_for_message(db, chat_id, message_id)
            if existing:
                recovery_session = (
                    services.session.load(db, chat_id, queued_session_id, model)
                    if queued_session_id
                    else services.session.ensure(db, chat_id, model)
                )
                if services.group.current_speaker(db, chat_id, recovery_session, text):
                    services.group.advance_turn(
                        db,
                        chat_id,
                        recovery_session["session_id"],
                        job_id,
                    )
                if json.loads(existing[2] or "[]"):
                    clear_failed_turn(db, chat_id, message_id)
                    if job_id is not None:
                        jobs.complete(db, job_id)
                    return
                delivery_session_id = queued_session_id or services.session.ensure(db, chat_id, model)["session_id"]
                send_reply(
                    token,
                    chat_id,
                    str(existing[1]),
                    db,
                    delivery_session_id,
                    int(existing[0]),
                    app_settings=services.config,
                )
                clear_failed_turn(db, chat_id, message_id)
                if job_id is not None:
                    jobs.complete(db, job_id)
                return
            services.conversation.process_message(
                db,
                token,
                api_key,
                model,
                fields,
                chat_id,
                text,
                message_id,
                queued_session_id=queued_session_id,
                operation_id=job_id,
                actor_id=actor_id,
            )
            if job_id is not None:
                jobs.complete(db, job_id)
        except Exception as exc:
            logging.error("Background message processing failed: %s", exc, exc_info=True)
            record_failed_turn(db, chat_id, message_id, text, model, str(exc), queued_session_id or "")
            if job_id is not None:
                jobs.fail(db, job_id, exc)
            if str(text).lstrip().startswith("/"):
                if "Telegram sendMessage failed" in str(exc):
                    failure_message = (
                        "Telegram could not deliver this command. The character backend was not "
                        "called; retry the command."
                    )
                else:
                    failure_message = "The command failed. Use /status for details, then retry the command."
            else:
                failure_message = "The character backend failed for this message. Use /retry or /status."
            services.telegram.send_text(token, chat_id, failure_message)
        finally:
            db.close()


def process_image_job(
    services: BridgeServices,
    chat_id: str,
    file_id: str,
    caption: str,
    file_size: int,
    message_id: int,
    queued_session_id: str | None = None,
    model_override: str | None = None,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    model = model_override or services.config.default_model
    jobs = services.jobs
    with chat_job_lock(chat_id):
        db = services.db_factory()
        try:
            if job_id is not None and not jobs.start(db, job_id):
                return
            existing = committed_assistant_for_message(db, chat_id, message_id)
            if existing:
                if json.loads(existing[2] or "[]"):
                    clear_failed_turn(db, chat_id, message_id)
                    if job_id is not None:
                        jobs.complete(db, job_id)
                    return
                delivery_session_id = queued_session_id or services.session.ensure(db, chat_id, model)["session_id"]
                send_reply(
                    token,
                    chat_id,
                    str(existing[1]),
                    db,
                    delivery_session_id,
                    int(existing[0]),
                    app_settings=services.config,
                )
                clear_failed_turn(db, chat_id, message_id)
                if job_id is not None:
                    jobs.complete(db, job_id)
                return
            if file_size > IMAGE_MAX_BYTES:
                services.telegram.send_text(
                    token,
                    chat_id,
                    "Image is too large. The limit is 8 MB.",
                )
                if job_id is not None:
                    jobs.complete(db, job_id)
                return
            image_bytes = services.telegram.download_file(
                token,
                file_id,
                IMAGE_MAX_BYTES,
            )
            session = (
                services.session.load(db, chat_id, queued_session_id, model)
                if queued_session_id
                else services.session.ensure(db, chat_id, model)
            )
            image_fields = card_fields_from_file(session["character_file"], app_settings=services.config)
            process_image_message(
                db,
                token,
                services.config.api_key,
                session,
                image_fields,
                chat_id,
                caption,
                image_bytes,
                telegram_message_id=message_id,
                group_service=services.group,
                provider_port=services.provider,
                memory_service=services.memory,
                persona_service=services.persona,
                group_director_service=services.group_director,
                app_settings=services.config,
            )
            if job_id is not None:
                jobs.complete(db, job_id)
        except Exception as exc:
            logging.error("Background image processing failed: %s", exc, exc_info=True)
            if job_id is not None:
                jobs.fail(db, job_id, exc)
            services.telegram.send_text(
                token, chat_id, "Image processing failed. The selected model may not support vision."
            )
        finally:
            db.close()


def process_callback_job(
    services: BridgeServices,
    chat_id: str,
    callback: dict,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    jobs = services.jobs
    with chat_job_lock(chat_id):
        db = services.db_factory()
        try:
            if job_id is not None and not jobs.start(db, job_id):
                return
            actor_id = jobs.actor_id(db, job_id) if job_id is not None else ""
            if job_id is not None and operation_was_applied(db, job_id):
                jobs.complete(db, job_id)
                return
            process_callback(
                db,
                token,
                callback,
                operation_id=job_id,
                actor_id=actor_id,
                services=services,
            )
            if job_id is not None:

                def write_callback_operation():
                    record_operation(db, job_id, "callback")
                    db.commit()

                run_write_txn(db, write_callback_operation)
                jobs.complete(db, job_id)
        except Exception as exc:
            logging.error("Background callback processing failed: %s", exc, exc_info=True)
            if job_id is not None:
                jobs.fail(db, job_id, exc)
            services.telegram.send_text(token, chat_id, "Callback processing failed; try the command again.")
        finally:
            db.close()


def native_edit_committed_after_failure(db: sqlite3.Connection, job_id: int | None, exc: BaseException) -> bool:
    if job_id is None or "Telegram sendMessage failed" in str(exc):
        return False
    try:
        return operation_phase(db, job_id) == "local_committed"
    except sqlite3.OperationalError:
        logging.warning("Could not inspect native edit operation %s after failure", job_id, exc_info=True)
        return False


def process_edit_job(
    services: BridgeServices,
    chat_id: str,
    message_id: int,
    text: str,
    model_override: str | None = None,
    job_id: int | None = None,
) -> None:
    token = services.config.bot_token
    api_key = services.config.api_key
    model = model_override or services.config.default_model
    jobs = services.jobs
    with chat_job_lock(chat_id):
        db = services.db_factory()
        try:
            if job_id is not None and not jobs.start(db, job_id):
                return
            target = native_edit_target(db, chat_id, message_id)
            if target is not None and target[2] == "user":
                # Ownership can change after enqueue or during a restart.
                # Read the durable actor again, including recovered edit jobs.
                actor_id = jobs.actor_id(db, job_id)
                if not services.group.user_turn_allowed(db, chat_id, str(target[1]), actor_id):
                    if job_id is not None:
                        jobs.complete(db, job_id)
                    services.telegram.send_text(token, chat_id, "It is not your turn in manual group mode.")
                    return
            edit_telegram_user_message(
                db,
                token,
                api_key,
                chat_id,
                message_id,
                text,
                model,
                operation_id=job_id,
                provider_port=services.provider,
                memory_service=services.memory,
                persona_service=services.persona,
                app_settings=services.config,
            )
            if job_id is not None:
                jobs.complete(db, job_id)
        except Exception as exc:
            logging.error("Background native edit failed: %s", exc, exc_info=True)
            if native_edit_committed_after_failure(db, job_id, exc):
                logging.warning("Native edit %s committed locally; suppressing rollback fallback", job_id)
                if job_id is not None:
                    jobs.complete(db, job_id)
                return
            if job_id is not None:
                jobs.fail(db, job_id, exc)
            services.telegram.send_text(
                token, chat_id, "Native message edit failed; the previous branch was preserved."
            )
        finally:
            db.close()


def resolve_recovered_job_submission(
    services: BridgeServices,
    fields: dict,
    job: DurableJob,
) -> JobSubmission | None:
    payload = job.payload
    model_override = str(payload.get("model") or services.config.default_model)
    session_for_job = None if payload.get("resolve_active") else job.session_id

    if job.kind in {"generation", "command"}:
        return JobSubmission(
            label=job.kind,
            chat_id=job.chat_id,
            worker=process_message_job,
            args=(
                services,
                fields,
                job.chat_id,
                str(payload["text"]),
                job.telegram_message_id,
                session_for_job,
                model_override,
            ),
        )
    if job.kind == "callback":
        return JobSubmission(
            label=job.kind,
            chat_id=job.chat_id,
            worker=process_callback_job,
            args=(
                services,
                job.chat_id,
                payload["callback"],
            ),
        )
    if job.kind == "edit":
        return JobSubmission(
            label=job.kind,
            chat_id=job.chat_id,
            worker=process_edit_job,
            args=(
                services,
                job.chat_id,
                job.telegram_message_id,
                str(payload["text"]),
                model_override,
            ),
        )
    if job.kind == "voice":
        return JobSubmission(
            label=job.kind,
            chat_id=job.chat_id,
            worker=process_voice_job,
            args=(
                services,
                fields,
                job.chat_id,
                payload["voice"],
                job.telegram_message_id,
                session_for_job,
                model_override,
            ),
        )
    if job.kind == "image":
        return JobSubmission(
            label=job.kind,
            chat_id=job.chat_id,
            worker=process_image_job,
            args=(
                services,
                job.chat_id,
                str(payload["file_id"]),
                str(payload.get("caption") or ""),
                int(payload.get("file_size") or 0),
                job.telegram_message_id,
                session_for_job,
                model_override,
            ),
        )
    if job.kind == "document":
        return JobSubmission(
            label=job.kind,
            chat_id=job.chat_id,
            worker=process_document_job,
            args=(
                services,
                job.chat_id,
                payload["document"],
                job.telegram_message_id,
                model_override,
            ),
        )
    return None


def make_durable_backlog_dispatcher(
    services: BridgeServices,
    fields: dict,
):
    def dispatch() -> None:
        db = services.db_factory()
        try:
            services.jobs.recover(
                db,
                lambda job: resolve_recovered_job_submission(
                    services,
                    fields,
                    job,
                ),
                recover_running=False,
            )
        finally:
            db.close()

    return dispatch

"""Canonical document jobs owner."""

from __future__ import annotations

import logging
from functools import partial

from bridge.background import chat_job_lock
from bridge.composition import BridgeServices as _BridgeServices
from bridge.image_messages import process_image_message
from bridge.native_imports import import_telegram_document
from bridge.request_types import RequestContext


def process_document_job(
    services: _BridgeServices,
    chat_id: str,
    document: dict,
    message_id: int | None = None,
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
            if job_id is not None and not queued_session_id:
                raise ValueError("durable document job requires its queued session")
            session = (
                services.session.load(db, chat_id, queued_session_id, model)
                if queued_session_id
                else services.session.ensure(db, chat_id, model)
            )
            request_context = RequestContext(
                db,
                session["session_id"],
                jobs.actor_id(db, job_id),
                app_settings=services.config,
            )
            import_telegram_document(
                db,
                token,
                chat_id,
                document,
                model,
                telegram_message_id=message_id,
                api_key=services.config.api_key,
                process_image=partial(
                    process_image_message,
                    provider_port=services.provider,
                    group_service=services.group,
                    app_settings=services.config,
                    rag_service=services.rag,
                ),
                memory_service=services.memory,
                persona_service=services.persona,
                group_director_service=services.group_director,
                app_settings=services.config,
                rag_service=services.rag,
                provider_port=services.provider,
                request_context=request_context,
            )
            if job_id is not None:
                jobs.complete(db, job_id)
        except Exception as exc:
            logging.error("Document job failed: %s", exc, exc_info=True)
            if job_id is not None:
                jobs.fail(db, job_id, exc)
            services.telegram.send_text(
                token, chat_id, "Document import failed. Check the file format and size limits."
            )
        finally:
            db.close()

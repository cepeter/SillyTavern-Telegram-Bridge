"""Durable choice-only workers; narrative generation stays in the existing worker."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bridge.background import chat_job_lock
from bridge.card_content import card_fields_from_file
from bridge.light_novel_panels import render_choices
from bridge.light_novel_repository import load_choice_set
from bridge.light_novel_service import current_choice_story, ensure_choices

if TYPE_CHECKING:
    from bridge.composition import BridgeServices


def process_light_novel_choices_job(
    services: BridgeServices,
    chat_id: str,
    nonce: str,
    retry: bool = False,
    job_id: int | None = None,
) -> None:
    with chat_job_lock(chat_id):
        db = services.db_factory()
        try:
            if job_id is not None and not services.jobs.start(db, job_id):
                return
            record = load_choice_set(db, nonce)
            if record is None or record.chat_id != chat_id or current_choice_story(db, record) is None:
                if job_id is not None:
                    services.jobs.complete(db, job_id)
                return
            session = services.session.load(db, chat_id, record.session_id, services.config.default_model)
            # Repair an interrupted greeting/narrative delivery without re-generating it.
            row = db.execute(
                "SELECT content,telegram_message_ids FROM messages WHERE rowid=? AND chat_id=? AND session_id=?",
                (record.assistant_rowid, chat_id, record.session_id),
            ).fetchone()
            if row and str(row[1] or "[]") == "[]":
                services.delivery.send_reply(
                    services.config.bot_token, chat_id, str(row[0]), db, record.session_id, record.assistant_rowid
                )
            fields = card_fields_from_file(session["character_file"], app_settings=services.config)
            record = ensure_choices(
                db, nonce, session, fields, provider_port=services.provider, app_settings=services.config, retry=retry
            )
            render_choices(db, services.config.bot_token, record, app_settings=services.config)
            if job_id is not None:
                services.jobs.complete(db, job_id)
        except Exception as exc:
            # The story is already committed. Do not make this a failed narrative turn.
            logging.warning("Light Novel panel/choice work interrupted; use /lightnovel to restore choices")
            if job_id is not None:
                services.jobs.fail(db, job_id, type(exc).__name__)
        finally:
            db.close()

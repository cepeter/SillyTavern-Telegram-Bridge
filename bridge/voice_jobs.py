"""Canonical voice jobs owner."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bridge.composition import BridgeServices as _BridgeServices

import json
import logging
import sqlite3
from pathlib import Path

from bridge.background import chat_job_lock
from bridge.conversation_jobs import narrative_job_is_current
from bridge.conversation_lifecycle import START_REQUIRED, require_started
from bridge.config import STT_DEFAULT_MODEL
from bridge.failed_turns import clear_failed_turn
from bridge.limits import STT_MAX_BYTES
from bridge.metadata import get_meta
from bridge.response_delivery import send_reply
from bridge.session_core import ensure_session
from bridge.speech import transcribe_audio_bytes
from bridge.telegram import download_telegram_file, send_text
from bridge.transcript_repository import committed_assistant_for_message


def process_voice_message(
    db: sqlite3.Connection,
    token: str,
    api_key: str,
    model: str,
    fields: dict,
    chat_id: str,
    voice: dict,
    message_id: int,
    queued_session_id: str | None = None,
    *,
    actor_id: str = "",
    services: _BridgeServices,
) -> None:
    session_id = queued_session_id or ensure_session(db, chat_id, model, app_settings=services.config)["session_id"]
    if not require_started(db, chat_id, session_id):
        send_text(token, chat_id, START_REQUIRED)
        return
    if get_meta(db, f"stt_mode:{chat_id}", "on") != "on":
        send_text(token, chat_id, "Voice input is disabled. Use /voice_input on to enable it.")
        return
    file_size = int(voice.get("file_size") or 0)
    if file_size > STT_MAX_BYTES:
        send_text(token, chat_id, "Voice message is too large. The limit is 20 MB.")
        return
    raw = download_telegram_file(token, str(voice.get("file_id", "")), STT_MAX_BYTES)
    suffix = Path(str(voice.get("file_name") or ".ogg")).suffix or ".ogg"
    language = get_meta(db, f"stt_language:{chat_id}", "auto")
    stt_model = get_meta(db, f"stt_model:{chat_id}", STT_DEFAULT_MODEL)
    transcript = transcribe_audio_bytes(raw, suffix, stt_model, language, app_settings=services.config)
    services.conversation.process_message(
        db,
        token,
        api_key,
        model,
        fields,
        chat_id,
        transcript,
        message_id,
        queued_session_id=queued_session_id,
        actor_id=actor_id,
    )


def process_voice_job(
    services: _BridgeServices,
    fields: dict,
    chat_id: str,
    voice: dict,
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
            if not narrative_job_is_current(db, job_id):
                if job_id is not None:
                    jobs.complete(db, job_id)
                return
            actor_id = jobs.actor_id(db, job_id)
            existing = committed_assistant_for_message(db, chat_id, message_id)
            if existing:
                if json.loads(existing[2] or "[]"):
                    clear_failed_turn(db, chat_id, message_id)
                    if job_id is not None:
                        jobs.complete(db, job_id)
                    return
                delivery_session_id = (
                    queued_session_id or ensure_session(db, chat_id, model, app_settings=services.config)["session_id"]
                )
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
            process_voice_message(
                db,
                token,
                api_key,
                model,
                fields,
                chat_id,
                voice,
                message_id,
                queued_session_id=queued_session_id,
                actor_id=actor_id,
                services=services,
            )
            if job_id is not None:
                jobs.complete(db, job_id)
        except Exception as exc:
            logging.error("Voice job failed: %s", exc, exc_info=True)
            if job_id is not None:
                jobs.fail(db, job_id, exc)
            services.telegram.send_text(
                token, chat_id, "Voice processing failed. Use /voice_input status to check transcription settings."
            )
        finally:
            db.close()

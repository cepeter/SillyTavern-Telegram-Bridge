"""Telegram callback-query ingress routing."""
from __future__ import annotations

import sqlite3

from bridge.catalog import answer_callback
from bridge.common import topic_scope_from_message
from bridge.composition import BridgeServices, RequestContext
from bridge.help_details import handle_help_callback, is_help_callback
from bridge.job_service import JobSubmission
from bridge.telegram import ensure_session
from bridge.worker_orchestration import process_callback_job


def route_callback_update(
    services: BridgeServices,
    db: sqlite3.Connection,
    callback: dict,
    update_id: int,
    permitted: frozenset[str],
) -> None:
    token = services.config.bot_token
    model = services.config.default_model
    sender = str((callback.get("from") or {}).get("id", ""))
    callback_message = callback.get("message") or {}
    callback_real_chat_id = str(
        (callback_message.get("chat") or {}).get("id", "")
    )
    callback_chat_id = topic_scope_from_message(
        callback_real_chat_id,
        callback_message,
    )
    if callback_chat_id:
        callback_message.setdefault("chat", {})["id"] = callback_chat_id

    if sender not in permitted or not callback_chat_id:
        return

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
        return

    callback["_queued"] = True
    callback_session = ensure_session(
        db,
        callback_chat_id,
        model,
    )["session_id"]
    callback_message_id = int(
        callback_message.get("message_id") or 0
    )
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
    answer_callback(
        token,
        str(callback.get("id", "")),
        "Queued",
    )

"""Choice ingress authorizes durable handles and commits a normal user-job handoff."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from bridge.conversation_lifecycle import conversation_state
from bridge.job_service import JobSubmission
from bridge.light_novel_contracts import LightNovelRuntime
from bridge.light_novel_format import validate_choices
from bridge.light_novel_jobs import process_light_novel_choices_job
from bridge.light_novel_repository import consume_choice_set, load_choice_set, set_choice_job
from bridge.light_novel_service import current_choice_story
from bridge.metadata import get_meta
from bridge.sqlite_store import write_transaction

NEXT_SCENE_INSTRUCTION = (
    "Advance to the next scene without speaking, deciding, or acting for the user character. "
    "Continue the narrative until the user character can meaningfully participate again."
)
NEXT_SCENE_LABEL = "⏭ Next Scene"


def route_light_novel_callback(
    services: LightNovelRuntime,
    db: sqlite3.Connection,
    callback: dict,
    update_id: int,
    chat_id: str,
    actor_id: str,
    *,
    message_worker: Callable,
) -> bool:
    data = str(callback.get("data") or "")
    if not data.startswith(("lnchoice:", "lnretry:", "lnnext:")):
        return False
    message_id = int((callback.get("message") or {}).get("message_id") or 0)
    token = services.config.bot_token
    feedback = "Choice expired"
    selection = None
    selection_label = None
    submission = None
    job_id = None
    try:
        parts = data.split(":")
        action = parts[0]
        expected_parts = 3 if action == "lnchoice" else 2
        if action not in {"lnchoice", "lnretry", "lnnext"} or len(parts) != expected_parts or len(data.encode()) > 64:
            raise ValueError("Choice expired")
        with write_transaction(db):
            record = load_choice_set(db, parts[1])
            if record is None or (record.chat_id, record.actor_id, record.panel_message_id) != (
                chat_id,
                actor_id,
                message_id,
            ):
                raise ValueError("Choice expired")
            if get_meta(db, f"active_session:{chat_id}", "default") != record.session_id:
                raise ValueError("Choice expired")
            state = conversation_state(db, chat_id, record.session_id)
            if record.state == "consumed":
                raise ValueError("Choice already used")
            if current_choice_story(db, record) is None or state.epoch != record.epoch:
                raise ValueError("Choice expired")
            if action in {"lnchoice", "lnnext"}:
                if action == "lnchoice":
                    choices = validate_choices(list(record.choices), record.requested_count)
                    index = int(parts[2])
                    consume_choice_set(
                        db, record.nonce, index, chat_id, record.session_id, actor_id, state.epoch, message_id
                    )
                    selection = choices[index]
                    selection_label = selection
                else:
                    consume_choice_set(
                        db, record.nonce, None, chat_id, record.session_id, actor_id, state.epoch, message_id
                    )
                    selection = NEXT_SCENE_INSTRUCTION
                    selection_label = NEXT_SCENE_LABEL
                payload = {
                    "text": selection,
                    "model": record.model_id,
                    "actor_id": actor_id,
                    "resolve_active": False,
                    "epoch": state.epoch,
                    "choice_nonce": record.nonce,
                }
                job_id = services.jobs.enqueue(
                    db, update_id, chat_id, record.session_id, -record.id, "generation", payload
                )
                set_choice_job(db, record.nonce, job_id)
                submission = JobSubmission(
                    "generation",
                    chat_id,
                    message_worker,
                    (services, {}, chat_id, selection, -record.id, None, record.session_id, record.model_id),
                )
                feedback = "Choice selected"
            else:
                if record.generation_status == "ready":
                    raise ValueError("Choices are already available; use /lightnovel to show them.")
                existing = db.execute(
                    "SELECT job_id FROM jobs WHERE chat_id=? AND session_id=? AND kind='novel_choices' "
                    "AND state IN ('queued','scheduled','running') AND json_extract(payload_json,'$.nonce')=? LIMIT 1",
                    (chat_id, record.session_id, record.nonce),
                ).fetchone()
                if existing:
                    feedback = "Choices are already queued"
                else:
                    payload = {
                        "nonce": record.nonce,
                        "actor_id": actor_id,
                        "retry": True,
                        "epoch": state.epoch,
                        "resolve_active": False,
                    }
                    job_id = services.jobs.enqueue(
                        db, update_id, chat_id, record.session_id, 0, "novel_choices", payload
                    )
                    submission = JobSubmission(
                        "utility", chat_id, process_light_novel_choices_job, (services, chat_id, record.nonce, True)
                    )
                    feedback = "Choices queued"
        if submission is not None and job_id is not None:
            # Admission failure leaves a committed recoverable job, never a reusable choice.
            services.jobs.submit(db, job_id, submission)
        if selection is not None:
            try:
                services.telegram.request(
                    token,
                    "editMessageText",
                    {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": "Selected: " + str(selection_label or selection),
                        "reply_markup": {"inline_keyboard": []},
                    },
                )
            except Exception:
                logging.info("Choice accepted; old panel cleanup unavailable")
    except (ValueError, TypeError, IndexError) as exc:
        feedback = str(exc) if isinstance(exc, ValueError) else "Choice expired"
    finally:
        try:
            services.telegram.request(
                token,
                "answerCallbackQuery",
                {"callback_query_id": str(callback.get("id") or ""), "text": feedback[:200]},
            )
        except Exception:
            logging.info("Choice callback acknowledgement unavailable")
    return True

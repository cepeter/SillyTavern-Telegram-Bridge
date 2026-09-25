"""Canonical response delivery owner."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from functools import partial as _partial

from bridge.background import submit_background
from bridge.expressions import deliver_expression
from bridge.metadata import get_meta
from bridge.settings import AppSettings
from bridge.speech import send_tts
from bridge.sqlite_store import write_transaction
from bridge.telegram import send_text, telegram_request


def delete_outgoing_messages(
    db: sqlite3.Connection, token: str, chat_id: str, session_id: str, after_rowid: int | None = None
) -> None:
    query = "SELECT telegram_message_ids FROM messages WHERE chat_id=? AND session_id=? AND role='assistant'"
    params = [chat_id, session_id]
    if after_rowid is not None:
        query += " AND rowid>?"
        params.append(after_rowid)
    for (raw_ids,) in db.execute(query, params).fetchall():
        try:
            message_ids = json.loads(raw_ids or "[]")
        except json.JSONDecodeError:
            message_ids = []
        for message_id in message_ids:
            try:
                telegram_request(token, "deleteMessage", {"chat_id": chat_id, "message_id": int(message_id)})
            except Exception:
                logging.info("Could not delete outgoing Telegram message %s", message_id, exc_info=True)


def delete_outgoing_message_row(db: sqlite3.Connection, token: str, chat_id: str, rowid: int) -> None:
    row = db.execute(
        "SELECT telegram_message_ids FROM messages WHERE rowid=? AND chat_id=? AND role='assistant'", (rowid, chat_id)
    ).fetchone()
    if not row:
        return
    try:
        message_ids = json.loads(row[0] or "[]")
    except json.JSONDecodeError:
        message_ids = []
    for message_id in message_ids:
        try:
            telegram_request(token, "deleteMessage", {"chat_id": chat_id, "message_id": int(message_id)})
        except Exception:
            logging.info("Could not delete outgoing Telegram message %s", message_id, exc_info=True)
    db.execute("UPDATE messages SET telegram_message_ids='[]' WHERE rowid=?", (rowid,))
    db.commit()


def quoted_speech_from_reply(text: str) -> str:
    """Return only dialogue enclosed in straight double quotes for TTS."""
    quoted = re.findall(r'"([^"\n]{1,4000})"', str(text or ""), flags=re.DOTALL)
    return re.sub(r"\s+", " ", " ".join(quoted)).strip()


def queue_user_quote_tts(
    token: str,
    chat_id: str,
    text: str,
    db: sqlite3.Connection,
    session_id: str,
    message_id: int | None = None,
    *,
    app_settings: AppSettings,
) -> bool:
    """Queue quoted user dialogue for TTS without changing the transcript."""
    if get_meta(db, f"voice_mode:{chat_id}", "off") != "tts":
        return False
    speech = quoted_speech_from_reply(text)
    if not speech:
        return False
    stable_id = (
        str(message_id)
        if message_id is not None
        else hashlib.sha256(f"{session_id}\0{text}".encode("utf-8")).hexdigest()[:24]
    )
    operation_id = f"user-tts:{chat_id}:{session_id}:{stable_id}"
    if not submit_background(
        "tts", _partial(send_tts, app_settings=app_settings), token, chat_id, speech, operation_id
    ):
        logging.warning("Automatic user-quote TTS dropped for chat %s", chat_id)
        return False
    return True


def persist_assistant_delivery_ids(db: sqlite3.Connection, assistant_rowid: int, message_ids: list[int]) -> bool:
    """Persist Telegram delivery metadata without misreporting a sent reply as generation failure."""
    nested = db.in_transaction
    try:

        def write():
            db.execute(
                "UPDATE messages SET telegram_message_ids=? WHERE rowid=?", (json.dumps(message_ids), assistant_rowid)
            )

            return True

        with write_transaction(db):
            return write()
    except sqlite3.OperationalError as exc:
        if nested or ("locked" not in str(exc).casefold() and "busy" not in str(exc).casefold()):
            raise

        logging.warning("Reply delivered but Telegram message IDs could not be recorded: %s", exc)
        return False


def send_reply(
    token: str,
    chat_id: str,
    text: str,
    db: sqlite3.Connection | None = None,
    session_id: str | None = None,
    assistant_rowid: int | None = None,
    *,
    app_settings: AppSettings,
) -> None:
    if db is not None and session_id:
        deliver_expression(token, chat_id, text, db, session_id, app_settings=app_settings)
    message_ids = send_text(token, chat_id, text)
    if db is not None and assistant_rowid is not None:
        persist_assistant_delivery_ids(db, assistant_rowid, message_ids)
    if db is not None and session_id and get_meta(db, f"voice_mode:{chat_id}", "off") == "tts":
        speech = quoted_speech_from_reply(text)
        if speech:
            operation_id = None
            if assistant_rowid is not None:
                speech_hash = hashlib.sha256(speech.encode("utf-8")).hexdigest()[:16]
                operation_id = f"assistant-tts:{chat_id}:{session_id}:{assistant_rowid}:{speech_hash}"
            if not submit_background(
                "tts", _partial(send_tts, app_settings=app_settings), token, chat_id, speech, operation_id
            ):
                logging.warning("Automatic TTS dropped for chat %s", chat_id)

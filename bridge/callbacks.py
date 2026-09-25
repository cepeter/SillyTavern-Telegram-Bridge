from __future__ import annotations

import logging
import sqlite3

from bridge.telegram import telegram_request


def close_panel_message(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    callback: dict,
) -> None:
    message = callback.get("message") or callback
    message_id = message.get("message_id")
    try:
        telegram_request(
            token,
            "deleteMessage",
            {"chat_id": chat_id, "message_id": message_id},
        )
    except Exception:
        logging.info(
            "Panel delete failed; replacing it with a closed marker",
            exc_info=True,
        )
        try:
            telegram_request(
                token,
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": "Panel closed.",
                    "reply_markup": {"inline_keyboard": []},
                },
            )
        except Exception:
            logging.warning("Panel close fallback failed", exc_info=True)
    try:
        db.execute(
            "DELETE FROM panel_sessions WHERE chat_id=? AND message_id=?",
            (str(chat_id), str(message_id)),
        )
        db.commit()
    except Exception:
        logging.debug("Could not remove closed panel binding", exc_info=True)


def discard_panel_binding(
    db: sqlite3.Connection,
    chat_id: str,
    message_id: int | str | None,
) -> None:
    if message_id is None:
        return
    db.execute(
        "DELETE FROM panel_sessions WHERE chat_id=? AND message_id=?",
        (str(chat_id), str(message_id)),
    )
    db.commit()


def is_session_scoped_panel_callback(data: str) -> bool:
    return data.startswith(
        (
            "character",
            "persona",
            "session",
            "world",
            "systemprompt",
            "language",
            "note",
            "reset",
            "sync",
            "greeting:",
            "status:",
            "prompt:",
            "scene:",
            "goal:",
            "curated:",
            "summary:",
            "swipe:",
            "expression:",
            "update:",
            "models",
            "provider",
            "model",
            "group",
            "groupchars",
            "groupmode",
            "enum:settings",
            "enum:preset",
            "enum:rag",
            "enum:stt",
        )
    )


def remove_inline_keyboard(db: sqlite3.Connection, token: str, callback: dict) -> None:
    message = callback.get("message") or callback
    chat_id = str((message.get("chat") or {}).get("id", ""))
    message_id = message.get("message_id")
    if chat_id and message_id:
        close_panel_message(db, token, chat_id, callback)

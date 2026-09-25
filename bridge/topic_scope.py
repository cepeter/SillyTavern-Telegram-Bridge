"""Pure Telegram chat/forum-topic scope identifiers."""

from __future__ import annotations

TOPIC_SCOPE_SEPARATOR = "|topic:"


def topic_scope_id(chat_id: str, message_thread_id: int | str | None = None) -> str:
    chat_id = str(chat_id)
    if message_thread_id in (None, ""):
        return chat_id
    return f"{chat_id}{TOPIC_SCOPE_SEPARATOR}{int(message_thread_id)}"


def parse_topic_scope(scope_id: str) -> tuple[str, int | None]:
    value = str(scope_id)
    if TOPIC_SCOPE_SEPARATOR not in value:
        return value, None
    chat_id, thread_id = value.rsplit(TOPIC_SCOPE_SEPARATOR, 1)
    try:
        return chat_id, int(thread_id)
    except ValueError:
        return value, None


def topic_scope_from_message(chat_id: str, message: dict | None) -> str:
    return topic_scope_id(chat_id, (message or {}).get("message_thread_id"))

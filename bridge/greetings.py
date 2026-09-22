"""Character-card opening greeting helpers."""
from __future__ import annotations

from bridge.card_content import (
    replace_macros,
)

from bridge.common import (
    json,
    sqlite3,
    time,
)

from bridge.config import (
    CARD_FIELD_MAX_CHARS,
)

from bridge.database import (
    begin_operation,
    operation_was_applied,
    record_operation,
)

import random


def greeting_options(fields: dict) -> list[str]:
    """Return the primary greeting followed by bounded alternate greetings."""
    options = [str(fields.get("first_mes") or "")]
    raw = fields.get("alternate_greetings") or "[]"
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        values = []
    if isinstance(values, list):
        options.extend(str(value or "") for value in values[:20])
    return [value[:CARD_FIELD_MAX_CHARS].strip() for value in options if value[:CARD_FIELD_MAX_CHARS].strip()]


def send_character_greeting(db: sqlite3.Connection, token: str, chat_id: str, fields: dict, session_id: str, user_name: str, index: int | None = 0, operation_id=None, operation_kind: str = "greeting") -> bool:
    if operation_id is not None and (operation_was_applied(db, operation_id) or not begin_operation(db, operation_id, operation_kind)):
        return False
    options = greeting_options(fields)
    if not options:
        return False
    selected_index = random.randrange(len(options)) if index is None else int(index)  # nosec B311 - greeting choice is not security-sensitive
    if selected_index < 0 or selected_index >= len(options):
        return False
    greeting = replace_macros(options[selected_index], fields, user_name).strip()
    if not greeting:
        return False
    message_ids = send_text(token, chat_id, greeting)
    db.execute("INSERT INTO messages(chat_id,session_id,role,content,telegram_message_ids,created_at) VALUES(?,?,?,?,?,?)", (chat_id, session_id, "assistant", greeting, json.dumps(message_ids), time.time()))
    if operation_id is not None:
        record_operation(db, operation_id, operation_kind)
    db.commit()
    return True


# Explicit late imports replace transitional dependency injection.
from bridge.telegram import send_text

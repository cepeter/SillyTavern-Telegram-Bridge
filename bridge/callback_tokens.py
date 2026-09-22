"""Canonical callback-token cache and persistence helpers."""

import hashlib
import logging
import time

_CALLBACK_TOKEN_VALUES: dict[
    str,
    tuple[str, str, str, float],
] = {}
_CALLBACK_TOKEN_TTL_SECONDS = 900


def _run_db_action(db, action, error_message: str):
    """Run action(db) on the explicit request/job database; log failures."""
    try:
        return action(db)
    except Exception:
        logging.debug(error_message, exc_info=True)
        return None


def dynamic_callback_token(
    kind: str,
    value: str,
    chat_id: str = "",
    *,
    db,
) -> str:
    raw = f"{kind}|{chat_id}|{value}"
    token = (
        "t"
        + hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()[:16]
    )
    expires_at = time.time() + _CALLBACK_TOKEN_TTL_SECONDS
    _CALLBACK_TOKEN_VALUES[token] = (
        str(kind),
        str(value),
        str(chat_id),
        expires_at,
    )

    def persist(conn):
        conn.execute(
            "INSERT OR REPLACE INTO callback_tokens("
            "token,kind,value,chat_id,expires_at"
            ") VALUES(?,?,?,?,?)",
            (
                token,
                str(kind),
                str(value),
                str(chat_id),
                expires_at,
            ),
        )
        conn.commit()

    _run_db_action(
        db,
        persist,
        "Could not persist callback token",
    )
    return token


def resolve_dynamic_callback_token(
    token: str,
    kind: str,
    chat_id: str = "",
    *,
    db,
) -> str | None:
    item = _CALLBACK_TOKEN_VALUES.get(str(token))
    if item is None:

        def load(conn):
            row = conn.execute(
                "SELECT kind,value,chat_id,expires_at "
                "FROM callback_tokens WHERE token=?",
                (str(token),),
            ).fetchone()
            if row:
                found = (
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    float(row[3]),
                )
                _CALLBACK_TOKEN_VALUES[str(token)] = found
                return found
            return None

        item = _run_db_action(
            db,
            load,
            "Could not load callback token",
        )

    if item is None:
        return None

    (
        stored_kind,
        value,
        stored_chat_id,
        expires_at,
    ) = item

    if (
        expires_at < time.time()
        or stored_kind != str(kind)
        or (
            stored_chat_id
            and stored_chat_id != str(chat_id)
        )
    ):
        _CALLBACK_TOKEN_VALUES.pop(str(token), None)

        def forget(conn):
            conn.execute(
                "DELETE FROM callback_tokens WHERE token=?",
                (str(token),),
            )
            conn.commit()

        _run_db_action(
            db,
            forget,
            "Could not remove expired callback token",
        )
        return None

    return value

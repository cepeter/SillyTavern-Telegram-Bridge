"""Canonical swipe panels owner."""

from __future__ import annotations

import sqlite3

from bridge.delivery_port import DeliveryPort
from bridge.metadata import set_meta
from bridge.response_variants import last_user_variants, swipe_state_key


def swipe_markup() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "⬅️ Previous", "callback_data": "swipe:prev"}, {"text": "Next ➡️", "callback_data": "swipe:next"}],
            [
                {"text": "✅ Keep", "callback_data": "swipe:keep"},
                {"text": "❌ Cancel", "callback_data": "swipe:cancel"},
            ],
        ]
    }


def send_swipe_menu(
    token: str, db: sqlite3.Connection, chat_id: str, session_id: str, *, delivery_port: DeliveryPort, request_context
) -> None:
    user_row, variants = last_user_variants(db, chat_id, session_id)
    if not user_row or not variants:
        delivery_port.send_text(
            token, chat_id, "Belum ada response variant. Kirim pesan lalu gunakan /regen terlebih dahulu."
        )
        return
    selected = next((int(row[0]) for row in variants if row[2]), int(variants[-1][0]))
    set_meta(db, swipe_state_key(chat_id, session_id), str(selected))
    response = next((row[1] for row in variants if int(row[0]) == selected), variants[-1][1])
    text = f"Variant {selected} of {len(variants)}\n\n{response[:3900]}"
    result = delivery_port.send_panel_request(
        token,
        "sendMessage",
        {"chat_id": chat_id, "text": text, "reply_markup": swipe_markup()},
        request_context=request_context,
    )
    if result.get("message_id"):
        set_meta(db, f"swipe_message:{chat_id}:{session_id}", str(result["message_id"]))


def edit_swipe_menu(
    token: str,
    db: sqlite3.Connection,
    callback: dict,
    session_id: str,
    index: int,
    variants,
    *,
    delivery_port: DeliveryPort,
    request_context,
) -> None:
    message = callback.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    message_id = message.get("message_id")
    response = next(row[1] for row in variants if int(row[0]) == index)
    delivery_port.send_panel_request(
        token,
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": f"Variant {index} of {len(variants)}\n\n{response[:3900]}",
            "reply_markup": swipe_markup(),
        },
        request_context=request_context,
    )
    set_meta(db, swipe_state_key(chat_id, session_id), str(index))

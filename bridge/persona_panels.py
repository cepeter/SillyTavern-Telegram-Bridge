"""Canonical persona panels owner."""

from __future__ import annotations

import html

from bridge.callback_tokens import dynamic_callback_token
from bridge.cards import send_panel_message
from bridge.persona_service import PersonaService
from bridge.telegram import send_panel_request


def _persona_input_prompt(
    mode: str, current_name: str = "", persona_id: str = "", *, persona_service: PersonaService
) -> str:
    """Return the user-facing prompt for creating or editing a persona."""
    if mode == "create":
        return (
            "Create persona\n\nSend one line in this format:\n"
            "id | display name | persona description\n\n"
            "Use 1–64 letters, numbers, hyphens, or underscores for id. "
            "Description: 1–4,000 characters. Send /cancel to cancel."
        )
    persona = persona_service.get(persona_id) or {}
    description = str(persona.get("description") or "")
    if len(description) > 1600:
        description = description[:1600] + "…"
    if mode == "edit_name":
        return (
            f"Edit persona name\n\nCurrent name: {current_name}\n"
            f"Current description:\n{description}\n\n"
            "Send the new display name (1–120 characters). Send /cancel to cancel."
        )
    if mode == "edit_description":
        return (
            f"Edit persona description\n\nCurrent name: {current_name}\n"
            f"Current description:\n{description}\n\n"
            "Send the new description (1–4,000 characters). Send /cancel to cancel."
        )
    return (
        f"Edit persona\n\nCurrent name: {current_name}\n"
        f"Current description:\n{description}\n\n"
        "Send: display name | new description\nSend /cancel to cancel."
    )


def send_persona_edit_menu(
    token: str,
    chat_id: str,
    persona_id: str,
    message_id: int | None = None,
    *,
    persona_service: PersonaService,
    request_context,
) -> None:
    """Show current persona information before selecting an edit field."""
    persona = persona_service.get(persona_id)
    if not persona:
        send_panel_request(
            token,
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "Current persona is no longer available.",
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "⬅️ Back", "callback_data": "persona:menu"},
                            {"text": "❌ Close", "callback_data": "persona:cancel"},
                        ]
                    ]
                },
            },
            request_context=request_context,
        )
        return
    description = str(persona.get("description") or "")
    if len(description) > 1800:
        description = description[:1800] + "…"
    safe_id = html.escape(str(persona_id))
    safe_name = html.escape(str(persona.get("name") or persona_id))
    safe_description = html.escape(description)
    tags = ", ".join(str(tag) for tag in persona.get("tags", [])) or "none"
    text = (
        "Persona information\n\nID: "
        f"""{safe_id}"""
        "\nName: "
        f"""{safe_name}"""
        "\nDescription (tap the copy icon):\n<pre>"
        f"""{safe_description}"""
        "</pre>\n\nTags: "
        f"""{html.escape(tags)}"""
        "\n\nChoose what to update:"
    )
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "✏️ Edit name", "callback_data": "persona:edit_name"}],
                [{"text": "📝 Edit description", "callback_data": "persona:edit_description"}],
                [{"text": "🔧 Edit name + description", "callback_data": "persona:edit_all"}],
                [
                    {"text": "⬅️ Back", "callback_data": "persona:menu"},
                    {"text": "❌ Close", "callback_data": "persona:cancel"},
                ],
            ]
        },
    }
    if message_id:
        payload["message_id"] = message_id
    send_panel_request(
        token, "editMessageText" if message_id else "sendMessage", payload, request_context=request_context
    )


def send_persona_delete_confirm(
    token: str,
    chat_id: str,
    persona_id: str,
    message_id: int | None = None,
    *,
    persona_service: PersonaService,
    request_context,
) -> None:
    token_value = dynamic_callback_token("persona", persona_id, chat_id, db=request_context.db)
    payload = {
        "chat_id": chat_id,
        "text": (
            "Delete Persona '"
            f"""{persona_service.name(persona_id)}"""
            "'? Native Persona metadata will be removed; the avatar file will be "
            "preserved. This cannot be undone from the bridge."
        ),
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "✅ Confirm delete", "callback_data": "personadeleteconfirm:" + token_value}],
                [{"text": "❌ Cancel", "callback_data": "persona:menu"}],
            ]
        },
    }
    send_panel_message(
        token, chat_id, payload["text"], payload["reply_markup"], message_id, request_context=request_context
    )

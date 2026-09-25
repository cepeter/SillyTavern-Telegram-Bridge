"""Pure panel label, pagination, and navigation helpers."""

PANEL_PAGE_SIZE = 8


def panel_label(value: str, limit: int = 48) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def panel_page(items: list, page: int) -> tuple[list, int, int]:
    total_pages = max(
        1,
        (len(items) + PANEL_PAGE_SIZE - 1) // PANEL_PAGE_SIZE,
    )
    current_page = min(
        max(int(page), 0),
        total_pages - 1,
    )
    start = current_page * PANEL_PAGE_SIZE
    return (
        items[start : start + PANEL_PAGE_SIZE],
        current_page,
        total_pages,
    )


def panel_navigation(
    prefix: str,
    page: int,
    total_pages: int,
) -> list[dict[str, str]]:
    if total_pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(
            {
                "text": "⬅️ Previous",
                "callback_data": f"{prefix}:page:{page - 1}",
            }
        )
    if page < total_pages - 1:
        row.append(
            {
                "text": "Next ➡️",
                "callback_data": f"{prefix}:page:{page + 1}",
            }
        )
    return row


def panel_message_request(
    chat_id: str,
    text: str,
    reply_markup: dict,
    message_id: int | None = None,
) -> tuple[str, dict]:
    method = "editMessageText" if message_id else "sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": reply_markup,
    }
    if message_id:
        payload["message_id"] = message_id
    return method, payload

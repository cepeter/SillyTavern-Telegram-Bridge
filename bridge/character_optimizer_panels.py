"""Character optimizer selection and complete, paginated approval previews."""

from __future__ import annotations

from bridge.callback_tokens import dynamic_callback_token
from bridge.card_content import character_card_paths, character_display_name
from bridge.cards import send_panel_message
from bridge.character_quality import OPTIMIZABLE_FIELDS, character_rank, rank_badge
from bridge.panel_utils import panel_navigation, panel_page
from bridge.request_types import RequestContext


def send_character_optimize_menu(
    token: str, chat_id: str, message_id: int | None = None, page: int = 0, *, request_context: RequestContext
) -> None:
    options = [
        (path.name, character_display_name(path, app_settings=request_context.app_settings))
        for path in character_card_paths(app_settings=request_context.app_settings)
    ]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [
        [
            {
                "text": rank_badge(
                    character_rank(request_context.db, filename, app_settings=request_context.app_settings)
                )
                + label,
                "callback_data": "characteroptimize:"
                + dynamic_callback_token("character", filename, chat_id, db=request_context.db),
            }
        ]
        for filename, label in page_options
    ]
    navigation = panel_navigation("characteroptimize", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            {"text": "⬅️ Back", "callback_data": "character:menu"},
            {"text": "❌ Close", "callback_data": "character:cancel"},
        ]
    )
    text = f"Choose a character to optimize with the utility model (page {current_page + 1}/{total_pages}):"
    send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id, request_context=request_context)


def send_character_optimize_options(
    token: str,
    chat_id: str,
    filename: str,
    message_id: int | None = None,
    *,
    request_context: RequestContext,
) -> None:
    token_value = dynamic_callback_token("character", filename, chat_id, db=request_context.db)
    rows = [
        [{"text": "Auto Optimize", "callback_data": "characteroptimizeauto:" + token_value}],
        [{"text": "Manual Suggestion", "callback_data": "characteroptimizemanual:" + token_value}],
        [
            {"text": "Back", "callback_data": "character:optimize"},
            {"text": "Close", "callback_data": "character:cancel"},
        ],
    ]
    send_panel_message(
        token,
        chat_id,
        (
            f"Optimizer options: {filename}\n\n"
            "Auto Optimize uses the utility model directly. "
            "Manual Suggestion lets you give it editing guidance first."
        ),
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_character_optimize_result(
    token: str,
    chat_id: str,
    filename: str,
    fields: dict[str, str],
    nonce: str,
    message_id: int | None = None,
    page: int = 0,
    *,
    request_context: RequestContext,
) -> None:
    content = "Only the fields below change. Name, avatar, character book and extensions remain unchanged.\n\n"
    content += "\n\n".join(f"{key}:\n{fields[key]}" for key in OPTIMIZABLE_FIELDS if key in fields)
    # At most 3200 UTF-16 units per content page, including astral characters.
    pages = [content[i : i + 1600] for i in range(0, len(content), 1600)] or [""]
    current = min(max(int(page), 0), len(pages) - 1)
    rows = []
    navigation = []
    if current:
        navigation.append({"text": "Previous", "callback_data": f"characteroptimizepreview:{nonce}:{current - 1}"})
    if current + 1 < len(pages):
        navigation.append({"text": "Next", "callback_data": f"characteroptimizepreview:{nonce}:{current + 1}"})
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            {"text": "Apply", "callback_data": f"characteroptimizeapply:{nonce}"},
            {"text": "Manual Suggestion", "callback_data": f"characteroptimizerefine:{nonce}"},
        ]
    )
    rows.append([{"text": "Cancel", "callback_data": f"characteroptimizecancel:{nonce}"}])
    text = f"Optimization preview: {filename}\nPage {current + 1}/{len(pages)}\n\n" + pages[current]
    send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id, request_context=request_context)

"""Canonical group panels owner."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from bridge.callback_tokens import dynamic_callback_token
from bridge.card_content import character_card_paths
from bridge.cards import send_panel_message
from bridge.group_service import GroupService
from bridge.panel_utils import panel_label, panel_page
from bridge.topic_scope import parse_topic_scope


def send_group_menu(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    message_id: int | None = None,
    *,
    group_service: GroupService,
    request_context,
) -> None:
    state = group_service.state(db, chat_id, session["session_id"])
    labels = group_service.member_labels(state["members"])
    current = labels[int(state["turn_index"]) % len(labels)] if state["enabled"] and labels else "off"
    if state.get("forced_speaker") in state["members"]:
        current = group_service.member_labels([state["forced_speaker"]])[0]
    user_turn = (
        "open"
        if state["mode"] == "manual" and not state.get("turn_user_id")
        else "assigned"
        if state["mode"] == "manual"
        else "not used"
    )
    rows = []
    for filename, label in zip(state["members"], labels, strict=False):
        callback_token = dynamic_callback_token("group_character", filename, chat_id, db=request_context.db)
        rows.append(
            [
                {"text": "🎙️ " + panel_label(label), "callback_data": "groupchars:speak:" + callback_token},
                {"text": "🗑️", "callback_data": "groupremove:" + callback_token},
            ]
        )
    rows.extend(
        [
            [
                {"text": "➕ Add character", "callback_data": "group:add"},
                {"text": "🎙️ Choose speaker", "callback_data": "group:speak"},
            ],
            [{"text": "⚙️ Mode", "callback_data": "group:mode"}],
            [{"text": "✅ Enable", "callback_data": "group:on"}, {"text": "🚫 Disable", "callback_data": "group:off"}],
        ]
    )
    if state["mode"] == "manual":
        rows.append(
            [
                {"text": "🙋 Claim turn", "callback_data": "group:claim"},
                {"text": "➡️ Pass user turn", "callback_data": "group:pass"},
            ]
        )
    if parse_topic_scope(chat_id)[1] is not None:
        rows.append([{"text": "🆕 New group session", "callback_data": "group:new_session"}])
    rows.append(
        [
            {"text": "➡️ Next speaker", "callback_data": "group:next"},
            {"text": "❌ Close", "callback_data": "group:close"},
        ]
    )
    text = (
        "Group chat: "
        f"""{("on" if state["enabled"] else "off")}"""
        "\nMode: "
        f"""{state["mode"]}"""
        "\nMembers: "
        f"""{(", ".join(labels) if labels else "none")}"""
        "\nCurrent speaker: "
        f"""{current}"""
        "\nUser turn: "
        f"""{user_turn}"""
        "\nChoose a group action:"
    )
    try:
        send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id, request_context=request_context)
    except RuntimeError as exc:
        if "not modified" in str(exc).casefold():
            logging.info("Group panel already shows the requested state")
            return
        raise


def send_group_character_menu(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    action: str,
    message_id: int | None = None,
    page: int = 0,
    *,
    group_service: GroupService,
    request_context,
) -> None:
    state = group_service.state(db, chat_id, session["session_id"])
    members = list(state["members"])
    if action == "add":
        options = [
            (path.name, group_service.character_option_label(path))
            for path in character_card_paths(app_settings=request_context.app_settings)
            if path.name not in members
        ]
    else:
        options = [(filename, group_service.member_labels([filename])[0]) for filename in members]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [
        [
            {
                "text": panel_label(label),
                "callback_data": (
                    "groupchars:"
                    f"""{action}"""
                    ":"
                    f"""{dynamic_callback_token("group_character", filename, chat_id, db=request_context.db)}"""
                ),
            }
        ]
        for filename, label in page_options
    ]
    if total_pages > 1:
        navigation = []
        if current_page > 0:
            navigation.append({"text": "⬅️ Previous", "callback_data": f"groupchars:page:{action}:{current_page - 1}"})
        if current_page < total_pages - 1:
            navigation.append({"text": "Next ➡️", "callback_data": f"groupchars:page:{action}:{current_page + 1}"})
        rows.append(navigation)
    rows.append(
        [{"text": "⬅️ Back", "callback_data": "group:menu"}, {"text": "❌ Close", "callback_data": "group:close"}]
    )
    text = f"Group {action} character (page {current_page + 1}/{total_pages}):"
    send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id, request_context=request_context)


def send_group_speaker_menu(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    message_id: int | None = None,
    *,
    group_service: GroupService,
    request_context,
) -> None:
    state = group_service.state(db, chat_id, session["session_id"])
    rows = [
        [
            {
                "text": panel_label(label),
                "callback_data": (
                    "groupchars:speak:"
                    f"""{dynamic_callback_token("group_character", filename, chat_id, db=request_context.db)}"""
                ),
            }
        ]
        for filename, label in zip(state["members"], group_service.member_labels(state["members"]), strict=False)
    ]
    rows.append(
        [{"text": "⬅️ Back", "callback_data": "group:menu"}, {"text": "❌ Close", "callback_data": "group:close"}]
    )
    send_panel_message(
        token,
        chat_id,
        "Choose the next group speaker:",
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_group_mode_menu(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    message_id: int | None = None,
    *,
    group_service: GroupService,
    request_context,
) -> None:
    state = group_service.state(db, chat_id, session["session_id"])
    modes = [
        ("round_robin", "Round robin"),
        ("contextual", "Contextual"),
        ("director", "Director"),
        ("manual", "Manual"),
        ("autonomous", "Autonomous"),
    ]
    rows = [
        [{"text": ("✅ " if mode == state["mode"] else "") + label, "callback_data": f"groupmode:{mode}"}]
        for mode, label in modes
    ]
    rows.append(
        [{"text": "⬅️ Back", "callback_data": "group:menu"}, {"text": "❌ Close", "callback_data": "group:close"}]
    )
    send_panel_message(
        token,
        chat_id,
        f"Current group mode: {state['mode']}\nChoose a mode:",
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_group_remove_confirm(
    token: str, chat_id: str, filename: str, message_id: int | None = None, *, request_context
) -> None:
    callback_token = dynamic_callback_token("group_character", filename, chat_id, db=request_context.db)
    markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Remove from group", "callback_data": "groupremoveconfirm:" + callback_token},
                {"text": "❌ Cancel", "callback_data": "group:menu"},
            ]
        ]
    }
    send_panel_message(
        token,
        chat_id,
        f"Remove '{Path(filename).stem}' from this group? The native character card will not be deleted.",
        markup,
        message_id,
        request_context=request_context,
    )

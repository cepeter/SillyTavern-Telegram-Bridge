"""Canonical group commands owner."""

from __future__ import annotations

import sqlite3

from bridge.group_service import GroupService
from bridge.memory import generate_session_summary
from bridge.provider_port import ProviderPort
from bridge.settings import AppSettings
from bridge.telegram import send_text, send_typing


def handle_group_command(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    command_text: str,
    operation_id: int | str | None = None,
    *,
    group_service: GroupService,
) -> None:
    parts = command_text.split(None, 2)
    action = parts[1].casefold() if len(parts) > 1 else "status"
    state = group_service.state(db, chat_id, session["session_id"])
    members = list(state["members"])
    if action in {"status", "list"}:
        labels = group_service.member_labels(members)
        current = labels[int(state["turn_index"]) % len(labels)] if state["enabled"] and labels else "off"
        if state.get("forced_speaker") in members:
            current = group_service.member_labels([state["forced_speaker"]])[0]
        send_text(
            token,
            chat_id,
            (
                "Group chat: "
                f"""{("on" if state["enabled"] else "off")}"""
                "\nMode: "
                f"""{state["mode"]}"""
                "\nMembers: "
                f"""{(", ".join(labels) if labels else "none")}"""
                "\nCurrent speaker: "
                f"""{current}"""
                "\nUse /group add <character>, /group speak <character>, /group mode "
                "<round_robin|contextual|director|manual|autonomous>, or /group off."
            ),
        )
        return
    if action == "add":
        requested = parts[2].strip() if len(parts) > 2 else ""
        filename = group_service.resolve_character(requested)
        if not filename:
            send_text(token, chat_id, "Character not found. Use /character to see installed cards.")
            return
        if not members:
            members.append(session["character_file"])
        if filename not in members:
            if len(members) >= 6:
                send_text(token, chat_id, "A group can contain at most 6 characters.")
                return
            members.append(filename)
        state["members"] = members
        state["enabled"] = len(members) >= 2
        group_service.save(db, chat_id, session["session_id"], state, operation_id)
        send_text(
            token,
            chat_id,
            (
                "Group members: "
                f"""{", ".join(group_service.member_labels(members))}"""
                "\nGroup chat: "
                f"""{("on" if state["enabled"] else "off")}"""
            ),
        )
        return
    if action == "remove":
        requested = parts[2].strip() if len(parts) > 2 else ""
        filename = group_service.resolve_character(requested)
        if filename not in members:
            send_text(token, chat_id, "That character is not in the group.")
            return
        members.remove(filename)
        state["members"] = members
        state["enabled"] = len(members) >= 2
        state["turn_index"] = 0
        group_service.save(db, chat_id, session["session_id"], state, operation_id)
        send_text(
            token,
            chat_id,
            (
                "Group chat: "
                f"""{", ".join(group_service.member_labels(members))}"""
                "\nGroup chat: "
                f"""{("on" if state["enabled"] else "off")}"""
            ),
        )
        return
    if action == "mode":
        requested_mode = parts[2].casefold() if len(parts) > 2 else ""
        if requested_mode not in {"round_robin", "contextual", "director", "manual", "autonomous"}:
            send_text(
                token,
                chat_id,
                (
                    "Use /group mode round_robin, /group mode contextual, /group mode "
                    "director, /group mode manual, or /group mode autonomous."
                ),
            )
            return
        state["mode"] = requested_mode
        if requested_mode != "manual":
            state["turn_user_id"] = ""
        group_service.save(db, chat_id, session["session_id"], state, operation_id)
        send_text(token, chat_id, f"Group mode set to {requested_mode}.")
        return
    if action == "speak":
        requested = parts[2].strip() if len(parts) > 2 else ""
        filename = group_service.resolve_character(requested)
        if filename not in members:
            send_text(token, chat_id, "That character is not in the group.")
            return
        state["forced_speaker"] = filename
        state["enabled"] = len(members) >= 2
        group_service.save(db, chat_id, session["session_id"], state, operation_id)
        send_text(token, chat_id, f"Next speaker forced to {group_service.member_labels([filename])[0]}.")
        return
    if action == "on":
        if len(members) < 2:
            send_text(token, chat_id, "Add at least two characters first: /group add Karen")
            return
        state["enabled"] = True
        group_service.save(db, chat_id, session["session_id"], state, operation_id)
        send_text(token, chat_id, "Group chat enabled.")
        return
    if action == "off":
        state["enabled"] = False
        state["turn_user_id"] = ""
        group_service.save(db, chat_id, session["session_id"], state, operation_id)
        send_text(token, chat_id, "Group chat disabled; the session uses its default character.")
        return
    if action == "next":
        if len(members) < 2:
            send_text(token, chat_id, "The group needs at least two characters.")
            return
        state["turn_index"] = int(state["turn_index"]) + 1
        group_service.save(db, chat_id, session["session_id"], state, operation_id)
        current = group_service.member_labels(members)[state["turn_index"] % len(members)]
        send_text(token, chat_id, f"Next group speaker: {current}")
        return
    send_text(
        token,
        chat_id,
        (
            "Use /group status, /group add <character>, /group remove <character>, "
            "/group speak <character>, /group mode "
            "<round_robin|contextual|director|manual|autonomous>, /group on, /group "
            "off, or /group next."
        ),
    )


def handle_summary_command(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    *,
    provider_port: ProviderPort,
    app_settings: AppSettings,
) -> None:
    send_typing(token, chat_id)
    summary = generate_session_summary(
        db, chat_id, session, force=True, provider_port=provider_port, app_settings=app_settings
    )
    if summary:
        send_text(token, chat_id, "Session summary updated:\n\n" + summary)
    else:
        send_text(token, chat_id, "No chat messages are available to summarize.")

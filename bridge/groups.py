from __future__ import annotations

from bridge.callback_tokens import (
    dynamic_callback_token,
    resolve_dynamic_callback_token,
)

from bridge.card_content import (
    card_fields_from_file,
    character_card_paths,
    safe_character_path,
)

from bridge.common import (
    Path,
    json,
    logging,
    sqlite3,
    time,
)

from bridge.config import (
    DEFAULT_CHARACTER_FILE,
    DEFAULT_MODEL,
    PENDING_SETTINGS_TTL_SECONDS,
)

from bridge.database import (
    get_generation_settings,
    set_meta,
)

from bridge.panel_utils import (
    panel_label,
    panel_page,
)

from bridge.group_core import (
    group_state,
    group_user_turn_allowed,
    claim_group_user_turn,
    pass_group_user_turn,
    group_setup_state,
    group_character_option_label,
    save_group_state,
    resolve_character_file,
    group_member_labels,
    group_current_speaker,
    advance_group_turn,
)

def send_group_menu(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], message_id: int | None = None) -> None:
    state = group_state(db, chat_id, session["session_id"])
    labels = group_member_labels(state["members"])
    current = labels[int(state["turn_index"]) % len(labels)] if state["enabled"] and labels else "off"
    if state.get("forced_speaker") in state["members"]:
        current = group_member_labels([state["forced_speaker"]])[0]
    user_turn = "open" if state["mode"] == "manual" and not state.get("turn_user_id") else "assigned" if state["mode"] == "manual" else "not used"
    rows = []
    for filename, label in zip(state["members"], labels):
        callback_token = dynamic_callback_token("group_character", filename, chat_id)
        rows.append([
            {"text": "🎙️ " + panel_label(label), "callback_data": "groupchars:speak:" + callback_token},
            {"text": "🗑️", "callback_data": "groupremove:" + callback_token},
        ])
    rows.extend([
        [{"text": "➕ Add character", "callback_data": "group:add"}, {"text": "🎙️ Choose speaker", "callback_data": "group:speak"}],
        [{"text": "⚙️ Mode", "callback_data": "group:mode"}],
        [{"text": "✅ Enable", "callback_data": "group:on"}, {"text": "🚫 Disable", "callback_data": "group:off"}],
    ])
    if state["mode"] == "manual":
        rows.append([{"text": "🙋 Claim turn", "callback_data": "group:claim"}, {"text": "➡️ Pass user turn", "callback_data": "group:pass"}])
    if parse_topic_scope(chat_id)[1] is not None:
        rows.append([{"text": "🆕 New group session", "callback_data": "group:new_session"}])
    rows.append([{"text": "➡️ Next speaker", "callback_data": "group:next"}, {"text": "❌ Close", "callback_data": "group:close"}])
    text = f"Group chat: {'on' if state['enabled'] else 'off'}\nMode: {state['mode']}\nMembers: {', '.join(labels) if labels else 'none'}\nCurrent speaker: {current}\nUser turn: {user_turn}\nChoose a group action:"
    try:
        send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id)
    except RuntimeError as exc:
        if "not modified" in str(exc).casefold():
            logging.info("Group panel already shows the requested state")
            return
        raise
def start_group_session(db: sqlite3.Connection, chat_id: str, default_model: str, title: str = "New group session", session_id: str | None = None) -> dict[str, str]:
    """Create a clean topic-local session for the New group session wizard."""
    session_id = session_id or f"group-{time.time_ns()}"
    session = create_session(db, chat_id, default_model, session_id=session_id, title=title)
    update_session(db, chat_id, session_id, character_file=DEFAULT_CHARACTER_FILE, world_file="")
    save_group_state(db, chat_id, session_id, {
        "title": title,
        "enabled": False,
        "turn_index": 0,
        "mode": "round_robin",
        "forced_speaker": "",
        "members": [],
        "turn_user_id": "",
        "turn_users": [],
    })
    set_meta(db, f"group_setup:{chat_id}", json.dumps({"session_id": session_id, "stage": "character", "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}))
    return load_session(db, chat_id, session_id, default_model)


def apply_group_setup_character(db: sqlite3.Connection, token: str, callback: dict, answer_callback, chat_id: str, message: dict, session_id: str, operation_id: int | str | None, filename: str, character_name: str) -> bool:
    """Apply the wizard character and open the topic-local World Info picker."""
    update_session(db, chat_id, session_id, operation_id=operation_id, operation_kind="group_setup_character", character_file=Path(filename).name)
    setup = group_setup_state(db, chat_id, session_id)
    setup.update({"stage": "world", "character_file": Path(filename).name, "character_name": character_name})
    set_meta(db, f"group_setup:{chat_id}", json.dumps(setup))
    answer_callback(token, str(callback.get("id", "")), "Choose World Info")
    discard_panel_binding(db, chat_id, message.get("message_id"))
    close_panel_message(db, token, chat_id, callback)
    send_world_menu(token, chat_id, "", None, 0)
    return True
def send_group_character_menu(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], action: str, message_id: int | None = None, page: int = 0) -> None:
    state = group_state(db, chat_id, session["session_id"])
    members = list(state["members"])
    if action == "add":
        options = [(path.name, group_character_option_label(path)) for path in character_card_paths() if path.name not in members]
    else:
        options = [(filename, group_member_labels([filename])[0]) for filename in members]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [[{"text": panel_label(label), "callback_data": f"groupchars:{action}:{dynamic_callback_token('group_character', filename, chat_id)}"}] for filename, label in page_options]
    if total_pages > 1:
        navigation = []
        if current_page > 0:
            navigation.append({"text": "⬅️ Previous", "callback_data": f"groupchars:page:{action}:{current_page - 1}"})
        if current_page < total_pages - 1:
            navigation.append({"text": "Next ➡️", "callback_data": f"groupchars:page:{action}:{current_page + 1}"})
        rows.append(navigation)
    rows.append([{"text": "⬅️ Back", "callback_data": "group:menu"}, {"text": "❌ Close", "callback_data": "group:close"}])
    text = f"Group {action} character (page {current_page + 1}/{total_pages}):"
    send_panel_message(token, chat_id, text, {"inline_keyboard": rows}, message_id)


def send_group_speaker_menu(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], message_id: int | None = None) -> None:
    state = group_state(db, chat_id, session["session_id"])
    rows = [[{"text": panel_label(label), "callback_data": f"groupchars:speak:{dynamic_callback_token('group_character', filename, chat_id)}"}] for filename, label in zip(state["members"], group_member_labels(state["members"]))]
    rows.append([{"text": "⬅️ Back", "callback_data": "group:menu"}, {"text": "❌ Close", "callback_data": "group:close"}])
    send_panel_message(token, chat_id, "Choose the next group speaker:", {"inline_keyboard": rows}, message_id)


def send_group_mode_menu(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], message_id: int | None = None) -> None:
    state = group_state(db, chat_id, session["session_id"])
    modes = [("round_robin", "Round robin"), ("contextual", "Contextual"), ("director", "Director"), ("manual", "Manual"), ("autonomous", "Autonomous")]
    rows = [[{"text": ("✅ " if mode == state["mode"] else "") + label, "callback_data": f"groupmode:{mode}"}] for mode, label in modes]
    rows.append([{"text": "⬅️ Back", "callback_data": "group:menu"}, {"text": "❌ Close", "callback_data": "group:close"}])
    send_panel_message(token, chat_id, f"Current group mode: {state['mode']}\nChoose a mode:", {"inline_keyboard": rows}, message_id)


def send_group_remove_confirm(token: str, chat_id: str, filename: str, message_id: int | None = None) -> None:
    callback_token = dynamic_callback_token("group_character", filename, chat_id)
    markup = {"inline_keyboard": [[{"text": "✅ Remove from group", "callback_data": "groupremoveconfirm:" + callback_token}, {"text": "❌ Cancel", "callback_data": "group:menu"}]]}
    send_panel_message(token, chat_id, f"Remove '{Path(filename).stem}' from this group? The native character card will not be deleted.", markup, message_id)


def handle_group_panel_callback(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], data: str, message: dict, operation_id: int | str | None = None, sender_id: str = "") -> None:
    message_id = message.get("message_id")
    if data == "group:new_session":
        start_session_name_input(db, token, chat_id, session, kind="group", message=message)
    elif data == "group:menu":
        send_group_menu(db, token, chat_id, session, message_id)
    elif data == "group:add":
        send_group_character_menu(db, token, chat_id, session, "add", message_id)
    elif data == "group:remove":
        send_group_character_menu(db, token, chat_id, session, "remove", message_id)
    elif data == "group:speak":
        send_group_speaker_menu(db, token, chat_id, session, message_id)
    elif data == "group:mode":
        send_group_mode_menu(db, token, chat_id, session, message_id)
    elif data in {"group:claim", "group:pass"}:
        success = claim_group_user_turn(db, chat_id, session["session_id"], sender_id) if data == "group:claim" else pass_group_user_turn(db, chat_id, session["session_id"], sender_id)
        send_text(token, chat_id, "User turn claimed." if data == "group:claim" and success else "User turn passed." if data == "group:pass" and success else "You cannot change the current user turn.")
        send_group_menu(db, token, chat_id, session, message_id)
    elif data in {"group:on", "group:off", "group:next"}:
        handle_group_command(db, token, chat_id, session, "/" + data.replace(":", " "), operation_id)
        send_group_menu(db, token, chat_id, session, message_id)
    elif data == "group:close":
        remove_inline_keyboard(db, token, {"message": message})
    elif data.startswith("groupremoveconfirm:"):
        filename = resolve_dynamic_callback_token(data.split(":", 1)[1], "group_character", chat_id) or ""
        state = group_state(db, chat_id, session["session_id"])
        if filename in state["members"]:
            handle_group_command(db, token, chat_id, session, f"/group remove {filename}", operation_id)
        send_group_menu(db, token, chat_id, session, message_id)
    elif data.startswith("groupremove:"):
        filename = resolve_dynamic_callback_token(data.split(":", 1)[1], "group_character", chat_id) or ""
        if filename in group_state(db, chat_id, session["session_id"])["members"]:
            send_group_remove_confirm(token, chat_id, filename, message_id)
        else:
            send_group_menu(db, token, chat_id, session, message_id)
    elif data.startswith("groupchars:page:"):
        _, _, action, page = data.split(":", 3)
        send_group_character_menu(db, token, chat_id, session, action, message_id, int(page))
    elif data.startswith("groupchars:"):
        _, action, callback_token = data.split(":", 2)
        filename = resolve_dynamic_callback_token(callback_token, "group_character", chat_id) or ""
        if filename:
            handle_group_command(db, token, chat_id, session, f"/group {action} {filename}", operation_id)
        send_group_menu(db, token, chat_id, session, message_id)
    elif data.startswith("groupmode:"):
        handle_group_command(db, token, chat_id, session, f"/group mode {data.split(':', 1)[1]}", operation_id)
        send_group_mode_menu(db, token, chat_id, session, message_id)
def handle_group_command(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], command_text: str, operation_id: int | str | None = None) -> None:
    parts = command_text.split(None, 2)
    action = parts[1].casefold() if len(parts) > 1 else "status"
    state = group_state(db, chat_id, session["session_id"])
    members = list(state["members"])
    if action in {"status", "list"}:
        labels = group_member_labels(members)
        current = labels[int(state["turn_index"]) % len(labels)] if state["enabled"] and labels else "off"
        if state.get("forced_speaker") in members:
            current = group_member_labels([state["forced_speaker"]])[0]
        send_text(token, chat_id, f"Group chat: {'on' if state['enabled'] else 'off'}\nMode: {state['mode']}\nMembers: {', '.join(labels) if labels else 'none'}\nCurrent speaker: {current}\nUse /group add <character>, /group speak <character>, /group mode <round_robin|contextual|director|manual|autonomous>, or /group off.")
        return
    if action == "add":
        requested = parts[2].strip() if len(parts) > 2 else ""
        filename = resolve_character_file(requested)
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
        save_group_state(db, chat_id, session["session_id"], state, operation_id)
        send_text(token, chat_id, f"Group members: {', '.join(group_member_labels(members))}\nGroup chat: {'on' if state['enabled'] else 'off'}")
        return
    if action == "remove":
        requested = parts[2].strip() if len(parts) > 2 else ""
        filename = resolve_character_file(requested)
        if filename not in members:
            send_text(token, chat_id, "That character is not in the group.")
            return
        members.remove(filename)
        state["members"] = members
        state["enabled"] = len(members) >= 2
        state["turn_index"] = 0
        save_group_state(db, chat_id, session["session_id"], state, operation_id)
        send_text(token, chat_id, f"Group chat: {', '.join(group_member_labels(members))}\nGroup chat: {'on' if state['enabled'] else 'off'}")
        return
    if action == "mode":
        requested_mode = parts[2].casefold() if len(parts) > 2 else ""
        if requested_mode not in {"round_robin", "contextual", "director", "manual", "autonomous"}:
            send_text(token, chat_id, "Use /group mode round_robin, /group mode contextual, /group mode director, /group mode manual, or /group mode autonomous.")
            return
        state["mode"] = requested_mode
        if requested_mode != "manual":
            state["turn_user_id"] = ""
        save_group_state(db, chat_id, session["session_id"], state, operation_id)
        send_text(token, chat_id, f"Group mode set to {requested_mode}.")
        return
    if action == "speak":
        requested = parts[2].strip() if len(parts) > 2 else ""
        filename = resolve_character_file(requested)
        if filename not in members:
            send_text(token, chat_id, "That character is not in the group.")
            return
        state["forced_speaker"] = filename
        state["enabled"] = len(members) >= 2
        save_group_state(db, chat_id, session["session_id"], state, operation_id)
        send_text(token, chat_id, f"Next speaker forced to {group_member_labels([filename])[0]}.")
        return
    if action == "on":
        if len(members) < 2:
            send_text(token, chat_id, "Add at least two characters first: /group add Karen")
            return
        state["enabled"] = True
        save_group_state(db, chat_id, session["session_id"], state, operation_id)
        send_text(token, chat_id, "Group chat enabled.")
        return
    if action == "off":
        state["enabled"] = False
        state["turn_user_id"] = ""
        save_group_state(db, chat_id, session["session_id"], state, operation_id)
        send_text(token, chat_id, "Group chat disabled; the session uses its default character.")
        return
    if action == "next":
        if len(members) < 2:
            send_text(token, chat_id, "The group needs at least two characters.")
            return
        state["turn_index"] = int(state["turn_index"]) + 1
        save_group_state(db, chat_id, session["session_id"], state, operation_id)
        current = group_member_labels(members)[state["turn_index"] % len(members)]
        send_text(token, chat_id, f"Next group speaker: {current}")
        return
    send_text(token, chat_id, "Use /group status, /group add <character>, /group remove <character>, /group speak <character>, /group mode <round_robin|contextual|director|manual|autonomous>, /group on, /group off, or /group next.")


def handle_summary_command(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str]) -> None:
    send_typing(token, chat_id)
    summary = generate_session_summary(db, chat_id, session, force=True)
    if summary:
        send_text(token, chat_id, "Session summary updated:\n\n" + summary)
    else:
        send_text(token, chat_id, "No chat messages are available to summarize.")


# Explicit late imports replace transitional dependency injection.
from bridge.callbacks import (
    close_panel_message,
    discard_panel_binding,
)
from bridge.cards import send_panel_message
from bridge.catalog import send_world_menu
from bridge.common import parse_topic_scope
from bridge.generation import generate_text
from bridge.media import (
    remove_inline_keyboard,
    send_typing,
)
from bridge.memory import generate_session_summary
from bridge.session_naming import start_session_name_input
from bridge.telegram import (
    create_session,
    load_session,
    send_text,
    update_session,
)

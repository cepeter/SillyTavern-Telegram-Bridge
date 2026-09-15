def group_state(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, object]:
    row = db.execute("SELECT title,enabled,turn_index,mode,forced_speaker,members_json FROM group_sessions WHERE chat_id=? AND session_id=?", (chat_id, session_id)).fetchone()
    if not row:
        return {"title": "Group chat", "enabled": False, "turn_index": 0, "mode": "round_robin", "forced_speaker": "", "members": []}
    try:
        members = json.loads(row[5])
    except (TypeError, json.JSONDecodeError):
        members = []
    return {"title": row[0], "enabled": bool(row[1]), "turn_index": int(row[2]), "mode": str(row[3] or "round_robin"), "forced_speaker": str(row[4] or ""), "members": [str(item) for item in members if isinstance(item, str)]}


def send_group_menu(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], message_id: int | None = None) -> None:
    state = group_state(db, chat_id, session["session_id"])
    labels = group_member_labels(state["members"])
    current = labels[int(state["turn_index"]) % len(labels)] if state["enabled"] and labels else "off"
    if state.get("forced_speaker") in state["members"]:
        current = group_member_labels([state["forced_speaker"]])[0]
    rows = [
        [{"text": "➕ Add character", "callback_data": "group:add"}, {"text": "➖ Remove character", "callback_data": "group:remove"}],
        [{"text": "🎙️ Choose speaker", "callback_data": "group:speak"}, {"text": "⚙️ Mode", "callback_data": "group:mode"}],
        [{"text": "✅ Enable", "callback_data": "group:on"}, {"text": "🚫 Disable", "callback_data": "group:off"}],
        [{"text": "➡️ Next speaker", "callback_data": "group:next"}, {"text": "❌ Close", "callback_data": "group:close"}],
    ]
    text = f"Group chat: {'on' if state['enabled'] else 'off'}\nMode: {state['mode']}\nMembers: {', '.join(labels) if labels else 'none'}\nCurrent speaker: {current}\nChoose a group action:"
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def group_character_option_label(path: Path) -> str:
    try:
        return str(card_fields(read_png_chara(path)).get("name") or path.stem)
    except Exception:
        return path.stem


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
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_group_speaker_menu(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], message_id: int | None = None) -> None:
    state = group_state(db, chat_id, session["session_id"])
    rows = [[{"text": panel_label(label), "callback_data": f"groupchars:speak:{dynamic_callback_token('group_character', filename, chat_id)}"}] for filename, label in zip(state["members"], group_member_labels(state["members"]))]
    rows.append([{"text": "⬅️ Back", "callback_data": "group:menu"}, {"text": "❌ Close", "callback_data": "group:close"}])
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": "Choose the next group speaker:", "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_group_mode_menu(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], message_id: int | None = None) -> None:
    state = group_state(db, chat_id, session["session_id"])
    modes = [("round_robin", "Round robin"), ("contextual", "Contextual"), ("manual", "Manual"), ("autonomous", "Autonomous")]
    rows = [[{"text": ("✅ " if mode == state["mode"] else "") + label, "callback_data": f"groupmode:{mode}"}] for mode, label in modes]
    rows.append([{"text": "⬅️ Back", "callback_data": "group:menu"}, {"text": "❌ Close", "callback_data": "group:close"}])
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": f"Current group mode: {state['mode']}\nChoose a mode:", "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def handle_group_panel_callback(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], data: str, message: dict, operation_id: int | str | None = None) -> None:
    message_id = message.get("message_id")
    if data == "group:menu":
        send_group_menu(db, token, chat_id, session, message_id)
    elif data == "group:add":
        send_group_character_menu(db, token, chat_id, session, "add", message_id)
    elif data == "group:remove":
        send_group_character_menu(db, token, chat_id, session, "remove", message_id)
    elif data == "group:speak":
        send_group_speaker_menu(db, token, chat_id, session, message_id)
    elif data == "group:mode":
        send_group_mode_menu(db, token, chat_id, session, message_id)
    elif data in {"group:on", "group:off", "group:next"}:
        handle_group_command(db, token, chat_id, session, "/" + data.replace(":", " "), operation_id)
        send_group_menu(db, token, chat_id, session, message_id)
    elif data == "group:close":
        remove_inline_keyboard(token, {"message": message})
    elif data.startswith("groupchars:page:"):
        _, _, action, page = data.split(":", 3)
        send_group_character_menu(db, token, chat_id, session, action, message_id, int(page))
    elif data.startswith("groupchars:"):
        _, action, filename = data.split(":", 2)
        handle_group_command(db, token, chat_id, session, f"/group {action} {filename}", operation_id)
        send_group_menu(db, token, chat_id, session, message_id)
    elif data.startswith("groupmode:"):
        handle_group_command(db, token, chat_id, session, f"/group mode {data.split(':', 1)[1]}", operation_id)
        send_group_mode_menu(db, token, chat_id, session, message_id)


def save_group_state(db: sqlite3.Connection, chat_id: str, session_id: str, state: dict[str, object], operation_id: int | str | None = None, commit: bool = True) -> bool:
    if not begin_operation(db, operation_id, "group_state"):
        return False
    db.execute("INSERT OR REPLACE INTO group_sessions(chat_id,session_id,title,enabled,turn_index,mode,forced_speaker,members_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (chat_id, session_id, str(state.get("title") or "Group chat"), int(bool(state.get("enabled"))), int(state.get("turn_index") or 0), str(state.get("mode") or "round_robin"), str(state.get("forced_speaker") or ""), json.dumps(state.get("members") or [], ensure_ascii=False), time.time()))
    record_operation(db, operation_id, "group_state")
    if commit:
        db.commit()
    return True


def resolve_character_file(requested: str) -> str | None:
    wanted = requested.strip().casefold()
    for path in character_card_paths():
        try:
            label = card_fields(read_png_chara(path))["name"]
        except Exception:
            label = path.stem
        if wanted in {path.name.casefold(), path.stem.casefold(), label.casefold()}:
            return path.name
    return None


def group_member_labels(member_files: list[str]) -> list[str]:
    labels = []
    for filename in member_files:
        try:
            labels.append(card_fields_from_file(filename)["name"])
        except Exception:
            labels.append(Path(filename).stem)
    return labels


def group_current_speaker(db: sqlite3.Connection, chat_id: str, session: dict[str, str], user_text: str = "") -> tuple[str, dict[str, object]] | None:
    state = group_state(db, chat_id, session["session_id"])
    members = [name for name in state["members"] if safe_character_path(name)]
    if not state["enabled"] or len(members) < 2:
        return None
    forced = str(state.get("forced_speaker") or "")
    if forced in members:
        return forced, state
    if state.get("mode") == "contextual" and user_text:
        lowered = user_text.casefold()
        for filename in members:
            label = card_fields_from_file(filename)["name"]
            if label.casefold() in lowered or Path(filename).stem.casefold() in lowered:
                return filename, state
    index = int(state["turn_index"]) % len(members)
    return members[index], state


def group_prompt_context(db: sqlite3.Connection, chat_id: str, session: dict[str, str], speaker_file: str) -> str:
    state = group_state(db, chat_id, session["session_id"])
    labels = group_member_labels([name for name in state["members"] if safe_character_path(name)])
    speaker = card_fields_from_file(speaker_file)["name"]
    others = ", ".join(label for label in labels if label != speaker) or "none"
    if state.get("mode") == "autonomous":
        return f"You are in a bounded autonomous multi-character scene. Current lead speaker: {speaker}. Other characters present: {others}. Write up to 3 short labeled turns using only these characters, let them react to each other, and stop. Do not speak for the user."
    return f"You are in a multi-character group chat. Current speaker: {speaker}. Other characters present: {others}. Speak only as the current speaker. Do not write dialogue or actions for the user or other characters."


def advance_group_turn(db: sqlite3.Connection, chat_id: str, session_id: str, operation_id: int | str | None = None, commit: bool = True) -> None:
    state = group_state(db, chat_id, session_id)
    if not state["enabled"] or len(state["members"]) < 2:
        return
    state["forced_speaker"] = ""
    if state.get("mode") != "manual":
        state["turn_index"] = int(state["turn_index"]) + 1
    save_group_state(db, chat_id, session_id, state, operation_id, commit=commit)


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
        send_text(token, chat_id, f"Group chat: {'on' if state['enabled'] else 'off'}\nMode: {state['mode']}\nMembers: {', '.join(labels) if labels else 'none'}\nCurrent speaker: {current}\nUse /group add <character>, /group speak <character>, /group mode <round_robin|contextual|manual|autonomous>, or /group off.")
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
        if requested_mode not in {"round_robin", "contextual", "manual", "autonomous"}:
            send_text(token, chat_id, "Use /group mode round_robin, /group mode contextual, /group mode manual, or /group mode autonomous.")
            return
        state["mode"] = requested_mode
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
    send_text(token, chat_id, "Use /group status, /group add <character>, /group remove <character>, /group speak <character>, /group mode <round_robin|contextual|manual|autonomous>, /group on, /group off, or /group next.")


def handle_summary_command(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str]) -> None:
    send_typing(token, chat_id)
    summary = generate_session_summary(db, chat_id, session, force=True)
    if summary:
        send_text(token, chat_id, "Session summary updated:\n\n" + summary)
    else:
        send_text(token, chat_id, "No chat messages are available to summarize.")



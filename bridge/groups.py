def group_state(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict[str, object]:
    row = db.execute("SELECT title,enabled,turn_index,mode,forced_speaker,members_json,turn_user_id,turn_users_json FROM group_sessions WHERE chat_id=? AND session_id=?", (chat_id, session_id)).fetchone()
    if not row:
        return {"title": "Group chat", "enabled": False, "turn_index": 0, "mode": "round_robin", "forced_speaker": "", "members": [], "turn_user_id": "", "turn_users": []}
    try:
        members = json.loads(row[5])
    except (TypeError, json.JSONDecodeError):
        members = []
    try:
        turn_users = json.loads(row[7])
    except (TypeError, json.JSONDecodeError):
        turn_users = []
    return {"title": row[0], "enabled": bool(row[1]), "turn_index": int(row[2]), "mode": str(row[3] or "round_robin"), "forced_speaker": str(row[4] or ""), "members": [str(item) for item in members if isinstance(item, str)], "turn_user_id": str(row[6] or ""), "turn_users": [str(item) for item in turn_users if isinstance(item, str)]}


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


def group_user_turn_allowed(db: sqlite3.Connection, chat_id: str, session_id: str, sender_id: str) -> bool:
    """Allow manual-mode messages only from the current user turn owner."""
    state = group_state(db, chat_id, session_id)
    if not state["enabled"] or state["mode"] != "manual":
        return True
    sender_id = str(sender_id or "")
    if not sender_id:
        return False
    users = list(state.get("turn_users") or [])
    changed = False
    if sender_id not in users:
        users.append(sender_id)
        changed = True
    if not state.get("turn_user_id"):
        state["turn_user_id"] = sender_id
        changed = True
    state["turn_users"] = users
    if changed:
        save_group_state(db, chat_id, session_id, state)
    return state["turn_user_id"] == sender_id


def claim_group_user_turn(db: sqlite3.Connection, chat_id: str, session_id: str, sender_id: str) -> bool:
    """Claim an unassigned manual user turn or confirm its current owner."""
    state = group_state(db, chat_id, session_id)
    sender_id = str(sender_id or "")
    if not sender_id or not state["enabled"] or state["mode"] != "manual":
        return False
    if state.get("turn_user_id") and state["turn_user_id"] != sender_id:
        return False
    users = list(state.get("turn_users") or [])
    if sender_id not in users:
        users.append(sender_id)
    state["turn_users"] = users
    state["turn_user_id"] = sender_id
    save_group_state(db, chat_id, session_id, state)
    return True


def pass_group_user_turn(db: sqlite3.Connection, chat_id: str, session_id: str, sender_id: str) -> bool:
    """Pass a manual user turn to the next known group participant."""
    state = group_state(db, chat_id, session_id)
    sender_id = str(sender_id or "")
    if not sender_id or state.get("mode") != "manual" or state.get("turn_user_id") != sender_id:
        return False
    users = list(state.get("turn_users") or [])
    if len(users) <= 1:
        state["turn_user_id"] = ""
    else:
        position = users.index(sender_id) if sender_id in users else -1
        state["turn_user_id"] = users[(position + 1) % len(users)]
    save_group_state(db, chat_id, session_id, state)
    return True


def group_setup_state(db: sqlite3.Connection, chat_id: str, session_id: str) -> dict | None:
    """Load the short-lived topic-local New group session wizard state."""
    raw = get_meta(db, f"group_setup:{chat_id}", "")
    try:
        state = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        state = {}
    if not state or state.get("session_id") != session_id or float(state.get("expires_at", 0) or 0) < time.time():
        if state:
            set_meta(db, f"group_setup:{chat_id}", "")
        return None
    return state


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
    close_panel_message(token, chat_id, callback)
    send_world_menu(token, chat_id, "", None, 0)
    return True


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
        remove_inline_keyboard(token, {"message": message})
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


def save_group_state(db: sqlite3.Connection, chat_id: str, session_id: str, state: dict[str, object], operation_id: int | str | None = None, commit: bool = True) -> bool:
    if not begin_operation(db, operation_id, "group_state"):
        return False
    db.execute("INSERT OR REPLACE INTO group_sessions(chat_id,session_id,title,enabled,turn_index,mode,forced_speaker,members_json,turn_user_id,turn_users_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (chat_id, session_id, str(state.get("title") or "Group chat"), int(bool(state.get("enabled"))), int(state.get("turn_index") or 0), str(state.get("mode") or "round_robin"), str(state.get("forced_speaker") or ""), json.dumps(state.get("members") or [], ensure_ascii=False), str(state.get("turn_user_id") or ""), json.dumps(state.get("turn_users") or [], ensure_ascii=False), time.time()))
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


def parse_group_director_decision(raw: str, member_files: list[str]) -> tuple[str, str] | None:
    """Parse a bounded invisible-director decision into a known member and note."""
    text = str(raw or "").strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    try:
        payload = json.loads(match.group(0) if match else text)
    except (TypeError, json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(payload, dict):
        return None
    speaker_value = str(payload.get("speaker") or "").strip().casefold()
    direction = re.sub(r"\s+", " ", str(payload.get("direction") or "").strip())[:500]
    aliases = {}
    for filename in member_files:
        aliases[filename.casefold()] = filename
        aliases[Path(filename).stem.casefold()] = filename
        try:
            aliases[str(card_fields_from_file(filename)["name"]).casefold()] = filename
        except Exception:
            pass
    speaker_file = aliases.get(speaker_value)
    return (speaker_file, direction) if speaker_file else None


def group_director_plan(
    db: sqlite3.Connection,
    api_key: str,
    chat_id: str,
    session: dict[str, str],
    user_text: str,
) -> tuple[str, dict[str, object], str] | None:
    """Use an invisible bounded model call to choose the next speaker and pacing note."""
    state = group_state(db, chat_id, session["session_id"])
    members = [name for name in state["members"] if safe_character_path(name)]
    if not state["enabled"] or state.get("mode") != "director" or len(members) < 2:
        return None
    forced = str(state.get("forced_speaker") or "")
    if forced in members:
        return forced, state, ""

    labels = group_member_labels(members)
    recent = db.execute(
        "SELECT role,content FROM messages WHERE chat_id=? AND session_id=? "
        "ORDER BY created_at DESC,rowid DESC LIMIT 12",
        (chat_id, session["session_id"]),
    ).fetchall()
    recent = list(reversed(recent))
    transcript = "\n".join(f"{role}: {str(content)[:1200]}" for role, content in recent)[-9000:]
    director_messages = [
        {
            "role": "system",
            "content": (
                "You are an invisible scene director for a multi-character roleplay. "
                "Choose exactly one next speaker from the allowed names and provide one short pacing/scene direction. "
                "Do not write dialogue. Do not speak for the user. Output strict JSON only: "
                '{"speaker":"NAME","direction":"short direction"}.'
            ),
        },
        {
            "role": "user",
            "content": (
                "Allowed speakers: " + ", ".join(labels) + "\n"
                "Recent transcript:\n" + (transcript or "(empty)") + "\n"
                "Latest user turn:\n" + str(user_text)[:4000]
            ),
        },
    ]
    settings = get_generation_settings(db, chat_id, session["session_id"])
    settings.update({"temperature": 0.1, "max_tokens": 180, "reasoning_budget": 0, "stop_sequences": ""})
    try:
        raw = generate_text(
            api_key,
            session.get("model_id") or DEFAULT_MODEL,
            director_messages,
            session_id=f"group-director:{chat_id}:{session['session_id']}",
            settings=settings,
            force_non_stream=True,
        )
        decision = parse_group_director_decision(raw, members)
    except Exception:
        logging.warning("Group director decision failed; falling back to round robin", exc_info=True)
        decision = None
    if decision:
        return decision[0], state, decision[1]
    index = int(state["turn_index"]) % len(members)
    return members[index], state, ""


def group_prompt_context(db: sqlite3.Connection, chat_id: str, session: dict[str, str], speaker_file: str, director_instruction: str = "") -> str:
    state = group_state(db, chat_id, session["session_id"])
    labels = group_member_labels([name for name in state["members"] if safe_character_path(name)])
    speaker = card_fields_from_file(speaker_file)["name"]
    others = ", ".join(label for label in labels if label != speaker) or "none"
    if state.get("mode") == "autonomous":
        return f"You are in a bounded autonomous multi-character scene. Current lead speaker: {speaker}. Other characters present: {others}. Write up to 3 short labeled turns using only these characters, let them react to each other, and stop. Do not speak for the user."
    base = f"You are in a multi-character group chat. Current speaker: {speaker}. Other characters present: {others}. Speak only as the current speaker. Do not write dialogue or actions for the user or other characters."
    if state.get("mode") == "director" and director_instruction:
        base += "\nInvisible director guidance: " + director_instruction[:500] + " Treat this only as pacing/scene guidance; do not mention the director."
    return base


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

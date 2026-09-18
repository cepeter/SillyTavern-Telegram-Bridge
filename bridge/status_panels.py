"""Read-only status and prompt inspection panels."""

import json


def status_text(db, chat_id, session, fields, current_model, current_persona):
    count = db.execute(
        "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?",
        (chat_id, session["session_id"]),
    ).fetchone()[0]
    worlds = active_world_files(session["world_file"])
    world = ", ".join(Path(name).stem for name in worlds) if worlds else "off"
    persona = persona_name(current_persona) if current_persona else "off"
    note_state = "on" if session["author_note"] else "off"
    generation = get_generation_settings(db, chat_id, session["session_id"])
    summary, covered_until = get_session_summary(db, chat_id, session["session_id"])
    summary_state = f"on (through message {covered_until})" if summary else "off"
    rag_docs = data_bank_documents(db, chat_id)
    group = group_state(db, chat_id, session["session_id"])
    group_labels = group_member_labels(group["members"])
    group_state_text = f"{'on' if group['enabled'] else 'off'} ({', '.join(group_labels) if group_labels else 'none'})"
    expression_mode = get_meta(db, expression_mode_key(chat_id, session["session_id"]), "off")
    utility_model = task_model_for_session(db, chat_id, session, "utility")
    return (
        f"Character: {fields.get('name') or 'unknown'}\n"
        f"Session: {session.get('title') or session['session_id']} ({session['session_id']})\n"
        f"Stored messages: {count}\nModel: {current_model}\nUtility model: {utility_model}\n"
        f"Response language: {response_language_label(session.get('response_language') or 'auto')}\n"
        f"Persona: {persona}\nWorld Info: {world}\n"
        f"System Prompt: {system_prompt_label(session.get('system_prompt'))}\n"
        f"Author's Note: {note_state}\nExpressions: {expression_mode}\n"
        f"Summary: {summary_state}\nHindsight: {memory_mode(db, chat_id)} ({memory_scope(db, chat_id)})\n"
        f"Data Bank RAG: {rag_mode(db, chat_id)} ({len(rag_docs)} documents)\n"
        f"Group chat: {group_state_text}\n"
        f"Generation: temperature={generation['temperature']}, max_tokens={generation['max_tokens']}, top_p={generation['top_p']}"
    )


def send_status_menu(token, chat_id, db, session, fields, current_model, current_persona, message_id=None):
    text = "Session status\n\n" + status_text(db, chat_id, session, fields, current_model, current_persona)
    markup = {
        "inline_keyboard": [
            [{"text": "🎭 Character", "callback_data": "status:character"}, {"text": "🗂️ Session", "callback_data": "status:session"}],
            [{"text": "⚙️ Generation", "callback_data": "status:generation"}, {"text": "🧠 Memory", "callback_data": "status:memory"}],
            [{"text": "📚 Data Bank", "callback_data": "status:databank"}, {"text": "👥 Group", "callback_data": "status:group"}],
            [{"text": "🔎 Prompt inspector", "callback_data": "prompt:menu"}],
            [{"text": "❌ Close", "callback_data": "status:close"}],
        ]
    }
    send_panel_message(token, chat_id, text, markup, message_id)


def prompt_panel_text(db, chat_id, session, fields, section="overview"):
    if section == "budget":
        return (
            f"Prompt budget\nContext input budget: ~{context_input_budget_tokens()} tokens\n"
            f"History candidates: {context_history_candidate_limit()} messages\n"
            f"Session summary: {len(get_session_summary(db, chat_id, session['session_id'])[0])} chars"
        )
    if section == "memory":
        docs = data_bank_documents(db, chat_id)
        return (
            f"Prompt memory and retrieval\nHindsight: {memory_mode(db, chat_id)} / {memory_scope(db, chat_id)}\n"
            f"Data Bank: {rag_mode(db, chat_id)} / {len(docs)} documents"
        )
    if section == "group":
        group = group_state(db, chat_id, session["session_id"])
        return f"Prompt group context\nEnabled: {'on' if group['enabled'] else 'off'}\nMode: {group['mode']}\nMembers: {len(group['members'])}"
    return prompt_diagnostics(db, chat_id, session, fields)


def send_prompt_menu(token, chat_id, db, session, fields, message_id=None, section="overview"):
    labels = {
        "overview": "Prompt inspector",
        "budget": "Prompt budget",
        "memory": "Memory and retrieval",
        "group": "Group context",
    }
    markup = {
        "inline_keyboard": [
            [{"text": "📏 Budget", "callback_data": "prompt:budget"}, {"text": "🧠 Memory / RAG", "callback_data": "prompt:memory"}],
            [{"text": "👥 Group", "callback_data": "prompt:group"}],
            [{"text": "⬅️ Status", "callback_data": "prompt:status"}, {"text": "❌ Close", "callback_data": "prompt:close"}],
        ]
    }
    send_panel_message(token, chat_id, labels.get(section, labels["overview"]) + "\n\n" + prompt_panel_text(db, chat_id, session, fields, section), markup, message_id)


def handle_status_panel_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
    message_id = message.get("message_id")
    if data == "status:close" or data == "prompt:close":
        answer_callback(token, str(callback.get("id", "")), "Closed")
        close_panel_message(token, chat_id, callback)
        return True
    if data == "status:back":
        send_status_menu(token, chat_id, db, session, card_fields_from_file(session["character_file"]), session.get("model_id") or DEFAULT_MODEL, session.get("persona_id") or "", message_id)
        return True
    if data == "status:character":
        send_character_menu(token, chat_id, session["character_file"], message_id)
        return True
    if data == "status:session":
        send_session_menu(token, chat_id, list_sessions(db, chat_id), session_id, message_id)
        return True
    if data == "status:generation":
        send_settings_menu(token, chat_id, db, session_id, message_id)
        return True
    if data == "status:memory":
        send_memory_menu(token, chat_id, db, message_id)
        return True
    if data == "status:databank":
        send_databank_menu(token, chat_id, db, message_id)
        return True
    if data == "status:group":
        send_group_menu(db, token, chat_id, session, message_id)
        return True
    if data == "status:prompt" or data == "prompt:menu":
        send_prompt_menu(token, chat_id, db, session, card_fields_from_file(session["character_file"]), message_id)
        return True
    if data.startswith("prompt:"):
        if data == "prompt:status":
            send_status_menu(token, chat_id, db, session, card_fields_from_file(session["character_file"]), session.get("model_id") or DEFAULT_MODEL, session.get("persona_id") or "", message_id)
        elif data.rsplit(":", 1)[1] in {"budget", "memory", "group"}:
            send_prompt_menu(token, chat_id, db, session, card_fields_from_file(session["character_file"]), message_id, data.rsplit(":", 1)[1])
        return True
    if data.startswith(("scene:", "taskmodel:", "goal:", "curated:", "summary:")):
        return handle_feature_panel_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id)
    return False


def send_scene_menu(token, chat_id, db, session, message_id=None):
    state, covered = get_scene_state(db, chat_id, session["session_id"])
    body = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) if state else "No structured scene state has been established yet."
    markup = {"inline_keyboard": [[{"text": "🔄 Refresh", "callback_data": "scene:refresh"}, {"text": "🧹 Clear", "callback_data": "scene:clear"}], [{"text": "⬅️ Status", "callback_data": "scene:status"}, {"text": "❌ Close", "callback_data": "scene:close"}]]}
    send_panel_message(token, chat_id, f"Scene state (through message row {covered})\n\n{body}", markup, message_id)


def send_director_goal_menu(token, chat_id, db, session, message_id=None):
    goal = get_director_goal(db, chat_id, session["session_id"])
    text = "Director objective\n\n" + (goal or "No hidden objective is set.")
    markup = {"inline_keyboard": [[{"text": "✏️ Set objective", "callback_data": "goal:set"}], [{"text": "🧹 Clear", "callback_data": "goal:clear"}, {"text": "❌ Close", "callback_data": "goal:close"}]]}
    send_panel_message(token, chat_id, text, markup, message_id)


def send_task_model_menu(token, chat_id, db, session, message_id=None, page=0):
    configured = get_meta(db, task_model_key(chat_id, session["session_id"], "utility"), "").strip()
    current = task_model_for_session(db, chat_id, session, "utility")
    options = [("", "Follow main model")]
    for provider_id, (_name, models, supported) in get_model_groups().items():
        if supported:
            options.extend((model, f"{provider_id} / {model}") for model in models)
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for model, label in page_options:
        mark = "✅ " if (not model and not configured) or model == configured else ""
        token = dynamic_callback_token("taskmodel", model or "main", chat_id)
        rows.append([{"text": mark + panel_label(label), "callback_data": "taskmodel:model:" + token}])
    if current_page > 0 or current_page < total_pages - 1:
        navigation = []
        if current_page > 0:
            navigation.append({"text": "⬅️ Previous", "callback_data": f"taskmodel:page:{current_page - 1}"})
        if current_page < total_pages - 1:
            navigation.append({"text": "Next ➡️", "callback_data": f"taskmodel:page:{current_page + 1}"})
        rows.append(navigation)
    rows.append([{"text": "❌ Close", "callback_data": "taskmodel:close"}])
    send_panel_message(token, chat_id, f"Utility task model\nConfigured: {configured or 'follow main model'}\nEffective: {current}\n\nChoose a model (page {current_page + 1}/{total_pages}):", {"inline_keyboard": rows}, message_id)


def handle_feature_panel_callback(db, token, callback, answer_callback, data, chat_id, message, session, session_id, operation_id):
    message_id = message.get("message_id")
    if data.startswith("summary:"):
        action = data.split(":", 1)[1]
        if action == "cancel" or action == "close":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            close_panel_message(token, chat_id, callback)
        elif action == "confirm":
            answer_callback(token, str(callback.get("id", "")), "Summarizing")
            handle_summary_command(db, token, chat_id, session)
        return True
    if data.startswith("scene:"):
        action = data.split(":", 1)[1]
        if action == "close":
            answer_callback(token, str(callback.get("id", "")), "Closed")
            close_panel_message(token, chat_id, callback)
        elif action == "status":
            send_status_menu(token, chat_id, db, session, card_fields_from_file(session["character_file"]), session.get("model_id") or DEFAULT_MODEL, session.get("persona_id") or "", message_id)
        elif action == "refresh":
            send_typing(token, chat_id)
            refresh_scene_state_now(db, "", chat_id, session, str(card_fields_from_file(session["character_file"]).get("name") or "unknown"))
            send_scene_menu(token, chat_id, db, session, message_id)
        elif action == "clear":
            send_panel_message(token, chat_id, "Clear the stored structured scene state?", {"inline_keyboard": [[{"text": "✅ Confirm clear", "callback_data": "scene:clear_confirm"}], [{"text": "⬅️ Back", "callback_data": "scene:status"}, {"text": "❌ Close", "callback_data": "scene:close"}]]}, message_id)
        elif action == "clear_confirm":
            clear_scene_state(db, chat_id, session_id)
            answer_callback(token, str(callback.get("id", "")), "Scene cleared")
            send_scene_menu(token, chat_id, db, session, message_id)
        return True
    if data.startswith("goal:"):
        action = data.split(":", 1)[1]
        if action == "close":
            answer_callback(token, str(callback.get("id", "")), "Closed")
            close_panel_message(token, chat_id, callback)
        elif action == "set":
            start_text_action_input(db, token, chat_id, session_id, "director_goal", "Send the hidden Director objective (up to 1,200 characters).", callback)
        elif action == "clear":
            set_director_goal(db, chat_id, session_id, "")
            answer_callback(token, str(callback.get("id", "")), "Objective cleared")
            send_director_goal_menu(token, chat_id, db, session, message_id)
        return True
    if data.startswith("taskmodel:"):
        action = data.split(":", 1)[1]
        if action == "close":
            answer_callback(token, str(callback.get("id", "")), "Closed")
            close_panel_message(token, chat_id, callback)
        elif action.startswith("page:"):
            send_task_model_menu(token, chat_id, db, session, message_id, int(action.split(":", 1)[1]))
        elif action.startswith("model:"):
            value = resolve_dynamic_callback_token(action.split(":", 1)[1], "taskmodel", chat_id)
            if value is not None:
                set_task_model(db, chat_id, session_id, "" if value == "main" else value, "utility")
                answer_callback(token, str(callback.get("id", "")), "Utility model updated")
                send_task_model_menu(token, chat_id, db, session, message_id)
        return True
    if data.startswith("curated:"):
        action = data.split(":", 1)[1]
        if action == "close":
            answer_callback(token, str(callback.get("id", "")), "Closed")
            close_panel_message(token, chat_id, callback)
        elif action == "refresh":
            if memory_mode(db, chat_id) != "on":
                send_text(token, chat_id, "Hindsight memory is off. Enable /memory first.")
            else:
                send_typing(token, chat_id)
                curate_memory_now(db, "", chat_id, session, str(card_fields_from_file(session["character_file"]).get("name") or "unknown"))
                send_curated_memory_menu(token, chat_id, db, session, message_id)
        elif action == "back":
            send_memory_menu(token, chat_id, db, message_id)
        return True
    return False


def send_curated_memory_menu(token, chat_id, db, session, message_id=None):
    text = curated_memory_text(db, chat_id, session["session_id"])
    markup = {"inline_keyboard": [[{"text": "🔄 Refresh", "callback_data": "curated:refresh"}], [{"text": "⬅️ Memory", "callback_data": "curated:back"}, {"text": "❌ Close", "callback_data": "curated:close"}]]}
    send_panel_message(token, chat_id, "Curated memory\n\n" + (text or "No curated durable memories yet."), markup, message_id)


def send_summary_menu(token, chat_id, db, session, message_id=None):
    summary, covered = get_session_summary(db, chat_id, session["session_id"])
    state = f"Existing summary: {len(summary)} chars" if summary else "No summary exists yet."
    text = "Session summary\n\n" + state + f"\nCovered through message row: {covered or 'none'}\n\nRegenerating uses the utility model and may take a while."
    markup = {"inline_keyboard": [[{"text": "✅ Regenerate summary", "callback_data": "summary:confirm"}], [{"text": "❌ Cancel", "callback_data": "summary:cancel"}]]}
    send_panel_message(token, chat_id, text, markup, message_id)

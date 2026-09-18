HELP_CATEGORIES = {
    "basic": [
        ("/start", "Show the character greeting when Persona, World Info, and System Prompt are all enabled; otherwise show what's off and how to fix it."),

        ("/help", "Open this guide. Use /help <command> to jump straight to one command."),
        ("/status", "Show everything about the current session — card, model, memory, RAG, group, and generation state."),
        ("/new", "Name and create a fresh isolated session, then switch to it."),
        ("/reset", "Open a confirmation panel to clear only the active session and its Hindsight memory. Other sessions stay untouched."),
        ("/session", "Switch between sessions, create new ones, or delete inactive ones — deletion removes session data and its Hindsight documents."),
        ("/sync", "Open session-scoped Live API Sync controls; synchronization uses the local SillyTavern API only, not chat files or JSONL transfer."),
        ("/update", "Check the latest GitHub release. If you're already current, nothing happens. Otherwise a confirmation panel lets you update and restart."),
    ],
    "characters": [
        ("/providers", "Open the provider and model catalog. Adapter-enabled entries can generate; catalog-only entries are view-only."),
        ("/providers refresh", "Open the provider panel and refresh discoverable model catalogs."),
        ("/providers health", "Open the provider panel and run health checks. Providers without /models use a bounded streaming chat probe."),
        ("/character", "Open the character panel — pick a card, view info, delete safely, or get upload guidance."),
        ("/persona", "Choose, create, edit, or disable a Persona. Delete only targets inactive, unreferenced ones."),
        ("/world", "Open World Info selection — activate or disable one or more lorebooks."),
        ("/note", "Open the Author's Note panel. Off clears it; User input waits for your next message."),
        ("/systemprompt", "Open the native TXT System Prompt picker. The prompt body stays private."),
        ("/language", "Choose the language for model replies in this session — Auto follows you, or pick a fixed language."),
        ("/expression", "Off by default. Choose automatic sprite detection or pick a native sprite from the active card."),
        ("/imagine", "Opt-in image generation. Opens a 1–4,000 character prompt panel, or use /imagine <prompt> when an image provider is configured."),
    ],
    "generation": [
        ("/settings", "Open this session's generation panel — pick a reasoning level or set your own temperature, tokens, and sampling values."),
        ("/taskmodel", "Route utility tasks such as session summarization to a per-session model, or follow the main model."),
        ("/stream on|off", "Toggle streaming preview on or off."),
        ("/preset", "Open the preset panel — apply, save, or delete generation setting presets."),
        ("/macro", "Open a panel, then send one message to preview supported SillyTavern macros"),
        ("/stscript", "Open the safe STscript panel for Note or Reset — only allowlisted actions, no arbitrary scripts."),
        ("/regen", "Generate a new response variant for the latest user turn."),
        ("/swipe", "Browse stored response variants and keep the one you like."),
        ("/branch", "Open the response branch selector for the active session."),
        ("/continue", "Continue the latest assistant response from where it stopped."),
        ("/edit", "Open a panel, then send replacement text for the latest user turn"),
        ("/retry", "Retry the latest failed character response — no duplicate turns."),
        ("/prompt", "Inspect prompt sections and smart context budget — history candidates, memory, RAG, and World Info — without the full prompt."),
    ],
    "memory_rag": [
        ("/memory", "Open Hindsight memory controls. Recall is always limited to the active session."),
        ("/remember", "Open a panel, then send one explicit long-term fact to store in memory."),
        ("/summarize", "Regenerate the active session's summary from its stored conversation."),
        ("/databank", "Open Data Bank RAG controls. Same-name uploads create versions; use versions/activate to inspect or roll back."),
    ],
    "voice_group": [
        ("/voice on|off", "Toggle automatic voice replies. Only dialogue in straight double quotes gets synthesized — narration stays silent."),
        ("/voice_input on|off", "Open transcription, STT model, and language controls."),
        ("/voice_input language", "Open the STT language panel — Auto, a fixed code, or User input."),
        ("/group", "Open Forum Topic group controls, including invisible Director mode for model-selected speaker and pacing guidance."),
    ],
}


def _system_prompt_key(current: str) -> str:
    if current and current in dict(system_prompt_choices()):
        return current
    for key, _name in system_prompt_choices():
        if get_system_prompt_choice(key) == current:
            return key
    return ""


def system_prompt_menu_markup(current: str, page: int = 0) -> dict:
    current_key = _system_prompt_key(current)
    options = system_prompt_choices()
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for key, name in page_options:
        mark = "✅ " if key == current_key else ""
        rows.append([{"text": mark + name, "callback_data": "systemprompt:" + system_prompt_callback_token(key)}])
    navigation = panel_navigation("systemprompt", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "🚫 Off", "callback_data": "systemprompt:off"}, {"text": "❌ Close", "callback_data": "systemprompt:cancel"}])
    return {"inline_keyboard": rows}


def send_system_prompt_menu(token: str, chat_id: str, current: str, message_id: int | None = None, page: int = 0) -> None:
    current_key = _system_prompt_key(current)
    labels = dict(system_prompt_choices())
    current_label = labels.get(current_key, "off")
    text = f"System Prompt choice\nCurrent: {current_label}\nChoose a TXT prompt:"
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": system_prompt_menu_markup(current, page)}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_help_menu(token: str, chat_id: str, category: str | None = None, message_id: int | None = None, command_index: int | None = None, page: int = 0) -> None:
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": help_text(category, command_index, page), "reply_markup": help_markup(category, command_index, page)}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_settings_menu(token: str, chat_id: str, db: sqlite3.Connection, session_id: str, message_id: int | None = None) -> None:
    settings = get_generation_settings(db, chat_id, session_id)
    levels = list(REASONING_LEVELS.items())
    current = int(settings.get("reasoning_budget", 0))
    current_label = next((label.title() for label, budget in levels if budget == current), "Custom")
    rows = [[{"text": ("✅ " if budget == current else "") + f"Reasoning: {label.title()}", "callback_data": f"enum:settings:reasoning:{label}"}] for label, budget in levels]
    rows.append([{"text": "✏️ Custom reasoning budget", "callback_data": "enum:settings:input:reasoning_budget"}])
    rows.append([{"text": "✏️ Temperature", "callback_data": "enum:settings:input:temperature"}, {"text": "✏️ Max tokens", "callback_data": "enum:settings:input:max_tokens"}])
    rows.append([{"text": "✏️ Top P", "callback_data": "enum:settings:input:top_p"}, {"text": "✏️ Frequency penalty", "callback_data": "enum:settings:input:frequency_penalty"}])
    rows.append([{"text": "✏️ Presence penalty", "callback_data": "enum:settings:input:presence_penalty"}, {"text": "✏️ Stop sequences", "callback_data": "enum:settings:input:stop_sequences"}])
    rows.append([{"text": "↩️ Reset all settings", "callback_data": "enum:settings:reset"}, {"text": "❌ Close", "callback_data": "enum:close"}])
    stop_sequences = settings.get("stop_sequences") or ""
    if isinstance(stop_sequences, (list, tuple)):
        stop_label = ", ".join(str(value) for value in stop_sequences) if stop_sequences else "none"
    else:
        stop_label = str(stop_sequences) or "none"
    current_values = (f"temperature={settings['temperature']}, max_tokens={settings['max_tokens']}, "
                      f"top_p={settings['top_p']}, frequency_penalty={settings['frequency_penalty']}, "
                      f"presence_penalty={settings['presence_penalty']}, stop_sequences={stop_label}")
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": f"Generation settings — current session\nActive reasoning: {current_label} ({current} budget)\nCurrent values: {current_values}\n\nTap a reasoning level below to apply it.\nTap a field to enter its value in the next message. Send /cancel to leave it unchanged.\nFields: temperature, max_tokens, top_p, frequency_penalty, presence_penalty, stop_sequences.", "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_stream_menu(token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None) -> None:
    current = get_meta(db, f"stream_mode:{chat_id}", "on")
    rows = [[{"text": ("✅ " if current == "on" else "") + "Streaming on", "callback_data": "enum:stream:on"}, {"text": ("✅ " if current == "off" else "") + "Streaming off", "callback_data": "enum:stream:off"}], [{"text": "❌ Close", "callback_data": "enum:close"}]]
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": f"Live response streaming: {current}", "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_voice_menu(token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None) -> None:
    current = get_meta(db, f"voice_mode:{chat_id}", "off")
    rows = [[{"text": ("✅ " if current == "tts" else "") + "Voice replies on", "callback_data": "enum:voice:on"}, {"text": ("✅ " if current != "tts" else "") + "Voice replies off", "callback_data": "enum:voice:off"}], [{"text": "❌ Close", "callback_data": "enum:close"}]]
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": f"Automatic voice replies: {'on' if current == 'tts' else 'off'}", "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_voice_input_menu(token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None) -> None:
    mode = get_meta(db, f"stt_mode:{chat_id}", "on")
    model = get_meta(db, f"stt_model:{chat_id}", STT_DEFAULT_MODEL)
    language = normalize_stt_language(get_meta(db, f"stt_language:{chat_id}", "auto"))
    rows = [[{"text": ("✅ " if mode == "on" else "") + "Transcription on", "callback_data": "enum:stt:on"}, {"text": ("✅ " if mode == "off" else "") + "Transcription off", "callback_data": "enum:stt:off"}], [{"text": f"Model: {model}", "callback_data": "enum:stt:model"}], [{"text": f"Language: {stt_language_label(language)}", "callback_data": "enum:stt:language"}], [{"text": "❌ Close", "callback_data": "enum:close"}]]
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": f"Voice input transcription: {mode}\nModel: {model}\nLanguage: {stt_language_label(language)}", "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_stt_language_menu(token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None, page: int = 0) -> None:
    current = normalize_stt_language(get_meta(db, f"stt_language:{chat_id}", "auto"))
    options = list(RESPONSE_LANGUAGES)
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for code, label in page_options:
        mark = "✅ " if code == current else ""
        rows.append([{"text": mark + label, "callback_data": "enum:sttlanguage:" + code}])
    navigation = panel_navigation("enum:sttlanguagepage", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "✏️ User input", "callback_data": "enum:stt:language_input"}])
    rows.append([{"text": "⬅️ Back", "callback_data": "enum:stt:back"}, {"text": "❌ Close", "callback_data": "enum:close"}])
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": f"Choose voice input language (page {current_page + 1}/{total_pages}). Auto detects the language; User input accepts a custom 2–8 letter code.", "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_stt_model_menu(token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None) -> None:
    current = get_meta(db, f"stt_model:{chat_id}", STT_DEFAULT_MODEL)
    rows = [[{"text": ("✅ " if model == current else "") + model, "callback_data": "enum:sttmodel:" + model}] for model in ("tiny", "base", "small")]
    rows.append([{"text": "⬅️ Back", "callback_data": "enum:stt:back"}, {"text": "❌ Close", "callback_data": "enum:close"}])
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": "Choose STT model:", "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_memory_menu(token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None) -> None:
    mode = memory_mode(db, chat_id)
    rows = [[{"text": ("✅ " if mode == "on" else "") + "Memory on", "callback_data": "enum:memory:on"}, {"text": ("✅ " if mode == "off" else "") + "Memory off", "callback_data": "enum:memory:off"}], [{"text": "🔎 Search memories", "callback_data": "enum:memory:search"}], [{"text": "❌ Close", "callback_data": "enum:close"}]]
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": f"Hindsight memory: {mode}\nScope: active session only (fixed)", "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_preset_menu(token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None, page: int = 0) -> None:
    options = preset_names(db, chat_id)
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [[{"text": panel_label(name), "callback_data": "enum:presetuse:" + dynamic_callback_token("preset", name, chat_id)}] for name in page_options]
    if total_pages > 1:
        navigation = []
        if current_page > 0:
            navigation.append({"text": "⬅️ Previous", "callback_data": f"enum:presetpage:{current_page - 1}"})
        if current_page < total_pages - 1:
            navigation.append({"text": "Next ➡️", "callback_data": f"enum:presetpage:{current_page + 1}"})
        rows.append(navigation)
    rows.append([{"text": "💾 Save preset", "callback_data": "enum:preset:save"}])
    rows.append([{"text": "🗑️ Delete preset", "callback_data": "enum:presetdelete"}])
    rows.append([{"text": "❌ Close", "callback_data": "enum:close"}])
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": f"Choose a preset to apply (page {current_page + 1}/{total_pages}). Use Save preset for the two-step name input." , "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_preset_delete_menu(token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None, page: int = 0) -> None:
    options = preset_names(db, chat_id)
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [[{"text": panel_label(name), "callback_data": "enum:presetdel:" + dynamic_callback_token("preset", name, chat_id)}] for name in page_options]
    if total_pages > 1:
        navigation = []
        if current_page > 0:
            navigation.append({"text": "⬅️ Previous", "callback_data": f"enum:presetdeletepage:{current_page - 1}"})
        if current_page < total_pages - 1:
            navigation.append({"text": "Next ➡️", "callback_data": f"enum:presetdeletepage:{current_page + 1}"})
        rows.append(navigation)
    rows.append([{"text": "⬅️ Back", "callback_data": "enum:preset:back"}, {"text": "❌ Close", "callback_data": "enum:close"}])
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": "Choose a preset to delete:", "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_databank_menu(token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None) -> None:
    mode = rag_mode(db, chat_id)
    docs = data_bank_documents(db, chat_id)
    total_chunks, indexed_chunks = rag_embedding_coverage(db, chat_id)
    rows = [[{"text": ("✅ " if mode == "on" else "") + "RAG on", "callback_data": "enum:rag:on"}, {"text": ("✅ " if mode == "off" else "") + "RAG off", "callback_data": "enum:rag:off"}], [{"text": "List documents", "callback_data": "enum:rag:list"}, {"text": "🔎 Search", "callback_data": "enum:rag:search"}], [{"text": "Remove document", "callback_data": "enum:rag:remove"}], [{"text": "Reindex embeddings", "callback_data": "enum:rag:reindex"}], [{"text": "❌ Close", "callback_data": "enum:close"}]]
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": f"Data Bank RAG: {mode}\nDocuments: {len(docs)}\nEmbedding coverage: {indexed_chunks}/{total_chunks} chunks", "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_databank_remove_menu(token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None, page: int = 0) -> None:
    docs = data_bank_documents(db, chat_id)
    options = [str(row[1]) for row in docs]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [[{"text": panel_label(filename), "callback_data": "enum:ragremove:" + dynamic_callback_token("rag_document", filename, chat_id)}] for filename in page_options]
    if total_pages > 1:
        navigation = []
        if current_page > 0:
            navigation.append({"text": "⬅️ Previous", "callback_data": f"enum:ragremovepage:{current_page - 1}"})
        if current_page < total_pages - 1:
            navigation.append({"text": "Next ➡️", "callback_data": f"enum:ragremovepage:{current_page + 1}"})
        rows.append(navigation)
    rows.append([{"text": "⬅️ Back", "callback_data": "enum:rag:back"}, {"text": "❌ Close", "callback_data": "enum:close"}])
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": f"Choose a document to remove (page {current_page + 1}/{total_pages}):", "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def handle_enum_callback(db: sqlite3.Connection, token: str, chat_id: str, session: dict[str, str], data: str, message: dict) -> None:
    message_id = message.get("message_id")
    if data == "enum:close":
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(token, chat_id, {"message": message})
        return
    if data == "enum:stscript:cancel":
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(token, chat_id, {"message": message})
        return
    if data == "enum:stscript:reset":
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(token, chat_id, {"message": message})
        send_reset_confirmation_menu(token, chat_id)
        return
    parts = data.split(":", 2)
    if data.startswith("enum:settings:input:"):
        key = data.rsplit(":", 1)[1]
        prompts = {"temperature": "Send temperature (0–2).", "max_tokens": "Send max_tokens (1–16000).", "top_p": "Send top_p (0–1).", "frequency_penalty": "Send frequency_penalty (-2–2).", "presence_penalty": "Send presence_penalty (-2–2).", "reasoning_budget": "Send reasoning budget (0–32000).", "stop_sequences": "Send stop sequences as text; separate multiple stops with newlines."}
        if key in prompts:
            pending_setting = {"key": key, "session_id": session["session_id"], "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
            set_meta(db, f"settings_input:{chat_id}", json.dumps(pending_setting))
            discard_panel_binding(db, chat_id, message_id)
            close_panel_message(token, chat_id, {"message": message})
            pending_setting["prompt_message_ids"] = send_text(token, chat_id, prompts[key] + " Send /cancel to leave it unchanged.")
            set_meta(db, f"settings_input:{chat_id}", json.dumps(pending_setting))
        return
    if data == "enum:settings:reset":
        update_generation_settings(db, chat_id, session["session_id"], **GENERATION_DEFAULTS)
        send_settings_menu(token, chat_id, db, session["session_id"], message_id)
        return
    if data.startswith("enum:settings:reasoning:"):
        label = data.rsplit(":", 1)[1]
        budgets = REASONING_LEVELS
        if label in budgets:
            update_generation_settings(db, chat_id, session["session_id"], reasoning_budget=budgets[label])
        send_settings_menu(token, chat_id, db, session["session_id"], message_id)
        return
    if data.startswith("enum:stream:"):
        value = parts[2]
        if value in {"on", "off"}:
            set_meta(db, f"stream_mode:{chat_id}", value)
        send_stream_menu(token, chat_id, db, message_id)
    elif data.startswith("enum:voice:"):
        value = parts[2]
        if value in {"on", "off"}:
            set_meta(db, f"voice_mode:{chat_id}", "tts" if value == "on" else "off")
        send_voice_menu(token, chat_id, db, message_id)
    elif data == "enum:stt:language":
        send_stt_language_menu(token, chat_id, db, message_id)
    elif data == "enum:stt:language_input":
        pending_stt = {"session_id": session["session_id"], "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
        set_meta(db, f"stt_language_input:{chat_id}", json.dumps(pending_stt))
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(token, chat_id, {"message": message})
        pending_stt["prompt_message_ids"] = send_text(token, chat_id, "Send a 2–8 letter STT language code such as id, en, or ja. Send /cancel to cancel.")
        set_meta(db, f"stt_language_input:{chat_id}", json.dumps(pending_stt))
    elif data.startswith("enum:sttlanguagepage:"):
        send_stt_language_menu(token, chat_id, db, message_id, int(parts[2]))
    elif data.startswith("enum:sttlanguage:"):
        try:
            value = normalize_stt_language(parts[2])
        except ValueError:
            send_text(token, chat_id, "Invalid STT language choice.")
        else:
            set_meta(db, f"stt_language:{chat_id}", value)
            send_voice_input_menu(token, chat_id, db, message_id)
    elif data == "enum:stt:model":
        send_stt_model_menu(token, chat_id, db, message_id)
    elif data == "enum:stt:back":
        send_voice_input_menu(token, chat_id, db, message_id)
    elif data.startswith("enum:stt:"):
        value = parts[2]
        if value in {"on", "off"}:
            set_meta(db, f"stt_mode:{chat_id}", value)
        send_voice_input_menu(token, chat_id, db, message_id)
    elif data.startswith("enum:sttmodel:"):
        value = parts[2]
        if value in {"tiny", "base", "small"}:
            set_meta(db, f"stt_model:{chat_id}", value)
        send_voice_input_menu(token, chat_id, db, message_id)
    elif data == "enum:memory:search":
        start_text_action_input(db, token, chat_id, session["session_id"], "memory_search", "Send a query to search Hindsight memory for the active session.", {"message": message})
    elif data == "enum:memory:scope":
        send_memory_menu(token, chat_id, db, message_id)
    elif data == "enum:memory:back":
        send_memory_menu(token, chat_id, db, message_id)
    elif data.startswith("enum:memory:"):
        value = parts[2]
        if value in {"on", "off"}:
            set_meta(db, f"memory_mode:{chat_id}", value)
        send_memory_menu(token, chat_id, db, message_id)
    elif data.startswith("enum:memoryscope:"):
        if parts[2] == "session":
            set_meta(db, f"memory_scope:{chat_id}", "session")
        send_memory_menu(token, chat_id, db, message_id)
    elif data == "enum:preset:save":
        pending_preset = {"session_id": session["session_id"], "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
        set_meta(db, f"preset_save_input:{chat_id}", json.dumps(pending_preset))
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(token, chat_id, {"message": message})
        pending_preset["prompt_message_ids"] = send_text(token, chat_id, "Send a preset name (1–64 letters, numbers, hyphens, or underscores). Send /cancel to cancel.")
        set_meta(db, f"preset_save_input:{chat_id}", json.dumps(pending_preset))
    elif data == "enum:preset:back":
        send_preset_menu(token, chat_id, db, message_id)
    elif data == "enum:presetdelete":
        send_preset_delete_menu(token, chat_id, db, message_id)
    elif data.startswith("enum:presetpage:"):
        send_preset_menu(token, chat_id, db, message_id, int(parts[2]))
    elif data.startswith("enum:presetdeletepage:"):
        send_preset_delete_menu(token, chat_id, db, message_id, int(parts[2]))
    elif data.startswith("enum:presetuse:"):
        apply_preset_action(db, token, chat_id, session["session_id"], "use", resolve_dynamic_callback_token(parts[2], "preset", chat_id) or "")
        send_preset_menu(token, chat_id, db, message_id)
    elif data.startswith("enum:presetdel:"):
        apply_preset_action(db, token, chat_id, session["session_id"], "delete", resolve_dynamic_callback_token(parts[2], "preset", chat_id) or "")
        send_preset_delete_menu(token, chat_id, db, message_id)
    elif data == "enum:rag:search":
        start_text_action_input(db, token, chat_id, session["session_id"], "databank_search", "Send a query to search the active Data Bank.", {"message": message})
    elif data == "enum:rag:remove":
        send_databank_remove_menu(token, chat_id, db, message_id)
    elif data == "enum:rag:back":
        send_databank_menu(token, chat_id, db, message_id)
    elif data.startswith("enum:ragremovepage:"):
        send_databank_remove_menu(token, chat_id, db, message_id, int(parts[2]))
    elif data.startswith("enum:ragremove:"):
        handle_data_bank_command(db, token, chat_id, "/databank remove " + (resolve_dynamic_callback_token(parts[2], "rag_document", chat_id) or "") + " confirm")
        send_databank_menu(token, chat_id, db, message_id)
    elif data == "enum:rag:reindex":
        total, indexed = reindex_data_bank_documents(db, chat_id)
        send_text(token, chat_id, f"Data Bank reindex complete: {indexed}/{total} chunks indexed.")
        send_databank_menu(token, chat_id, db, message_id)
    elif data.startswith("enum:rag:"):
        value = parts[2]
        if value in {"on", "off"}:
            set_meta(db, f"rag_mode:{chat_id}", value)
        elif value == "list":
            handle_data_bank_command(db, token, chat_id, "/databank list")
        send_databank_menu(token, chat_id, db, message_id)



def process_document_job(token: str, chat_id: str, document: dict, default_model: str, message_id: int | None = None, job_id: int | None = None) -> None:
    with chat_job_lock(chat_id):
        db = db_connect()
        set_db_connection_context(db)
        try:
            if job_id is not None and not mark_job_running(db, job_id):
                return
            import_telegram_document(db, token, chat_id, document, default_model, telegram_message_id=message_id)
            if job_id is not None:
                finish_job(db, job_id, "done")
        except Exception as exc:
            logging.error("Document job failed: %s", exc, exc_info=True)
            if job_id is not None:
                finish_job(db, job_id, "failed", str(exc))
            send_text(token, chat_id, "Document import failed. Check the file format and size limits.")
        finally:
            set_db_connection_context(None)
            db.close()


def set_bot_commands(token: str) -> None:
    try:
        telegram_request(token, "setMyCommands", {
            "commands": [
                {"command": "start", "description": "Show optional setup instructions"},
                {"command": "help", "description": "Show commands"},

                {"command": "providers", "description": "Open provider catalog; catalog-only entries are view-only"},
                {"command": "character", "description": "Open character management panel"},
                {"command": "session", "description": "Manage sessions; delete inactive only"},
                {"command": "sync", "description": "Open Live API Sync controls"},
                {"command": "update", "description": "Check and confirm bridge update"},
                {"command": "persona", "description": "Choose user persona"},
                {"command": "world", "description": "Choose World Info lore"},
                {"command": "status", "description": "Show model and chat status"},
                {"command": "edit", "description": "Edit last user message"},
                {"command": "voice", "description": "Open automatic voice panel"},
                {"command": "voice_input", "description": "Open transcription/model/language panel"},
                {"command": "settings", "description": "Open generation settings panel"},
                {"command": "taskmodel", "description": "Route utility tasks to another model"},
                {"command": "stream", "description": "Open streaming on/off panel"},
                {"command": "preset", "description": "Open preset use/delete panel"},
                {"command": "macro", "description": "Preview a macro"},
                {"command": "stscript", "description": "Run safe STscript"},
                {"command": "memory", "description": "Open memory mode/scope panel"},
                {"command": "remember", "description": "Store an explicit memory"},
                {"command": "summarize", "description": "Summarize active session"},
                {"command": "databank", "description": "Open RAG/list/remove panel"},
                {"command": "group", "description": "Open topic-only group panel"},
                {"command": "new", "description": "Name and start a new session"},
                {"command": "reset", "description": "Confirm active-session reset"},
                {"command": "regen", "description": "Regenerate last response"},
                {"command": "swipe", "description": "Browse response variants"},
                {"command": "branch", "description": "Choose an active chat branch"},
                {"command": "prompt", "description": "Inspect prompt context"},
                {"command": "continue", "description": "Continue last response"},
                {"command": "retry", "description": "Retry the last failed response"},
                {"command": "note", "description": "Open Author's Note panel"},
                {"command": "systemprompt", "description": "Choose TXT System Prompt from panel"},
                {"command": "language", "description": "Choose model reply language"},
                {"command": "expression", "description": "Choose manual or automatic character expressions"},
                {"command": "imagine", "description": "Generate an image from a prompt"},
            ]
        })
    except Exception:
        logging.warning("Could not register Telegram command menu", exc_info=True)

HELP_CATEGORIES = {
    "basic": [
        ("/start", "Send the character card's first message only."),
        ("/help", "Open this help menu; use /help <command> for one command."),
        ("/status", "Show active card, session, model, memory, RAG, group, and generation state."),
        ("/new", "Create and activate a new isolated session."),
        ("/reset", "Clear messages, variants, and summary from the active session."),
        ("/session", "List and switch between sessions."),
    ],
    "characters": [
        ("/model", "Choose a provider and model from the standalone catalog; 8 options per page."),
        ("/providers", "Open the provider catalog; adapter-enabled entries can generate, catalog-only entries are view-only."),
        ("/providers refresh", "Open the provider panel; use Refresh models there."),
        ("/providers health", "Open the provider panel; use Provider health there."),
        ("/character", "Open the character panel for selection, info, safe deletion, and upload guidance."),
        ("/persona", "Choose or disable the user persona."),
        ("/world", "Open the panel to choose or disable a World Info lorebook."),
        ("/note <text>", "Set the session-scoped Author's Note."),
        ("/systemprompt", "Open the panel to choose a configured TXT System Prompt; text fallback is disabled."),
    ],
    "generation": [
        ("/settings", "Open this session's generation panel; choose reasoning or set validated numeric fields."),
        ("/stream on|off", "Open the streaming on/off panel."),
        ("/preset", "Open the preset use/delete panel; save a new name with text."),
        ("/macro <text>", "Preview supported SillyTavern macros safely."),
        ("/stscript note|reset", "Run the safe Telegram STscript subset."),
        ("/regen", "Regenerate the latest response as a new variant."),
        ("/swipe", "Browse and keep response variants."),
        ("/branch", "Open or select an active response branch."),
        ("/continue", "Continue the latest assistant response."),
        ("/edit <text>", "Replace the latest user turn and regenerate."),
        ("/retry", "Retry the latest failed character response."),
        ("/prompt", "Inspect prompt sections without showing the full prompt."),
    ],
    "memory_rag": [
        ("/memory", "Open memory mode/scope panel; search remains a free-text query."),
        ("/remember <fact>", "Queue an explicit long-term memory."),
        ("/summarize", "Force-refresh the active session summary."),
        ("/databank", "Open RAG mode/list/remove/reindex panel; search remains a free-text query."),
        ("/export", "Export the active session as SillyTavern JSONL."),
        ("/import", "Prepare to import a SillyTavern JSONL document."),
    ],
    "voice_group": [
        ("/tts <text>", "Send one text-to-speech voice message."),
        ("/voice on|off", "Open automatic voice reply panel."),
        ("/voice_input on|off", "Open transcription/model panel; language remains text input."),
        ("/group", "Open the group control panel: add/remove characters, choose speaker, mode, and enable state."),
    ],
}


def help_markup() -> dict:
    return {"inline_keyboard": [
        [{"text": "💬 Basic", "callback_data": "help:basic"}, {"text": "🎭 Characters", "callback_data": "help:characters"}],
        [{"text": "⚙️ Generation", "callback_data": "help:generation"}, {"text": "🧠 Memory/RAG", "callback_data": "help:memory_rag"}],
        [{"text": "🎙️ Voice/Group", "callback_data": "help:voice_group"}],
        [{"text": "❌ Close", "callback_data": "help:close"}],
    ]}


def help_text(category: str | None = None) -> str:
    if category in HELP_CATEGORIES:
        title = category.replace("_", " / ").title()
        return "Help — " + title + "\n\n" + "\n".join(f"{command} — {description}" for command, description in HELP_CATEGORIES[category])
    return "Help — SillyTavern Telegram Bridge\n\nTap a category below for command explanations.\nYou can also use /help <command>."


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


def send_help_menu(token: str, chat_id: str, category: str | None = None, message_id: int | None = None) -> None:
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": help_text(category), "reply_markup": help_markup()}
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
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": f"Generation settings — current session\nActive reasoning: {current_label} ({current} budget)\n\nTap a reasoning level below to apply it.\nOther validated fields: temperature, max_tokens, top_p, frequency_penalty, presence_penalty, stop_sequences.\nUse /settings <name> <value> only for those numeric/text fields.", "reply_markup": {"inline_keyboard": rows}}
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
    rows = [[{"text": ("✅ " if mode == "on" else "") + "Transcription on", "callback_data": "enum:stt:on"}, {"text": ("✅ " if mode == "off" else "") + "Transcription off", "callback_data": "enum:stt:off"}], [{"text": f"Model: {model}", "callback_data": "enum:stt:model"}], [{"text": "❌ Close", "callback_data": "enum:close"}]]
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": f"Voice input transcription: {mode}\nModel: {model}\nLanguage: {get_meta(db, f'stt_language:{chat_id}', 'auto')}", "reply_markup": {"inline_keyboard": rows}}
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
    scope = memory_scope(db, chat_id)
    rows = [[{"text": ("✅ " if mode == "on" else "") + "Memory on", "callback_data": "enum:memory:on"}, {"text": ("✅ " if mode == "off" else "") + "Memory off", "callback_data": "enum:memory:off"}], [{"text": f"Scope: {scope}", "callback_data": "enum:memory:scope"}], [{"text": "❌ Close", "callback_data": "enum:close"}]]
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": f"Hindsight memory: {mode}\nScope: {scope}", "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_memory_scope_menu(token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None) -> None:
    current = memory_scope(db, chat_id)
    rows = [[{"text": ("✅ " if scope == current else "") + scope.title(), "callback_data": "enum:memoryscope:" + scope}] for scope in ("user", "character", "session")]
    rows.append([{"text": "⬅️ Back", "callback_data": "enum:memory:back"}, {"text": "❌ Close", "callback_data": "enum:close"}])
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": "Choose memory scope:", "reply_markup": {"inline_keyboard": rows}}
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
    rows.append([{"text": "🗑️ Delete preset", "callback_data": "enum:presetdelete"}])
    rows.append([{"text": "❌ Close", "callback_data": "enum:close"}])
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": f"Choose a preset to apply (page {current_page + 1}/{total_pages}). Save a new preset with `/preset save <name>`." , "reply_markup": {"inline_keyboard": rows}}
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
    rows = [[{"text": ("✅ " if mode == "on" else "") + "RAG on", "callback_data": "enum:rag:on"}, {"text": ("✅ " if mode == "off" else "") + "RAG off", "callback_data": "enum:rag:off"}], [{"text": "List documents", "callback_data": "enum:rag:list"}, {"text": "Remove document", "callback_data": "enum:rag:remove"}], [{"text": "Reindex embeddings", "callback_data": "enum:rag:reindex"}], [{"text": "❌ Close", "callback_data": "enum:close"}]]
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
        remove_inline_keyboard(token, {"message": message})
        return
    parts = data.split(":", 2)
    if data.startswith("enum:settings:input:"):
        key = data.rsplit(":", 1)[1]
        prompts = {"temperature": "Send temperature (0–2).", "max_tokens": "Send max_tokens (1–16000).", "top_p": "Send top_p (0–1).", "frequency_penalty": "Send frequency_penalty (-2–2).", "presence_penalty": "Send presence_penalty (-2–2).", "reasoning_budget": "Send reasoning budget (0–32000).", "stop_sequences": "Send stop sequences as text; separate multiple stops with newlines."}
        if key in prompts:
            set_meta(db, f"settings_input:{chat_id}", json.dumps({"key": key, "session_id": session["session_id"], "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}))
            send_text(token, chat_id, prompts[key] + " Send /cancel to leave it unchanged.")
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
    elif data == "enum:memory:scope":
        send_memory_scope_menu(token, chat_id, db, message_id)
    elif data == "enum:memory:back":
        send_memory_menu(token, chat_id, db, message_id)
    elif data.startswith("enum:memory:"):
        value = parts[2]
        if value in {"on", "off"}:
            set_meta(db, f"memory_mode:{chat_id}", value)
        send_memory_menu(token, chat_id, db, message_id)
    elif data.startswith("enum:memoryscope:"):
        value = parts[2]
        if value in {"user", "character", "session"}:
            set_meta(db, f"memory_scope:{chat_id}", value)
        send_memory_menu(token, chat_id, db, message_id)
    elif data == "enum:preset:back":
        send_preset_menu(token, chat_id, db, message_id)
    elif data == "enum:presetdelete":
        send_preset_delete_menu(token, chat_id, db, message_id)
    elif data.startswith("enum:presetpage:"):
        send_preset_menu(token, chat_id, db, message_id, int(parts[2]))
    elif data.startswith("enum:presetdeletepage:"):
        send_preset_delete_menu(token, chat_id, db, message_id, int(parts[2]))
    elif data.startswith("enum:presetuse:"):
        handle_preset_command(db, token, chat_id, session["session_id"], "/preset use " + (resolve_dynamic_callback_token(parts[2], "preset", chat_id) or ""))
        send_preset_menu(token, chat_id, db, message_id)
    elif data.startswith("enum:presetdel:"):
        handle_preset_command(db, token, chat_id, session["session_id"], "/preset delete " + (resolve_dynamic_callback_token(parts[2], "preset", chat_id) or ""))
        send_preset_delete_menu(token, chat_id, db, message_id)
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
        try:
            if job_id is not None and not mark_job_running(db, job_id):
                return
            import_telegram_document(db, token, chat_id, document, default_model, telegram_message_id=message_id, operation_id=job_id)
            if job_id is not None:
                finish_job(db, job_id, "done")
        except Exception as exc:
            logging.error("Document job failed: %s", exc, exc_info=True)
            if job_id is not None:
                finish_job(db, job_id, "failed", str(exc))
            send_text(token, chat_id, "Document import failed. Check the file format and size limits.")
        finally:
            db.close()


def set_bot_commands(token: str) -> None:
    try:
        telegram_request(token, "setMyCommands", {
            "commands": [
                {"command": "start", "description": "Send character first message"},
                {"command": "help", "description": "Show commands"},
                {"command": "model", "description": "Choose provider/model (8 per page)"},
                {"command": "providers", "description": "Open provider catalog; catalog-only entries are view-only"},
                {"command": "character", "description": "Open character management panel"},
                {"command": "session", "description": "Choose chat session"},
                {"command": "persona", "description": "Choose user persona"},
                {"command": "world", "description": "Choose World Info lore"},
                {"command": "status", "description": "Show model and chat status"},
                {"command": "export", "description": "Export chat as SillyTavern JSONL"},
                {"command": "import", "description": "Import a SillyTavern JSONL chat"},
                {"command": "edit", "description": "Edit last user message"},
                {"command": "tts", "description": "Convert text to voice"},
                {"command": "voice", "description": "Open automatic voice panel"},
                {"command": "voice_input", "description": "Open transcription/model panel"},
                {"command": "settings", "description": "Set validated generation values"},
                {"command": "stream", "description": "Open streaming on/off panel"},
                {"command": "preset", "description": "Open preset use/delete panel"},
                {"command": "macro", "description": "Preview a macro"},
                {"command": "stscript", "description": "Run safe STscript"},
                {"command": "memory", "description": "Open memory mode/scope panel"},
                {"command": "remember", "description": "Store an explicit memory"},
                {"command": "summarize", "description": "Summarize active session"},
                {"command": "databank", "description": "Open RAG/list/remove panel"},
                {"command": "group", "description": "Open group control panel"},
                {"command": "new", "description": "Start a new chat"},
                {"command": "reset", "description": "Reset this chat"},
                {"command": "regen", "description": "Regenerate last response"},
                {"command": "swipe", "description": "Browse response variants"},
                {"command": "branch", "description": "Choose an active chat branch"},
                {"command": "prompt", "description": "Inspect prompt context"},
                {"command": "continue", "description": "Continue last response"},
                {"command": "note", "description": "Set session Author's Note"},
                {"command": "systemprompt", "description": "Choose TXT System Prompt from panel"},
            ]
        })
    except Exception:
        logging.warning("Could not register Telegram command menu", exc_info=True)



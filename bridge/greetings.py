"""Character-card opening and alternate greeting helpers."""


def greeting_options(fields: dict) -> list[str]:
    """Return the primary greeting followed by bounded alternate greetings."""
    options = [str(fields.get("first_mes") or "")]
    raw = fields.get("alternate_greetings") or "[]"
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        values = []
    if isinstance(values, list):
        options.extend(str(value or "") for value in values[:20])
    return [value[:CARD_FIELD_MAX_CHARS].strip() for value in options if value[:CARD_FIELD_MAX_CHARS].strip()]


def greeting_menu_markup(fields: dict, page: int = 0) -> dict:
    options = greeting_options(fields)
    page_options, current_page, total_pages = panel_page(list(enumerate(options)), page)
    rows = [[{"text": ("⭐ " if index == 0 else "💬 ") + f"Greeting {index + 1}", "callback_data": f"greeting:{index}"}] for index, _text in page_options]
    navigation = panel_navigation("greeting", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "❌ Close", "callback_data": "greeting:cancel"}])
    return {"inline_keyboard": rows}


def send_greeting_menu(token: str, chat_id: str, fields: dict, message_id: int | None = None, page: int = 0) -> None:
    options = greeting_options(fields)
    if not options:
        send_text(token, chat_id, "This character card has no opening greeting.")
        return
    text = f"Character greetings ({len(options)})\nChoose an opening greeting to send."
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": greeting_menu_markup(fields, page)}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_character_greeting(db, token: str, chat_id: str, fields: dict, session_id: str, user_name: str, index: int = 0, operation_id=None, operation_kind: str = "greeting") -> bool:
    if operation_id is not None and (operation_was_applied(db, operation_id) or not begin_operation(db, operation_id, operation_kind)):
        return False
    options = greeting_options(fields)
    if index < 0 or index >= len(options):
        return False
    greeting = replace_macros(options[index], fields, user_name).strip()
    if not greeting:
        return False
    message_ids = send_text(token, chat_id, greeting)
    db.execute("INSERT INTO messages(chat_id,session_id,role,content,telegram_message_ids,created_at) VALUES(?,?,?,?,?,?)", (chat_id, session_id, "assistant", greeting, json.dumps(message_ids), time.time()))
    if operation_id is not None:
        record_operation(db, operation_id, operation_kind)
    db.commit()
    return True


def handle_greeting_callback(db, token: str, callback, answer_callback, data: str, chat_id: str, message: dict, session: dict, session_id: str, operation_id=None) -> bool:
    if not data.startswith("greeting:"):
        return False
    value = data.split(":", 1)[1]
    if value == "cancel":
        answer_callback(token, str(callback.get("id", "")), "Cancelled")
        remove_inline_keyboard(token, callback)
        return True
    if value.startswith("page:"):
        page = int(value.split(":", 1)[1])
        answer_callback(token, str(callback.get("id", "")), "Page")
        fields = card_fields_from_file(session["character_file"])
        send_greeting_menu(token, chat_id, fields, message.get("message_id"), page)
        return True
    try:
        index = int(value)
    except ValueError:
        answer_callback(token, str(callback.get("id", "")), "Invalid greeting")
        return True
    fields = card_fields_from_file(session["character_file"])
    user_name = persona_name(session.get("persona_id") or "") if session.get("persona_id") else DEFAULT_USER_NAME
    if send_character_greeting(db, token, chat_id, fields, session_id, user_name, index, operation_id, "greeting_select"):
        answer_callback(token, str(callback.get("id", "")), "Greeting sent")
        remove_inline_keyboard(token, callback)
    else:
        answer_callback(token, str(callback.get("id", "")), "Greeting unavailable")
    return True

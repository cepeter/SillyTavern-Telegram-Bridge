"""Character, World Info, prompt, callback-token, and panel helpers.

This module is loaded into the shared runtime namespace after common.py.
"""
def read_png_chara(path: Path) -> dict:
    return parse_png_chara_bytes(path.read_bytes())


def parse_png_chara_bytes(raw: bytes) -> dict:
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("character file is not a PNG")
    pos = 8
    encoded = None
    while pos + 12 <= len(raw):
        size = struct.unpack(">I", raw[pos:pos + 4])[0]
        chunk_type = raw[pos + 4:pos + 8]
        chunk = raw[pos + 8:pos + 8 + size]
        pos += 12 + size
        if chunk_type == b"tEXt" and chunk.startswith(b"chara\x00"):
            encoded = chunk.split(b"\x00", 1)[1]
            break
    if not encoded:
        raise ValueError("PNG has no SillyTavern chara metadata")
    return json.loads(base64.b64decode(encoded).decode("utf-8"))


def card_fields(card: dict) -> dict[str, str]:
    data = card.get("data") if isinstance(card.get("data"), dict) else card
    fields = {}
    for key in (
        "name", "description", "personality", "scenario", "first_mes",
        "mes_example", "system_prompt", "post_history_instructions",
    ):
        value = str(data.get(key) or card.get(key) or "")
        fields[key] = value[:120] if key == "name" else value[:CARD_FIELD_MAX_CHARS]
    total = sum(len(value) for key, value in fields.items() if key != "name")
    if total > CARD_TOTAL_MAX_CHARS:
        remaining = CARD_TOTAL_MAX_CHARS
        for key in ("description", "personality", "scenario", "first_mes", "mes_example", "system_prompt", "post_history_instructions"):
            fields[key] = fields[key][:remaining]
            remaining = max(0, remaining - len(fields[key]))
    fields["name"] = fields["name"] or "Alisha"
    return fields


def character_card_paths() -> list[Path]:
    if not CHARACTER_DIR.exists():
        return []
    return sorted(p for p in CHARACTER_DIR.glob("*.png") if p.is_file())[:CATALOG_MAX_ITEMS]


def safe_character_path(name: str) -> Path | None:
    base = CHARACTER_DIR.resolve()
    path = (CHARACTER_DIR / name).resolve()
    if path.parent != base or path.suffix.lower() != ".png" or not path.is_file():
        return None
    return path


def card_fields_from_file(name: str) -> dict[str, str]:
    path = safe_character_path(name) or CARD_FILE
    return card_fields(read_png_chara(path))

def load_world_document(name: str) -> tuple[Path, dict]:
    path = safe_world_path(name)
    if path is None:
        raise ValueError("World Info file not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("World Info document must be a JSON object")
    return path, data


def save_world_document(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def world_entries(data: dict) -> tuple[list[str], list[dict]]:
    raw = data.get("entries", {})
    if isinstance(raw, dict):
        keys = [str(key) for key in raw]
        return keys, [dict(raw[key]) if isinstance(raw[key], dict) else {} for key in raw]
    values = list(raw or [])
    return [str(index) for index in range(len(values))], [dict(item) if isinstance(item, dict) else {} for item in values]


def replace_world_entries(data: dict, keys: list[str], entries: list[dict]) -> None:
    if isinstance(data.get("entries"), dict):
        data["entries"] = {key: entry for key, entry in zip(keys, entries)}
    else:
        data["entries"] = entries


def world_file_paths() -> list[Path]:
    if not WORLD_DIR.exists():
        return []
    return sorted(p for p in WORLD_DIR.glob("*.json") if p.is_file())[:CATALOG_MAX_ITEMS]


def safe_world_path(name: str) -> Path | None:
    if not name or name == "off":
        return None
    base = WORLD_DIR.resolve()
    path = (WORLD_DIR / name).resolve()
    if path.parent != base or path.suffix.lower() != ".json" or not path.is_file():
        return None
    return path


def active_world_files(value: str | list[str] | None) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        text = str(value or "").strip()
        if not text:
            return []
        try:
            parsed = json.loads(text) if text.startswith("[") else [text]
        except json.JSONDecodeError:
            parsed = [text]
        raw = parsed if isinstance(parsed, list) else [parsed]
    result = []
    for name in raw:
        name = str(name)
        if name not in result and safe_world_path(name):
            result.append(name)
    return result


def encode_world_files(names: list[str]) -> str:
    return json.dumps(list(dict.fromkeys(names)), ensure_ascii=False, separators=(",", ":")) if names else ""


def build_world_info(world_names: str | list[str], context: str, fields: dict[str, str], user_name: str = "Punto") -> str:
    """Activate basic SillyTavern World Info entries by key and secondary key."""
    sections = []
    for world_name in active_world_files(world_names):
        path = safe_world_path(world_name)
        if path is None:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw_entries = data.get("entries", {})
            entries = list(raw_entries.values()) if isinstance(raw_entries, dict) else list(raw_entries or [])
            lowered = context.casefold()
            activated = []
            activated_ids = set()
            for _ in range(3):
                changed = False
                for entry_index, entry in enumerate(entries):
                    if entry_index in activated_ids or entry.get("disable"):
                        continue
                    keys = entry.get("key", [])
                    secondary = entry.get("keysecondary", [])
                    if isinstance(keys, str):
                        keys = [keys]
                    if isinstance(secondary, str):
                        secondary = [secondary]
                    key_hit = bool(entry.get("constant")) or any(str(k).casefold() in lowered for k in keys if str(k).strip())
                    if not key_hit:
                        continue
                    if secondary and not any(str(k).casefold() in lowered for k in secondary if str(k).strip()):
                        continue
                    content = str(entry.get("content") or "").strip()
                    if content:
                        activated.append((int(entry.get("order", 100)), content))
                        activated_ids.add(entry_index)
                        lowered += "\n" + content.casefold()
                        changed = True
                if not changed:
                    break
            activated.sort(key=lambda item: item[0])
            sections.extend(replace_macros(content, fields, user_name) for _, content in activated)
        except Exception:
            logging.warning("Could not load World Info %s", world_name, exc_info=True)
    return "\n\n".join(sections)[:12000]


def load_personas() -> dict[str, dict[str, str]]:
    try:
        return load_native_personas()
    except Exception:
        logging.warning("Could not load native SillyTavern personas", exc_info=True)
        return {}


def get_persona(persona_id: str) -> dict[str, str] | None:
    return load_personas().get(persona_id)


def persona_name(persona_id: str) -> str:
    persona = get_persona(persona_id)
    return str(persona.get("name") or persona_id or "Punto") if persona else "Punto"


def _merge_system_prompt_json(result: dict[str, dict[str, str]], path: Path) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logging.warning("Could not read System Prompt catalog %s", path, exc_info=True)
        return
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        result[path.stem] = {"name": path.stem.replace("_", " ").replace("-", " ").title(), "prompt": "\n".join(raw)}
        return
    if isinstance(raw, str) and raw.strip():
        result[path.stem] = {"name": path.stem.replace("_", " ").replace("-", " ").title(), "prompt": raw}
        return
    if isinstance(raw, dict) and str(raw.get("prompt") or "").strip():
        key = str(raw.get("id") or path.stem)
        result[key] = {"name": str(raw.get("name") or path.stem), "prompt": str(raw["prompt"])}
        return
    for key, value in (raw.items() if isinstance(raw, dict) else []):
        if isinstance(value, str):
            result[str(key)] = {"name": str(key), "prompt": value}
        elif isinstance(value, dict) and str(value.get("prompt") or "").strip():
            result[str(key)] = {"name": str(value.get("name") or key), "prompt": str(value["prompt"])}


def _merge_system_prompt_text(result: dict[str, dict[str, str]], path: Path) -> None:
    try:
        prompt = path.read_text(encoding="utf-8")
    except OSError:
        logging.warning("Could not read System Prompt text file %s", path, exc_info=True)
        return
    if prompt.strip():
        result[path.stem] = {"name": path.stem.replace("_", " ").replace("-", " ").title(), "prompt": prompt}


def load_system_prompts() -> dict[str, dict[str, str]]:
    result = {}
    if SYSTEM_PROMPTS_FILE:
        path = Path(SYSTEM_PROMPTS_FILE)
        _merge_system_prompt_text(result, path) if path.suffix.casefold() == ".txt" else _merge_system_prompt_json(result, path)
    if SYSTEM_PROMPTS_DIR.exists():
        for path in sorted(list(SYSTEM_PROMPTS_DIR.glob("*.json")) + list(SYSTEM_PROMPTS_DIR.glob("*.txt"))):
            _merge_system_prompt_text(result, path) if path.suffix.casefold() == ".txt" else _merge_system_prompt_json(result, path)
    return dict(list(result.items())[:CATALOG_MAX_ITEMS])


_CALLBACK_TOKEN_VALUES: dict[str, tuple[str, str, str, float]] = {}
_CALLBACK_TOKEN_TTL_SECONDS = 900


def dynamic_callback_token(kind: str, value: str, chat_id: str = "") -> str:
    raw = f"{kind}|{chat_id}|{value}"
    token = "t" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    expires_at = time.time() + _CALLBACK_TOKEN_TTL_SECONDS
    _CALLBACK_TOKEN_VALUES[token] = (str(kind), str(value), str(chat_id), expires_at)
    try:
        token_db = db_connect()
        token_db.execute("INSERT OR REPLACE INTO callback_tokens(token,kind,value,chat_id,expires_at) VALUES(?,?,?,?,?)", (token, str(kind), str(value), str(chat_id), expires_at))
        token_db.commit()
        token_db.close()
    except Exception:
        logging.debug("Could not persist callback token", exc_info=True)
    return token


def resolve_dynamic_callback_token(token: str, kind: str, chat_id: str = "") -> str | None:
    item = _CALLBACK_TOKEN_VALUES.get(str(token))
    if item is None:
        try:
            token_db = db_connect()
            row = token_db.execute("SELECT kind,value,chat_id,expires_at FROM callback_tokens WHERE token=?", (str(token),)).fetchone()
            token_db.close()
            if row:
                item = (str(row[0]), str(row[1]), str(row[2]), float(row[3]))
                _CALLBACK_TOKEN_VALUES[str(token)] = item
        except Exception:
            logging.debug("Could not load callback token", exc_info=True)
    if item is None:
        return None
    stored_kind, value, stored_chat_id, expires_at = item
    if expires_at < time.time() or stored_kind != str(kind) or (stored_chat_id and stored_chat_id != str(chat_id)):
        _CALLBACK_TOKEN_VALUES.pop(str(token), None)
        try:
            token_db = db_connect()
            token_db.execute("DELETE FROM callback_tokens WHERE token=?", (str(token),))
            token_db.commit()
            token_db.close()
        except Exception:
            logging.debug("Could not remove expired callback token", exc_info=True)
        return None
    return value


def get_system_prompt_choice(name: str) -> str | None:
    prompts = load_system_prompts()
    item = prompts.get(name)
    if item is None and str(name).startswith("id:"):
        item = next((value for key, value in prompts.items() if system_prompt_callback_token(key) == name), None)
    return item["prompt"] if item else None


def system_prompt_callback_token(key: str) -> str:
    candidate = "systemprompt:" + str(key)
    if len(candidate.encode("utf-8")) <= 64:
        return str(key)
    return "id:" + hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:24]


def system_prompt_choices() -> list[tuple[str, str]]:
    return [(key, item["name"]) for key, item in load_system_prompts().items()]


PANEL_PAGE_SIZE = 8


def panel_label(value: str, limit: int = 48) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def panel_page(items: list, page: int) -> tuple[list, int, int]:
    total_pages = max(1, (len(items) + PANEL_PAGE_SIZE - 1) // PANEL_PAGE_SIZE)
    current_page = min(max(int(page), 0), total_pages - 1)
    start = current_page * PANEL_PAGE_SIZE
    return items[start:start + PANEL_PAGE_SIZE], current_page, total_pages


def panel_navigation(prefix: str, page: int, total_pages: int) -> list[dict[str, str]]:
    if total_pages <= 1:
        return []
    row = []
    if page > 0:
        row.append({"text": "⬅️ Previous", "callback_data": f"{prefix}:page:{page - 1}"})
    if page < total_pages - 1:
        row.append({"text": "Next ➡️", "callback_data": f"{prefix}:page:{page + 1}"})
    return row


def send_persona_menu(token: str, chat_id: str, current_persona: str, message_id: int | None = None, page: int = 0) -> None:
    personas = load_personas()
    options = [(persona_id, str(persona.get("name") or persona_id)) for persona_id, persona in list(personas.items())[:CATALOG_MAX_ITEMS]]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for persona_id, label in page_options:
        mark = "✅ " if persona_id == current_persona else ""
        rows.append([{"text": mark + label, "callback_data": "persona:" + dynamic_callback_token("persona", persona_id, chat_id)}])
    navigation = panel_navigation("persona", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "🚫 Persona off", "callback_data": "persona:off"}])
    if current_persona and current_persona in personas:
        rows.append([{"text": "✏️ Edit current persona", "callback_data": "persona:edit"}])
    rows.append([{"text": "➕ Create persona", "callback_data": "persona:create"}])
    rows.append([{"text": "❌ Cancel", "callback_data": "persona:cancel"}])
    page_label = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"Current Persona: {persona_name(current_persona) if current_persona else 'off'}{page_label}\nChoose a persona:"
    warning = persona_catalog_warning()
    if warning:
        text += f"\n\n⚠️ {warning}"
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_character_menu(token: str, chat_id: str, current_character: str, message_id: int | None = None, page: int = 0) -> None:
    options = [(path.name, character_display_name(path)) for path in character_card_paths()]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for filename, label in page_options:
        mark = "✅ " if filename == current_character else ""
        rows.append([{"text": mark + panel_label(label), "callback_data": "character:" + dynamic_callback_token("character", filename, chat_id)}])
    navigation = panel_navigation("character", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "ℹ️ Info", "callback_data": "character:info"}, {"text": "🗑️ Delete", "callback_data": "character:delete"}])
    rows.append([{"text": "🔄 Refresh", "callback_data": "character:menu"}, {"text": "📤 Upload", "callback_data": "character:upload"}])
    rows.append([{"text": "❌ Cancel", "callback_data": "character:cancel"}])
    current_label = current_character
    if safe_character_path(current_character):
        current_label = card_fields_from_file(current_character)["name"]
    page_label = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"Current character: {current_label}{page_label}\nChoose a character card:"
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    try:
        telegram_request(token, method, payload)
    except RuntimeError as exc:
        if method == "editMessageText" and "not modified" in str(exc).casefold():
            return
        raise


def send_character_info_menu(token: str, chat_id: str, message_id: int | None = None, page: int = 0) -> None:
    options = [(path.name, character_display_name(path)) for path in character_card_paths()]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [[{"text": label, "callback_data": "characterinfo:" + dynamic_callback_token("character", filename, chat_id)}] for filename, label in page_options]
    navigation = panel_navigation("characterinfo", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "⬅️ Back", "callback_data": "character:menu"}, {"text": "❌ Close", "callback_data": "character:cancel"}])
    text = f"Choose a character for info (page {current_page + 1}/{total_pages}):"
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_character_delete_menu(token: str, chat_id: str, active_character: str, message_id: int | None = None, page: int = 0) -> None:
    options = [(path.name, character_display_name(path)) for path in character_card_paths() if path.name != active_character]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [[{"text": label, "callback_data": "characterdelete:" + dynamic_callback_token("character", filename, chat_id)}] for filename, label in page_options]
    navigation = panel_navigation("characterdelete", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "⬅️ Back", "callback_data": "character:menu"}, {"text": "❌ Close", "callback_data": "character:cancel"}])
    text = f"Choose a non-active character to delete (page {current_page + 1}/{total_pages}):"
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_character_delete_confirm(token: str, chat_id: str, filename: str, message_id: int | None = None) -> None:
    token_value = dynamic_callback_token("character", filename, chat_id)
    payload = {"chat_id": chat_id, "text": f"Delete {Path(filename).stem}? The card file will be removed; verified backups are kept.", "reply_markup": {"inline_keyboard": [[{"text": "✅ Confirm delete", "callback_data": "characterdeleteconfirm:" + token_value}, {"text": "❌ Cancel", "callback_data": "character:delete"}]]}}
    method = "editMessageText" if message_id else "sendMessage"
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def send_session_menu(token: str, chat_id: str, sessions: list[dict[str, str]], current_id: str, message_id: int | None = None, page: int = 0) -> None:
    options = [(session["session_id"], session["title"] or session["session_id"]) for session in sessions]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for session_id, label in page_options:
        mark = "✅ " if session_id == current_id else ""
        rows.append([{"text": mark + label, "callback_data": "session:" + session_id}])
    navigation = panel_navigation("session", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": "➕ New session", "callback_data": "session:new"}, {"text": "🗑️ Delete session", "callback_data": "session:delete"}])
    rows.append([{"text": "❌ Cancel", "callback_data": "session:cancel"}])
    page_label = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"Current session: {current_id}{page_label}\nChoose a session, create a new one, or delete an inactive session with its session-scoped Hindsight documents."
    method = "editMessageText" if message_id else "sendMessage"
    payload = {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": rows}}
    if message_id:
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


def replace_macros(text: str, fields: dict[str, str], user_name: str = "Punto") -> str:
    result = (text.replace("{{char}}", fields["name"])
                .replace("{{user}}", user_name)
                .replace("<USER>", user_name)
                .replace("<BOT>", fields["name"]))
    def pick_macro(match):
        choices = [item for item in match.group(1).split("::") if item]
        return random.choice(choices) if choices else ""
    result = re.sub(r"\{\{(?:random|pick)::([^}]+)\}\}", pick_macro, result)
    now = time.localtime()
    return (result.replace("{{time}}", time.strftime("%H:%M", now))
                 .replace("{{date}}", time.strftime("%Y-%m-%d", now))
                 .replace("{{weekday}}", time.strftime("%A", now)))


def build_system_prompt(fields: dict[str, str], user_name: str = "Punto") -> str:
    system = fields["system_prompt"] or (
        "Write {{char}}'s next reply in a fictional chat between {{char}} and {{user}}. "
        "Stay in character and do not speak for {{user}}."
    )
    sections = [replace_macros(system, fields, user_name)]
    for label, key in (
        ("Character description", "description"),
        ("Personality", "personality"),
        ("Scenario", "scenario"),
    ):
        if fields[key]:
            sections.append(f"\n## {label}\n{replace_macros(fields[key], fields, user_name)}")
    if fields["mes_example"]:
        examples = fields["mes_example"][-8000:]
        sections.append(f"\n## Example dialogue\n{replace_macros(examples, fields, user_name)}")
    return "\n".join(sections)

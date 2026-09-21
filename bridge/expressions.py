"""SillyTavern-compatible expression sprites for Telegram delivery."""
from __future__ import annotations

from bridge.ordinary_dependencies import bind_module_dependencies as _bind_module_dependencies


import re

EXPRESSION_META = "expression"
EXPRESSION_MODES = {"auto", "off", "manual"}
EXPRESSION_LABEL_RE = re.compile(r"[^a-z0-9_-]+")
EXPRESSION_SUFFIX_RE = re.compile(r"^(.+?)(?:[-.].*?)?$", re.IGNORECASE)
EXPRESSION_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
EXPRESSION_KEYWORDS = {
    "joy": ("happy", "glad", "smile", "laugh", "joy", "excited", "delight"),
    "sadness": ("sad", "cry", "tears", "sorry", "grief", "lonely", "hurt"),
    "anger": ("angry", "怒", "furious", "mad", "rage", "annoyed", "irritated"),
    "surprise": ("surprise", "shocked", "gasp", "wow", "unexpected", "suddenly"),
    "fear": ("afraid", "fear", "scared", "terrified", "worry", "nervous"),
    "love": ("love", "dear", "darling", "kiss", "affection", "heart"),
    "disgust": ("disgust", "gross", "horrible", "ugh", "嫌"),
    "neutral": ("neutral",),
}


def expression_mode_key(chat_id: str, session_id: str) -> str:
    return f"{EXPRESSION_META}:mode:{chat_id}:{session_id}"


def expression_last_key(chat_id: str, session_id: str) -> str:
    return f"{EXPRESSION_META}:last:{chat_id}:{session_id}"


def normalize_expression_label(value: str) -> str:
    value = str(value or "").strip().casefold()
    return EXPRESSION_LABEL_RE.sub("_", value).strip("_-")[:40]


def expression_label(path: Path) -> str:
    stem = path.stem.casefold()
    match = EXPRESSION_SUFFIX_RE.match(stem)
    return normalize_expression_label(match.group(1) if match else stem)


def _expression_roots(character_file: str) -> list[Path]:
    card = safe_character_path(character_file)
    if card is None:
        card = CARD_FILE
    roots = [card.with_suffix("")]
    default_root = SILLYTAVERN_DIR / "public" / "img" / "default-expressions"
    if default_root.exists():
        roots.append(default_root)
    return roots


def discover_expression_assets(character_file: str) -> dict[str, Path]:
    """Discover native ST sprites in <characters>/<card stem> and defaults."""
    assets: dict[str, Path] = {}
    for root in _expression_roots(character_file):
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir(), key=lambda item: (expression_label(item), 0 if item.stem.casefold() == expression_label(item) else 1, item.name.casefold())):
            if path.is_file() and path.suffix.casefold() in EXPRESSION_EXTENSIONS:
                label = expression_label(path)
                if label and label not in assets:
                    assets[label] = path
    return assets


def classify_expression(text: str, available: dict[str, Path]) -> str:
    """Classify locally with keyword scores; choose neutral when uncertain."""
    lowered = str(text or "").casefold()
    scores = {label: sum(lowered.count(word) for word in words) for label, words in EXPRESSION_KEYWORDS.items()}
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    for label, score in ranked:
        if score and label in available:
            return label
    for fallback in ("neutral", "joy", "sadness"):
        if fallback in available:
            return fallback
    return ""


def _fixed_avatar(character_file: str) -> Path | None:
    path = safe_character_path(character_file)
    return path if path and path.is_file() else (CARD_FILE if CARD_FILE.is_file() else None)


def _send_expression_photo(token: str, chat_id: str, path: Path) -> bool:
    if path is None or path.stat().st_size > IMAGE_MAX_BYTES:
        logging.warning("Expression asset missing or exceeds size limit: %s", path)
        return False
    boundary = f"----BridgeExpression{int(time.time() * 1000000)}"
    real_chat_id, thread_id = parse_topic_scope(chat_id)
    chunks = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{real_chat_id}\r\n".encode()]
    if thread_id is not None:
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"message_thread_id\"\r\n\r\n{thread_id}\r\n".encode())
    chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{path.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode() + path.read_bytes() + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=b"".join(chunks), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # nosec B310 - fixed HTTPS Telegram endpoint
            return bool(json.loads(response.read().decode("utf-8")).get("ok"))
    except (OSError, ValueError, json.JSONDecodeError):
        logging.error("Expression image upload failed", exc_info=True)
        return False


def deliver_expression(token: str, chat_id: str, text: str, db: sqlite3.Connection, session_id: str) -> None:
    mode = get_meta(db, expression_mode_key(chat_id, session_id), "off")
    if mode == "off":
        return
    row = db.execute("SELECT character_file FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, session_id)).fetchone()
    character_file = str(row[0]) if row else DEFAULT_CHARACTER_FILE
    assets = discover_expression_assets(character_file)
    if mode == "auto":
        selected = classify_expression(text, assets)
    elif mode in assets:
        selected = mode
    else:
        return
    path = assets.get(selected) or assets.get("neutral") or _fixed_avatar(character_file)
    if path is None:
        return
    effective = f"{selected or 'fixed'}:{path.resolve()}"
    if get_meta(db, expression_last_key(chat_id, session_id), "") == effective:
        return
    if _send_expression_photo(token, chat_id, path):
        set_meta(db, expression_last_key(chat_id, session_id), effective)
        db.commit()


def expression_menu_markup(character_file: str, current: str = "off", page: int = 0) -> dict:
    assets = discover_expression_assets(character_file)
    page_options, current_page, total_pages = panel_page(list(assets.items()), page)
    rows = []
    for label, _path in page_options:
        mark = "✅ " if current == label else ""
        rows.append([{"text": mark + label.title(), "callback_data": f"expression:{label}"}])
    navigation = panel_navigation("expression", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([{"text": ("✅ " if current == "auto" else "") + "✨ Automatic", "callback_data": "expression:auto"}, {"text": ("✅ " if current == "off" else "") + "🚫 Off", "callback_data": "expression:off"}])
    rows.append([{"text": "❌ Close", "callback_data": "expression:cancel"}])
    return {"inline_keyboard": rows}


def send_expression_menu(token: str, chat_id: str, session: dict, db: sqlite3.Connection, message_id: int | None = None, page: int = 0) -> None:
    current = get_meta(db, expression_mode_key(chat_id, session["session_id"]), "off")
    assets = discover_expression_assets(session["character_file"])
    _page_options, current_page, total_pages = panel_page(list(assets.items()), page)
    page_text = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    available = ", ".join(label.title() for label in assets) or "none; fixed avatar fallback available"
    payload = {"chat_id": chat_id, "text": f"Expression mode: {current}{page_text}\nAvailable native assets: {available}\nAutomatic mode classifies locally and sends an image only when it changes.", "reply_markup": expression_menu_markup(session["character_file"], current, current_page)}
    method = "sendMessage"
    if message_id:
        method = "editMessageText"
        payload["message_id"] = message_id
    telegram_request(token, method, payload)


_bind_module_dependencies(__name__, globals())

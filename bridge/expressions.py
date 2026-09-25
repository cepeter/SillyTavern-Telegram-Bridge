"""SillyTavern-compatible expression sprites for Telegram delivery."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from bridge.card_content import safe_character_path
from bridge.database import get_meta, set_meta
from bridge.delivery_port import DeliveryPort
from bridge.limits import IMAGE_MAX_BYTES
from bridge.panel_utils import panel_navigation, panel_page
from bridge.settings import AppSettings
from bridge.topic_scope import parse_topic_scope

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


def _expression_roots(character_file: str, *, app_settings: AppSettings) -> list[Path]:
    card = safe_character_path(character_file, app_settings=app_settings)
    if card is None:
        card = app_settings.card_file
    roots = [card.with_suffix("")]
    default_root = app_settings.sillytavern_dir / "public" / "img" / "default-expressions"
    if default_root.exists():
        roots.append(default_root)
    return roots


def discover_expression_assets(character_file: str, *, app_settings: AppSettings) -> dict[str, Path]:
    """Discover native ST sprites in <characters>/<card stem> and defaults."""
    assets: dict[str, Path] = {}
    for root in _expression_roots(character_file, app_settings=app_settings):
        if not root.is_dir():
            continue
        for path in sorted(
            root.iterdir(),
            key=lambda item: (
                expression_label(item),
                0 if item.stem.casefold() == expression_label(item) else 1,
                item.name.casefold(),
            ),
        ):
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


def _fixed_avatar(character_file: str, *, app_settings: AppSettings) -> Path | None:
    path = safe_character_path(character_file, app_settings=app_settings)
    return path if path and path.is_file() else (app_settings.card_file if app_settings.card_file.is_file() else None)


def _send_expression_photo(token: str, chat_id: str, path: Path) -> bool:
    if path is None or path.stat().st_size > IMAGE_MAX_BYTES:
        logging.warning("Expression asset missing or exceeds size limit: %s", path)
        return False
    boundary = f"----BridgeExpression{int(time.time() * 1000000)}"
    real_chat_id, thread_id = parse_topic_scope(chat_id)
    chunks = [f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{real_chat_id}\r\n'.encode()]
    if thread_id is not None:
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="message_thread_id"\r\n\r\n{thread_id}\r\n'.encode()
        )
    chunks.append(
        (
            "--"
            f"""{boundary}"""
            '\r\nContent-Disposition: form-data; name="photo"; filename="'
            f"""{path.name}"""
            '"\r\nContent-Type: application/octet-stream\r\n\r\n'
        ).encode()
        + path.read_bytes()
        + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 -- fixed Telegram HTTPS endpoint; token is not a URL
            return bool(json.loads(response.read().decode("utf-8")).get("ok"))
    except (OSError, ValueError, json.JSONDecodeError):
        logging.error("Expression image upload failed", exc_info=True)
        return False


def deliver_expression(
    token: str, chat_id: str, text: str, db: sqlite3.Connection, session_id: str, *, app_settings: AppSettings
) -> None:
    mode = get_meta(db, expression_mode_key(chat_id, session_id), "off")
    if mode == "off":
        return
    row = db.execute(
        "SELECT character_file FROM sessions WHERE chat_id=? AND session_id=?", (chat_id, session_id)
    ).fetchone()
    character_file = str(row[0]) if row else app_settings.default_character_file
    assets = discover_expression_assets(character_file, app_settings=app_settings)
    if mode == "auto":
        selected = classify_expression(text, assets)
    elif mode in assets:
        selected = mode
    else:
        return
    path = assets.get(selected) or assets.get("neutral") or _fixed_avatar(character_file, app_settings=app_settings)
    if path is None:
        return
    effective = f"{selected or 'fixed'}:{path.resolve()}"
    if get_meta(db, expression_last_key(chat_id, session_id), "") == effective:
        return
    if _send_expression_photo(token, chat_id, path):
        set_meta(db, expression_last_key(chat_id, session_id), effective)
        db.commit()


def expression_menu_markup(
    character_file: str, current: str = "off", page: int = 0, *, app_settings: AppSettings
) -> dict:
    assets = discover_expression_assets(character_file, app_settings=app_settings)
    page_options, current_page, total_pages = panel_page(list(assets.items()), page)
    rows = []
    for label, _path in page_options:
        mark = "✅ " if current == label else ""
        rows.append([{"text": mark + label.title(), "callback_data": f"expression:{label}"}])
    navigation = panel_navigation("expression", current_page, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            {"text": ("✅ " if current == "auto" else "") + "✨ Automatic", "callback_data": "expression:auto"},
            {"text": ("✅ " if current == "off" else "") + "🚫 Off", "callback_data": "expression:off"},
        ]
    )
    rows.append([{"text": "❌ Close", "callback_data": "expression:cancel"}])
    return {"inline_keyboard": rows}


def send_expression_menu(
    token: str,
    chat_id: str,
    session: dict,
    db: sqlite3.Connection,
    message_id: int | None = None,
    page: int = 0,
    *,
    delivery_port: DeliveryPort,
    request_context,
) -> None:
    current = get_meta(db, expression_mode_key(chat_id, session["session_id"]), "off")
    assets = discover_expression_assets(session["character_file"], app_settings=request_context.app_settings)
    _page_options, current_page, total_pages = panel_page(list(assets.items()), page)
    page_text = f" (page {current_page + 1}/{total_pages})" if total_pages > 1 else ""
    available = ", ".join(label.title() for label in assets) or "none; fixed avatar fallback available"
    payload = {
        "chat_id": chat_id,
        "text": (
            "Expression mode: "
            f"""{current}"""
            f"""{page_text}"""
            "\nAvailable native assets: "
            f"""{available}"""
            "\nAutomatic mode classifies locally and sends an image only when it "
            "changes."
        ),
        "reply_markup": expression_menu_markup(
            session["character_file"], current, current_page, app_settings=request_context.app_settings
        ),
    }
    method = "sendMessage"
    if message_id:
        method = "editMessageText"
        payload["message_id"] = message_id
    delivery_port.send_panel_request(token, method, payload, request_context=request_context)

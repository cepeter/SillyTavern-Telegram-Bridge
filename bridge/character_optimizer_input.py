"""Pending free-text guidance for character optimization."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from bridge.callbacks import close_panel_message, discard_panel_binding
from bridge.card_content import card_fields_from_file, safe_character_path
from bridge.character_optimizer import prepare_character_optimization
from bridge.character_optimizer_panels import (
    format_character_optimizer_base,
    send_character_optimize_result,
)
from bridge.limits import PENDING_SETTINGS_TTL_SECONDS
from bridge.metadata import get_meta, set_meta
from bridge.provider_port import ProviderPort
from bridge.request_types import RequestContext
from bridge.telegram import delete_pending_input_prompts, send_text

_META_PREFIX = "character_optimizer_input:"
_MAX_SUGGESTION_CHARS = 2000


def optimizer_suggestion_key(chat_id: str, actor_id: str) -> str:
    return f"{_META_PREFIX}{chat_id}:{actor_id}"


def _clear_pending(db: sqlite3.Connection, token: str, chat_id: str, meta_key: str, state: dict) -> None:
    delete_pending_input_prompts(token, chat_id, state)
    set_meta(db, meta_key, "")


def start_character_optimizer_suggestion_input(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    filename: str,
    expected_digest: str,
    callback: dict,
    *,
    base_fields: dict[str, str] | None = None,
    request_context: RequestContext,
) -> None:
    """Close the panel and request one actor/session/digest-bound editing suggestion."""
    if not request_context.actor_id:
        raise ValueError("Optimizer suggestion requires an identified user")
    path = safe_character_path(filename, app_settings=request_context.app_settings)
    if path is None or hashlib.sha256(path.read_bytes()).hexdigest() != expected_digest:
        raise ValueError("Character changed; reopen the optimizer")
    meta_key = optimizer_suggestion_key(chat_id, request_context.actor_id)
    previous_raw = get_meta(db, meta_key, "")
    if previous_raw:
        try:
            previous = json.loads(previous_raw)
        except (TypeError, json.JSONDecodeError):
            previous = {}
        if isinstance(previous, dict) and previous:
            _clear_pending(db, token, chat_id, meta_key, previous)
    revision_base = dict(base_fields or {})
    if any(not isinstance(value, str) for value in revision_base.values()):
        raise ValueError("invalid optimizer revision base")
    installed = card_fields_from_file(filename, app_settings=request_context.app_settings)
    display_fields = dict(installed)
    display_fields.update(revision_base)
    state = {
        "session_id": request_context.session_id,
        "actor_id": request_context.actor_id,
        "character_file": filename,
        "expected_digest": expected_digest,
        "base_fields": revision_base,
        "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS,
    }
    message_id = (callback.get("message") or callback).get("message_id")
    discard_panel_binding(db, chat_id, message_id)
    close_panel_message(db, token, chat_id, callback)
    prompt = (
        format_character_optimizer_base(str(installed.get("name", "") or filename), display_fields)
        + "\n\nSend your optimizer suggestion (up to 2,000 characters). "
        "Example: make her more sarcastic, preserve the backstory, and shorten the first message."
        "\n\nSend /cancel to cancel."
    )
    state["prompt_message_ids"] = send_text(token, chat_id, prompt)
    set_meta(db, meta_key, json.dumps(state, ensure_ascii=False))


def handle_character_optimizer_suggestion_input(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict,
    stripped: str,
    state: dict,
    *,
    provider_port: ProviderPort,
    request_context: RequestContext,
) -> bool:
    """Consume a pending optimizer suggestion before ordinary message generation."""
    meta_key = optimizer_suggestion_key(chat_id, request_context.actor_id)
    if str(state.get("actor_id") or "") != request_context.actor_id:
        return False
    if (
        str(state.get("session_id") or "") != request_context.session_id
        or float(state.get("expires_at") or 0) < time.time()
    ):
        _clear_pending(db, token, chat_id, meta_key, state)
        return False
    value = stripped.strip()
    if value.casefold() in {"/cancel", "cancel"}:
        _clear_pending(db, token, chat_id, meta_key, state)
        send_text(token, chat_id, "Optimizer suggestion cancelled.")
        return True
    if not value or len(value) > _MAX_SUGGESTION_CHARS:
        state["prompt_message_ids"] = list(state.get("prompt_message_ids") or []) + send_text(
            token, chat_id, "Suggestion must be 1–2,000 characters. Try again or send /cancel."
        )
        set_meta(db, meta_key, json.dumps(state, ensure_ascii=False))
        return True
    filename = str(state.get("character_file") or "")
    expected_digest = str(state.get("expected_digest") or "")
    path = safe_character_path(filename, app_settings=request_context.app_settings)
    if path is None or hashlib.sha256(path.read_bytes()).hexdigest() != expected_digest:
        _clear_pending(db, token, chat_id, meta_key, state)
        send_text(token, chat_id, "Character changed since the suggestion panel opened. Reopen the optimizer.")
        return True
    try:
        draft = prepare_character_optimization(
            db,
            chat_id,
            session,
            filename,
            provider_port=provider_port,
            request_context=request_context,
            suggestion=value,
            expected_digest=expected_digest,
            base_fields=dict(state.get("base_fields") or {}),
        )
    except (OSError, ValueError) as exc:
        send_text(token, chat_id, f"{exc} Try again or send /cancel.")
        return True
    _clear_pending(db, token, chat_id, meta_key, state)
    send_character_optimize_result(
        token,
        chat_id,
        draft.filename,
        draft.fields,
        draft.nonce,
        request_context=request_context,
    )
    return True

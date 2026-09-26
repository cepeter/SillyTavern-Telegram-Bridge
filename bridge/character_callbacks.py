"""Canonical character callbacks owner."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import close_panel_message, discard_panel_binding
from bridge.card_content import card_fields_from_file, safe_character_path
from bridge.cards import (
    send_character_delete_confirm,
    send_character_delete_menu,
    send_character_info_menu,
    send_character_menu,
    send_session_menu,
)
from bridge.character_optimizer import prepare_character_optimization
from bridge.character_optimizer_input import start_character_optimizer_suggestion_input
from bridge.character_optimizer_panels import (
    send_character_optimize_menu,
    send_character_optimize_options,
    send_character_optimize_result,
)
from bridge.character_proposals import load_character_proposal
from bridge.character_quality import rank_character
from bridge.group_service import GroupService
from bridge.group_setup import apply_group_setup_character
from bridge.limits import PENDING_SETTINGS_TTL_SECONDS
from bridge.metadata import get_meta, set_meta
from bridge.native_imports import (
    apply_character_proposal,
    character_delete_references,
    verify_character_card_backup,
)
from bridge.operations import begin_operation, record_operation
from bridge.provider_port import ProviderPort
from bridge.session_core import list_sessions
from bridge.telegram import send_panel_photo, send_panel_request


def _character_info_text(info: dict, filename: str) -> str:
    return (
        "Character: "
        f"{info['name']}"
        "\nFile: "
        f"{filename}"
        "\nDescription: "
        f"{len(info['description'])}"
        " chars\nPersonality: "
        f"{len(info['personality'])}"
        " chars\nScenario: "
        f"{len(info['scenario'])}"
        " chars\nFirst message: "
        f"{len(info['first_mes'])}"
        " chars"
    )


def handle_character_callback(
    db,
    token,
    callback,
    answer_callback,
    data,
    chat_id,
    message,
    session,
    session_id,
    operation_id,
    *,
    group_service: GroupService,
    provider_port: ProviderPort,
    request_context,
):
    """Handle character selection, info, upload, and deletion callbacks."""
    message_id = message.get("message_id")
    if data.startswith("character:rank:"):
        answer_callback(token, str(callback.get("id", "")), "")
        return True
    if data == "character:protected":
        answer_callback(token, str(callback.get("id", "")), "Active/default character is protected")
        send_character_menu(token, chat_id, session["character_file"], message_id, request_context=request_context)
        return True
    if data == "character:menu":
        answer_callback(token, str(callback.get("id", "")), "Refreshed")
        send_character_menu(
            token, chat_id, session["character_file"], message.get("message_id"), request_context=request_context
        )
        return True
    if data == "character:info":
        answer_callback(token, str(callback.get("id", "")), "Info")
        send_character_info_menu(token, chat_id, message.get("message_id"), request_context=request_context)
        return True
    if data == "character:delete":
        answer_callback(token, str(callback.get("id", "")), "Delete")
        send_character_delete_menu(
            token, chat_id, session["character_file"], message.get("message_id"), request_context=request_context
        )
        return True
    if data == "character:upload":
        answer_callback(token, str(callback.get("id", "")), "Upload")
        send_panel_request(
            token,
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message.get("message_id"),
                "text": (
                    "Send the character card as a Telegram Document (PNG with SillyTavern "
                    "chara metadata). The upload will be validated and queued safely."
                ),
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "⬅️ Back", "callback_data": "character:menu"},
                            {"text": "❌ Close", "callback_data": "character:cancel"},
                        ]
                    ]
                },
            },
            request_context=request_context,
        )
        return True
    if data == "character:optimize":
        answer_callback(token, str(callback.get("id", "")), "Optimizer")
        send_character_optimize_menu(token, chat_id, message.get("message_id"), request_context=request_context)
        return True
    if data.startswith(
        ("characterupload:", "characteroptimizeapply:", "characteroptimizecancel:", "characteroptimizepreview:")
    ):
        try:
            parts = data.split(":")
            if parts[0] == "characteroptimizepreview" and len(parts) == 3:
                pending = load_character_proposal(db, chat_id, parts[1], request_context=request_context)
                if pending.kind != "optimize":
                    raise ValueError("invalid optimizer preview")
                send_character_optimize_result(
                    token,
                    chat_id,
                    pending.filename,
                    pending.fields,
                    pending.nonce,
                    message.get("message_id"),
                    int(parts[2]),
                    request_context=request_context,
                )
                return True
            if parts[0] == "characterupload" and len(parts) == 3:
                action, nonce = parts[1], parts[2]
            elif parts[0] in {"characteroptimizeapply", "characteroptimizecancel"} and len(parts) == 2:
                action = "apply" if parts[0] == "characteroptimizeapply" else "cancel"
                nonce = parts[1]
            else:
                raise ValueError("invalid or expired character confirmation")
            message_text, applied_filename = apply_character_proposal(
                db,
                chat_id,
                nonce,
                action,
                request_context=request_context,
            )
            if applied_filename:
                try:
                    rank_character(
                        db,
                        chat_id,
                        session,
                        card_fields_from_file(applied_filename, app_settings=request_context.app_settings),
                        applied_filename,
                        provider_port=provider_port,
                        app_settings=request_context.app_settings,
                    )
                except (OSError, ValueError, sqlite3.Error):
                    logging.warning("Character applied; optional ranking was unavailable")
        except (ValueError, OSError) as exc:
            message_text = str(exc) if isinstance(exc, ValueError) else "Character update failed; reopen the preview."
            logging.warning("Character proposal was not applied or could not be completed")
        answer_callback(token, str(callback.get("id", "")), "Character proposal processed")
        send_panel_request(
            token,
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message.get("message_id"),
                "text": message_text,
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "Character menu", "callback_data": "character:menu"},
                            {"text": "Close", "callback_data": "character:cancel"},
                        ]
                    ]
                },
            },
            request_context=request_context,
        )
        return True
    if data.startswith("characteroptimizerefine:"):
        nonce = data.split(":", 1)[1]
        try:
            pending = load_character_proposal(db, chat_id, nonce, request_context=request_context)
            if pending.kind != "optimize":
                raise ValueError("invalid optimizer preview")
            start_character_optimizer_suggestion_input(
                db,
                token,
                chat_id,
                pending.filename,
                pending.expected_digest,
                callback,
                base_fields=pending.fields,
                request_context=request_context,
            )
            answer_callback(token, str(callback.get("id", "")), "Send optimizer suggestion")
        except (OSError, ValueError):
            answer_callback(token, str(callback.get("id", "")), "Optimizer preview expired; reopen it")
        return True
    if data.startswith(("characteroptimizeauto:", "characteroptimizemanual:")):
        action, value = data.split(":", 1)
        filename = resolve_dynamic_callback_token(value, "character", chat_id, db=db) or ""
        path = safe_character_path(filename, app_settings=request_context.app_settings)
        if not path or path.is_symlink():
            answer_callback(token, str(callback.get("id", "")), "Character choice expired")
            return True
        if action == "characteroptimizemanual":
            try:
                start_character_optimizer_suggestion_input(
                    db,
                    token,
                    chat_id,
                    filename,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    callback,
                    request_context=request_context,
                )
                answer_callback(token, str(callback.get("id", "")), "Send optimizer suggestion")
            except (OSError, ValueError):
                answer_callback(token, str(callback.get("id", "")), "Character changed; reopen the optimizer")
            return True
        answer_callback(token, str(callback.get("id", "")), "Optimizing")
        try:
            draft = prepare_character_optimization(
                db,
                chat_id,
                session,
                filename,
                provider_port=provider_port,
                request_context=request_context,
            )
            send_character_optimize_result(
                token,
                chat_id,
                draft.filename,
                draft.fields,
                draft.nonce,
                message.get("message_id"),
                request_context=request_context,
            )
        except (ValueError, OSError):
            send_panel_request(
                token,
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message.get("message_id"),
                    "text": "Optimization could not produce a safe preview. The installed card is unchanged.",
                    "reply_markup": {"inline_keyboard": [[{"text": "Back", "callback_data": "character:optimize"}]]},
                },
                request_context=request_context,
            )
        return True
    if data.startswith("characteroptimize:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            try:
                page = int(value.split(":", 1)[1])
            except ValueError:
                page = 0
            send_character_optimize_menu(
                token, chat_id, message.get("message_id"), page, request_context=request_context
            )
            return True
        filename = resolve_dynamic_callback_token(value, "character", chat_id, db=db) or ""
        path = safe_character_path(filename, app_settings=request_context.app_settings)
        if not path or path.is_symlink():
            answer_callback(token, str(callback.get("id", "")), "Character choice expired")
            return True
        answer_callback(token, str(callback.get("id", "")), "Optimizer options")
        send_character_optimize_options(
            token, chat_id, filename, message.get("message_id"), request_context=request_context
        )
        return True
    if data.startswith("characterinfo:"):
        value = data.split(":", 1)[1]
        if value == "back":
            answer_callback(token, str(callback.get("id", "")), "Back")
            close_panel_message(db, token, chat_id, callback)
            send_character_info_menu(token, chat_id, request_context=request_context)
            return True
        if value.startswith("page:"):
            send_character_info_menu(
                token, chat_id, message.get("message_id"), int(value.split(":", 1)[1]), request_context=request_context
            )
            return True
        filename = resolve_dynamic_callback_token(value, "character", chat_id, db=db) or ""
        path = safe_character_path(filename, app_settings=request_context.app_settings)
        if not path:
            answer_callback(token, str(callback.get("id", "")), "Character choice expired")
            return True
        info = card_fields_from_file(filename, app_settings=request_context.app_settings)
        answer_callback(token, str(callback.get("id", "")), "Info")
        text = _character_info_text(info, filename)
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "⬅️ Back", "callback_data": "characterinfo:back"},
                    {"text": "❌ Close", "callback_data": "character:cancel"},
                ]
            ]
        }
        try:
            send_panel_photo(
                token,
                chat_id,
                path,
                text,
                reply_markup,
                request_context=request_context,
            )
        except (OSError, RuntimeError, ValueError):
            logging.info("Character photo preview unavailable; using text-only info panel", exc_info=True)
            send_panel_request(
                token,
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message.get("message_id"),
                    "text": text,
                    "reply_markup": {
                        "inline_keyboard": [
                            [
                                {"text": "⬅️ Back", "callback_data": "character:info"},
                                {"text": "❌ Close", "callback_data": "character:cancel"},
                            ]
                        ]
                    },
                },
                request_context=request_context,
            )
            return True
        close_panel_message(db, token, chat_id, callback)
        return True
    if data.startswith("characterdelete:"):
        value = data.split(":", 1)[1]
        if value.startswith("page:"):
            send_character_delete_menu(
                token,
                chat_id,
                session["character_file"],
                message.get("message_id"),
                int(value.split(":", 1)[1]),
                request_context=request_context,
            )
            return True
        filename = resolve_dynamic_callback_token(value, "character", chat_id, db=db) or ""
        if (
            not safe_character_path(filename, app_settings=request_context.app_settings)
            or filename == session["character_file"]
        ):
            answer_callback(token, str(callback.get("id", "")), "Character choice invalid")
            return True
        answer_callback(token, str(callback.get("id", "")), "Confirm deletion")
        send_character_delete_confirm(
            token, chat_id, filename, message.get("message_id"), request_context=request_context
        )
        return True
    if data.startswith("characterdeleteconfirm:"):
        filename = resolve_dynamic_callback_token(data.split(":", 1)[1], "character", chat_id, db=db) or ""
        path = safe_character_path(filename, app_settings=request_context.app_settings)
        references = character_delete_references(db, filename) if path else []
        is_default = filename == request_context.app_settings.default_character_file or (
            path and path.resolve() == request_context.app_settings.card_file.resolve()
        )
        if not path or filename == session["character_file"] or is_default or references:
            reason = "active/default/referenced character" if path else "character not found"
            answer_callback(token, str(callback.get("id", "")), f"Deletion refused: {reason}")
            send_character_menu(token, chat_id, session["character_file"], message_id, request_context=request_context)
            return True
        try:
            verify_character_card_backup(path, path.read_bytes(), app_settings=request_context.app_settings)
        except OSError:
            answer_callback(token, str(callback.get("id", "")), "Deletion refused: backup verification failed")
            return True
        if operation_id is not None and not begin_operation(db, operation_id, "character_delete"):
            answer_callback(token, str(callback.get("id", "")), "Already processed")
            return True
        path.unlink(missing_ok=True)
        record_operation(db, operation_id, "character_delete")
        db.commit()
        answer_callback(token, str(callback.get("id", "")), "Deleted")
        send_character_menu(
            token, chat_id, session["character_file"], message.get("message_id"), request_context=request_context
        )
        return True
    if data.startswith("character:"):
        value = data.split(":", 1)[1]
        if not value.startswith("page:") and value != "cancel":
            value = resolve_dynamic_callback_token(value, "character", chat_id, db=db) or ""
        if value.startswith("page:"):
            answer_callback(token, str(callback.get("id", "")), "Page")
            send_character_menu(
                token,
                chat_id,
                session["character_file"],
                message.get("message_id"),
                int(value.split(":", 1)[1]),
                request_context=request_context,
            )
            return True
        if value == "cancel":
            answer_callback(token, str(callback.get("id", "")), "Cancelled")
            if group_service.setup_state(db, chat_id, session_id):
                set_meta(db, f"group_setup:{chat_id}", "")
            if get_meta(db, f"character_session_input:{chat_id}", ""):
                set_meta(db, f"character_session_input:{chat_id}", "")
            discard_panel_binding(db, chat_id, message.get("message_id"))
            close_panel_message(db, token, chat_id, callback)
        elif safe_character_path(value, app_settings=request_context.app_settings):
            character_name = card_fields_from_file(value, app_settings=request_context.app_settings)["name"]
            setup = group_service.setup_state(db, chat_id, session_id)
            if setup and setup.get("stage") == "character":
                return apply_group_setup_character(
                    db,
                    token,
                    callback,
                    answer_callback,
                    chat_id,
                    message,
                    session_id,
                    operation_id,
                    value,
                    character_name,
                    group_service=group_service,
                    request_context=request_context,
                )
            set_meta(
                db,
                f"character_session_input:{chat_id}",
                json.dumps(
                    {
                        "character_file": Path(value).name,
                        "character_name": character_name,
                        "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS,
                    }
                ),
            )
            answer_callback(token, str(callback.get("id", "")), "Choose session")
            discard_panel_binding(db, chat_id, message.get("message_id"))
            close_panel_message(db, token, chat_id, callback)
            send_session_menu(token, chat_id, list_sessions(db, chat_id), session_id, request_context=request_context)
        else:
            answer_callback(token, str(callback.get("id", "")), "Character not found")
        return True
    return False

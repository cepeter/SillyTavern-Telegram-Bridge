"""Canonical enum callbacks owner."""

from __future__ import annotations

import json
import sqlite3
import time

from bridge.callback_tokens import resolve_dynamic_callback_token
from bridge.callbacks import close_panel_message, discard_panel_binding
from bridge.cards import send_panel_message
from bridge.config import GENERATION_DEFAULTS, REASONING_LEVELS
from bridge.databank_commands import handle_data_bank_command
from bridge.databank_panels import (
    send_databank_menu,
    send_databank_remove_confirm,
    send_databank_remove_menu,
    send_databank_versions_menu,
)
from bridge.generation_settings import update_generation_settings
from bridge.humanizer_settings import normalize_humanizer
from bridge.input_flow_service import InputFlowService
from bridge.language import normalize_stt_language
from bridge.limits import PENDING_SETTINGS_TTL_SECONDS
from bridge.memory_panels import send_memory_menu
from bridge.metadata import set_meta
from bridge.preset_actions import apply_preset_action
from bridge.preset_panels import send_preset_delete_confirm, send_preset_delete_menu, send_preset_menu
from bridge.rag_service import RagService
from bridge.reset_panel import reset_confirmation_request
from bridge.session_core import update_session
from bridge.settings_panels import send_settings_menu, send_stream_menu
from bridge.sqlite_store import write_transaction
from bridge.telegram import send_text
from bridge.voice_panels import send_stt_language_menu, send_stt_model_menu, send_voice_input_menu, send_voice_menu


def handle_enum_callback(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict[str, str],
    data: str,
    message: dict,
    *,
    input_flow_service: InputFlowService,
    request_context,
    rag_service: RagService,
) -> None:
    message_id = message.get("message_id")
    if data == "enum:close":
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(db, token, chat_id, {"message": message})
        return
    if data == "enum:stscript:cancel":
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(db, token, chat_id, {"message": message})
        return
    if data == "enum:stscript:reset":
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(db, token, chat_id, {"message": message})
        _method, payload = reset_confirmation_request(chat_id)
        send_panel_message(
            token,
            chat_id,
            payload["text"],
            payload["reply_markup"],
            request_context=request_context,
        )
        return
    parts = data.split(":", 2)
    if data.startswith("enum:settings:input:"):
        key = data.rsplit(":", 1)[1]
        prompts = {
            "temperature": "Send temperature (0–2).",
            "max_tokens": "Send max_tokens (1–16000).",
            "top_p": "Send top_p (0–1).",
            "frequency_penalty": "Send frequency_penalty (-2–2).",
            "presence_penalty": "Send presence_penalty (-2–2).",
            "reasoning_budget": "Send reasoning budget (0–32000).",
            "stop_sequences": "Send stop sequences as text; separate multiple stops with newlines.",
        }
        if key in prompts:
            pending_setting = {
                "key": key,
                "session_id": session["session_id"],
                "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS,
            }
            set_meta(db, f"settings_input:{chat_id}", json.dumps(pending_setting))
            discard_panel_binding(db, chat_id, message_id)
            close_panel_message(db, token, chat_id, {"message": message})
            pending_setting["prompt_message_ids"] = send_text(
                token, chat_id, prompts[key] + " Send /cancel to leave it unchanged."
            )
            set_meta(db, f"settings_input:{chat_id}", json.dumps(pending_setting))
        return
    if data == "enum:settings:reset":
        with write_transaction(db):
            update_generation_settings(db, chat_id, session["session_id"], **GENERATION_DEFAULTS)
            update_session(db, chat_id, session["session_id"], humanizer="off")
        send_settings_menu(token, chat_id, db, session["session_id"], message_id, request_context=request_context)
        return
    if data.startswith("enum:settings:reasoning:"):
        label = data.rsplit(":", 1)[1]
        budgets = REASONING_LEVELS
        if label in budgets:
            update_generation_settings(db, chat_id, session["session_id"], reasoning_budget=budgets[label])
        send_settings_menu(token, chat_id, db, session["session_id"], message_id, request_context=request_context)
        return
    if data.startswith("enum:humanizer:"):
        value = data.rsplit(":", 1)[1]
        try:
            update_session(db, chat_id, session["session_id"], humanizer=normalize_humanizer(value))
        except ValueError:
            send_text(token, chat_id, "Invalid Humanizer choice.")
            return
        send_settings_menu(token, chat_id, db, session["session_id"], message_id, request_context=request_context)
        return
    if data.startswith("enum:stream:"):
        value = parts[2]
        if value in {"on", "off"}:
            set_meta(db, f"stream_mode:{chat_id}", value)
        send_stream_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data.startswith("enum:voice:"):
        value = parts[2]
        if value in {"on", "off"}:
            set_meta(db, f"voice_mode:{chat_id}", "tts" if value == "on" else "off")
        send_voice_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data == "enum:stt:language":
        send_stt_language_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data == "enum:stt:language_input":
        pending_stt = {"session_id": session["session_id"], "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
        set_meta(db, f"stt_language_input:{chat_id}", json.dumps(pending_stt))
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(db, token, chat_id, {"message": message})
        pending_stt["prompt_message_ids"] = send_text(
            token, chat_id, "Send a 2–8 letter STT language code such as id, en, or ja. Send /cancel to cancel."
        )
        set_meta(db, f"stt_language_input:{chat_id}", json.dumps(pending_stt))
    elif data.startswith("enum:sttlanguagepage:"):
        send_stt_language_menu(token, chat_id, db, message_id, int(parts[2]), request_context=request_context)
    elif data.startswith("enum:sttlanguage:"):
        try:
            value = normalize_stt_language(parts[2])
        except ValueError:
            send_text(token, chat_id, "Invalid STT language choice.")
        else:
            set_meta(db, f"stt_language:{chat_id}", value)
            send_voice_input_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data == "enum:stt:model":
        send_stt_model_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data == "enum:stt:back":
        send_voice_input_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data.startswith("enum:stt:"):
        value = parts[2]
        if value in {"on", "off"}:
            set_meta(db, f"stt_mode:{chat_id}", value)
        send_voice_input_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data.startswith("enum:sttmodel:"):
        value = parts[2]
        if value in {"tiny", "base", "small"}:
            set_meta(db, f"stt_model:{chat_id}", value)
        send_voice_input_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data == "enum:memory:search":
        input_flow_service.start_text_action(
            db,
            token,
            chat_id,
            session["session_id"],
            "memory_search",
            "Send a query to search Hindsight memory for the active session.",
            {"message": message},
            request_context=request_context,
        )
    elif data == "enum:memory:scope":
        send_memory_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data == "enum:memory:back":
        send_memory_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data.startswith("enum:memory:"):
        value = parts[2]
        if value in {"on", "off"}:
            set_meta(db, f"memory_mode:{chat_id}", value)
        send_memory_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data.startswith("enum:memoryscope:"):
        if parts[2] == "session":
            set_meta(db, f"memory_scope:{chat_id}", "session")
        send_memory_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data == "enum:preset:save":
        pending_preset = {"session_id": session["session_id"], "expires_at": time.time() + PENDING_SETTINGS_TTL_SECONDS}
        set_meta(db, f"preset_save_input:{chat_id}", json.dumps(pending_preset))
        discard_panel_binding(db, chat_id, message_id)
        close_panel_message(db, token, chat_id, {"message": message})
        pending_preset["prompt_message_ids"] = send_text(
            token,
            chat_id,
            "Send a preset name (1–64 letters, numbers, hyphens, or underscores). Send /cancel to cancel.",
        )
        set_meta(db, f"preset_save_input:{chat_id}", json.dumps(pending_preset))
    elif data == "enum:preset:back":
        send_preset_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data == "enum:presetdelete":
        send_preset_delete_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data.startswith("enum:presetpage:"):
        send_preset_menu(token, chat_id, db, message_id, int(parts[2]), request_context=request_context)
    elif data.startswith("enum:presetdeletepage:"):
        send_preset_delete_menu(token, chat_id, db, message_id, int(parts[2]), request_context=request_context)
    elif data.startswith("enum:presetuse:"):
        apply_preset_action(
            db,
            token,
            chat_id,
            session["session_id"],
            "use",
            resolve_dynamic_callback_token(parts[2], "preset", chat_id, db=request_context.db) or "",
        )
        send_preset_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data.startswith("enum:presetdelconfirm:"):
        name = resolve_dynamic_callback_token(parts[2], "preset", chat_id, db=request_context.db) or ""
        apply_preset_action(db, token, chat_id, session["session_id"], "delete", name)
        send_preset_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data.startswith("enum:presetdel:"):
        name = resolve_dynamic_callback_token(parts[2], "preset", chat_id, db=request_context.db) or ""
        if name:
            send_preset_delete_confirm(token, chat_id, name, message_id, request_context=request_context)
        else:
            send_preset_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data == "enum:rag:search":
        input_flow_service.start_text_action(
            db,
            token,
            chat_id,
            session["session_id"],
            "databank_search",
            "Send a query to search the active Data Bank.",
            {"message": message},
            request_context=request_context,
        )
    elif data == "enum:rag:versions":
        send_databank_versions_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data.startswith("enum:ragversionspage:"):
        send_databank_versions_menu(token, chat_id, db, message_id, page=int(parts[2]), request_context=request_context)
    elif data.startswith("enum:ragversions:"):
        filename = resolve_dynamic_callback_token(parts[2], "rag_document", chat_id, db=request_context.db) or ""
        send_databank_versions_menu(token, chat_id, db, message_id, filename=filename, request_context=request_context)
    elif data.startswith("enum:ragactivate:"):
        raw = resolve_dynamic_callback_token(parts[2], "rag_version", chat_id, db=request_context.db) or ""
        filename, _, raw_version = raw.rpartition("|")
        try:
            version = int(raw_version)
        except ValueError:
            version = 0
        if filename and version > 0:
            rag_service.activate(db, chat_id, filename, version)
        send_databank_versions_menu(token, chat_id, db, message_id, filename=filename, request_context=request_context)
    elif data == "enum:rag:remove":
        send_databank_remove_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data == "enum:rag:back":
        send_databank_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data.startswith("enum:ragremovepage:"):
        send_databank_remove_menu(token, chat_id, db, message_id, int(parts[2]), request_context=request_context)
    elif data.startswith("enum:ragpage:"):
        send_databank_menu(token, chat_id, db, message_id, int(parts[2]), request_context=request_context)
    elif data.startswith("enum:ragremoveconfirm:"):
        filename = resolve_dynamic_callback_token(parts[2], "rag_document", chat_id, db=request_context.db) or ""
        if filename:
            handle_data_bank_command(
                db,
                token,
                chat_id,
                "/databank remove " + filename + " confirm",
                app_settings=request_context.app_settings,
                rag_service=rag_service,
            )
        send_databank_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data.startswith("enum:ragremove:"):
        filename = resolve_dynamic_callback_token(parts[2], "rag_document", chat_id, db=request_context.db) or ""
        if filename:
            send_databank_remove_confirm(token, chat_id, filename, message_id, request_context=request_context)
        else:
            send_databank_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data == "enum:rag:reindex":
        total, indexed = rag_service.reindex(db, chat_id)
        send_text(token, chat_id, f"Data Bank reindex complete: {indexed}/{total} chunks indexed.")
        send_databank_menu(token, chat_id, db, message_id, request_context=request_context)
    elif data.startswith("enum:rag:"):
        value = parts[2]
        if value in {"on", "off"}:
            set_meta(db, f"rag_mode:{chat_id}", value)
        elif value == "list":
            handle_data_bank_command(
                db, token, chat_id, "/databank list", app_settings=request_context.app_settings, rag_service=rag_service
            )
        send_databank_menu(token, chat_id, db, message_id, request_context=request_context)

"""Canonical input flows owner."""

from __future__ import annotations

import sqlite3

from bridge.card_content import card_fields_from_file
from bridge.group_service import GroupService
from bridge.memory_service import MemoryService
from bridge.metadata import set_meta
from bridge.pending_input import _pending_state
from bridge.persona_input import _handle_persona_input
from bridge.persona_service import PersonaService
from bridge.provider_port import ProviderPort
from bridge.rag_service import RagService
from bridge.settings_input import _handle_note_input, _handle_preset_input, _handle_settings_input, _handle_stt_input
from bridge.telegram import send_text
from bridge.text_action_input import _handle_text_action_input


def handle_pending_input(
    db: sqlite3.Connection,
    token: str,
    chat_id: str,
    session: dict,
    stripped: str,
    api_key: str = "",
    fields: dict | None = None,
    operation_id: int | None = None,
    *,
    handle_session_name,
    group_service: GroupService,
    provider_port: ProviderPort,
    memory_service: MemoryService,
    persona_service: PersonaService,
    request_context,
    rag_service: RagService,
) -> bool:
    """Consume one scoped pending-input message, including cancel and validation."""
    session_id = session["session_id"]
    world_upload = _pending_state(db, f"world_upload:{chat_id}", session_id, token, chat_id)
    if world_upload:
        if stripped.casefold() in {"/cancel", "cancel"}:
            set_meta(db, f"world_upload:{chat_id}", "")
            send_text(token, chat_id, "World Info upload cancelled.")
        else:
            send_text(token, chat_id, "Please send the World Info JSON as a document, or use /cancel.")
        return True
    text_action = _pending_state(db, f"text_action_input:{chat_id}", session_id, token, chat_id)
    if text_action:
        action_fields = (
            fields
            if fields is not None
            else card_fields_from_file(session["character_file"], app_settings=request_context.app_settings)
        )
        return _handle_text_action_input(
            db,
            token,
            api_key,
            chat_id,
            session,
            action_fields,
            stripped,
            text_action,
            operation_id,
            provider_port=provider_port,
            memory_service=memory_service,
            persona_service=persona_service,
            request_context=request_context,
            rag_service=rag_service,
        )
    session_name = _pending_state(db, f"session_name_input:{chat_id}", session_id, token, chat_id)
    if session_name:
        return handle_session_name(
            db,
            token,
            chat_id,
            session,
            stripped,
            session_name,
            operation_id,
            group_service=group_service,
            request_context=request_context,
        )
    settings = _pending_state(db, f"settings_input:{chat_id}", session_id, token, chat_id)
    if settings.get("key"):
        return _handle_settings_input(
            db, token, chat_id, session_id, stripped, settings, request_context=request_context
        )
    preset = _pending_state(db, f"preset_save_input:{chat_id}", session_id, token, chat_id)
    if preset:
        return _handle_preset_input(db, token, chat_id, session_id, stripped, preset, request_context=request_context)
    stt = _pending_state(db, f"stt_language_input:{chat_id}", session_id, token, chat_id)
    if stt:
        return _handle_stt_input(db, token, chat_id, stripped, stt, request_context=request_context)
    persona = _pending_state(db, f"persona_input:{chat_id}", session_id, token, chat_id)
    if persona:
        return _handle_persona_input(
            db,
            token,
            chat_id,
            session,
            stripped,
            persona,
            operation_id,
            persona_service=persona_service,
            request_context=request_context,
        )
    note = _pending_state(db, f"note_input:{chat_id}", session_id, token, chat_id)
    if note:
        return _handle_note_input(
            db, token, chat_id, session, stripped, note, operation_id, request_context=request_context
        )
    return False

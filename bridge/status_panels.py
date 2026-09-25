"""Canonical status panels owner."""

from __future__ import annotations

from pathlib import Path

from bridge.card_content import active_world_files, system_prompt_label
from bridge.expressions import expression_mode_key
from bridge.generation_settings import get_generation_settings
from bridge.group_service import GroupService
from bridge.humanize import humanizer_label
from bridge.language import response_language_label
from bridge.memory import get_session_summary
from bridge.memory_backend import memory_mode, memory_scope
from bridge.metadata import get_meta
from bridge.model_selection import task_model_for_session
from bridge.persona_sync import persona_name
from bridge.rag_core import data_bank_documents, rag_mode
from bridge.settings import AppSettings


def status_text(
    db,
    chat_id,
    session,
    fields,
    current_model,
    current_persona,
    *,
    group_service: GroupService,
    app_settings: AppSettings,
):
    count = db.execute(
        "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?",
        (chat_id, session["session_id"]),
    ).fetchone()[0]
    worlds = active_world_files(session["world_file"], app_settings=app_settings)
    world = ", ".join(Path(name).stem for name in worlds) if worlds else "off"
    persona = persona_name(current_persona, app_settings=app_settings) if current_persona else "off"
    note_state = "on" if session["author_note"] else "off"
    generation = get_generation_settings(db, chat_id, session["session_id"])
    summary, covered_until = get_session_summary(db, chat_id, session["session_id"])
    summary_state = f"on (through message {covered_until})" if summary else "off"
    rag_docs = data_bank_documents(db, chat_id)
    group = group_service.state(db, chat_id, session["session_id"])
    group_labels = group_service.member_labels(group["members"])
    group_state_text = f"{'on' if group['enabled'] else 'off'} ({', '.join(group_labels) if group_labels else 'none'})"
    expression_mode = get_meta(db, expression_mode_key(chat_id, session["session_id"]), "off")
    utility_model = task_model_for_session(db, chat_id, session, "utility", app_settings=app_settings)
    return (
        "📊 Session status\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎭 Character: {fields.get('name') or 'unknown'}\n"
        f"🗂️ Session: {session.get('title') or session['session_id']} ({session['session_id']})\n"
        f"💬 Stored messages: {count}\n"
        f"🤖 Model: {current_model}\n"
        f"🛠️ Utility model: {utility_model}\n"
        f"🌐 Response language: {response_language_label(session.get('response_language') or 'auto')}\n"
        f"🖋️ Humanizer: {humanizer_label(session.get('humanizer'))}\n\n"
        "📚 Native context\n"
        f"• Persona: {persona}\n"
        f"• World Info: {world}\n"
        f"• System Prompt: {system_prompt_label(session.get('system_prompt'), app_settings=app_settings)}\n"
        f"• Author's Note: {note_state}\n"
        f"• Expressions: {expression_mode}\n\n"
        "🧠 Memory and state\n"
        f"• Summary: {summary_state}\n"
        f"• Hindsight: {memory_mode(db, chat_id)} ({memory_scope(db, chat_id)})\n"
        f"• Data Bank RAG: {rag_mode(db, chat_id)} ({len(rag_docs)} documents)\n"
        f"• Group chat: {group_state_text}\n\n"
        "⚙️ Generation\n"
        f"• Temperature: {generation['temperature']}\n"
        f"• Max tokens: {generation['max_tokens']}\n"
        f"• Top P: {generation['top_p']}"
    )

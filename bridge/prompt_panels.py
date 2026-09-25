"""Canonical prompt panels owner."""

from __future__ import annotations

from bridge.cards import send_panel_message
from bridge.context_compaction import context_history_candidate_limit, context_input_budget_tokens
from bridge.group_service import GroupService
from bridge.memory import get_session_summary
from bridge.memory_backend import memory_mode, memory_scope
from bridge.prompt_diagnostics import prompt_diagnostics
from bridge.rag_core import data_bank_documents, rag_mode
from bridge.settings import AppSettings


def prompt_panel_text(
    db,
    chat_id,
    session,
    fields,
    section="overview",
    *,
    group_service: GroupService,
    memory_service,
    app_settings: AppSettings,
):
    if section == "budget":
        return (
            f"Prompt budget\nContext input budget: ~{context_input_budget_tokens(app_settings=app_settings)} tokens\n"
            f"History candidates: {context_history_candidate_limit(app_settings=app_settings)} messages\n"
            f"Session summary: {len(get_session_summary(db, chat_id, session['session_id'])[0])} chars"
        )
    if section == "memory":
        docs = data_bank_documents(db, chat_id)
        return (
            f"Prompt memory and retrieval\nHindsight: {memory_mode(db, chat_id)} / {memory_scope(db, chat_id)}\n"
            f"Data Bank: {rag_mode(db, chat_id)} / {len(docs)} documents"
        )
    if section == "group":
        group = group_service.state(db, chat_id, session["session_id"])
        return (
            "Prompt group context\nEnabled: "
            f"""{("on" if group["enabled"] else "off")}"""
            "\nMode: "
            f"""{group["mode"]}"""
            "\nMembers: "
            f"""{len(group["members"])}"""
        )
    return prompt_diagnostics(
        db,
        chat_id,
        session,
        fields,
        group_service=group_service,
        memory_service=memory_service,
        app_settings=app_settings,
    )


def send_prompt_menu(
    token,
    chat_id,
    db,
    session,
    fields,
    message_id=None,
    section="overview",
    *,
    group_service: GroupService,
    memory_service,
    request_context,
):
    labels = {
        "overview": "Prompt inspector",
        "budget": "Prompt budget",
        "memory": "Memory and retrieval",
        "group": "Group context",
    }
    markup = {
        "inline_keyboard": [
            [
                {"text": "📏 Budget", "callback_data": "prompt:budget"},
                {"text": "🧠 Memory / RAG", "callback_data": "prompt:memory"},
            ],
            [{"text": "👥 Group", "callback_data": "prompt:group"}],
            [
                {"text": "⬅️ Status", "callback_data": "prompt:status"},
                {"text": "❌ Close", "callback_data": "prompt:close"},
            ],
        ]
    }
    send_panel_message(
        token,
        chat_id,
        labels.get(section, labels["overview"])
        + "\n\n"
        + prompt_panel_text(
            db,
            chat_id,
            session,
            fields,
            section,
            group_service=group_service,
            memory_service=memory_service,
            app_settings=request_context.app_settings,
        ),
        markup,
        message_id,
        request_context=request_context,
    )

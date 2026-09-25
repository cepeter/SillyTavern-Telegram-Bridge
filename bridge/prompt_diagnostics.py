"""Canonical prompt diagnostics owner."""

from __future__ import annotations

import sqlite3

from bridge.context_compaction import context_history_candidate_limit, context_input_budget_tokens
from bridge.group_service import GroupService
from bridge.memory_backend import memory_mode, memory_scope
from bridge.memory_service import MemoryService
from bridge.rag_core import data_bank_documents, rag_mode
from bridge.settings import AppSettings


def prompt_diagnostics(
    db: sqlite3.Connection,
    chat_id: str,
    session: dict[str, str],
    fields: dict[str, str],
    *,
    group_service: GroupService,
    memory_service: MemoryService,
    app_settings: AppSettings,
) -> str:
    message_count = db.execute(
        "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?", (chat_id, session["session_id"])
    ).fetchone()[0]
    summary, covered_until = memory_service.summary_status(db, chat_id, session["session_id"])
    docs = data_bank_documents(db, chat_id)
    group = group_service.state(db, chat_id, session["session_id"])
    return (
        "Prompt inspector\nCharacter: "
        f"""{fields["name"]}"""
        "\nMessages: "
        f"""{message_count}"""
        "\nContext input budget: ~"
        f"""{context_input_budget_tokens(app_settings=app_settings)}"""
        " tokens\nHistory candidates: "
        f"""{context_history_candidate_limit(app_settings=app_settings)}"""
        " messages\nSession summary: "
        f"""{len(summary)}"""
        " chars (through row "
        f"""{covered_until}"""
        ")\nHindsight: "
        f"""{memory_mode(db, chat_id)}"""
        " / "
        f"""{memory_scope(db, chat_id)}"""
        "\nData Bank: "
        f"""{rag_mode(db, chat_id)}"""
        " / "
        f"""{len(docs)}"""
        " documents\nGroup: "
        f"""{("on" if group["enabled"] else "off")}"""
        " / mode="
        f"""{group["mode"]}"""
        " / members="
        f"""{len(group["members"])}"""
        "\nMacro support: char, user, random, pick, time, date, weekday\nWorld Info "
        "recursion: maximum 3 passes"
    )

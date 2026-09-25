"""Canonical databank panels owner."""

from __future__ import annotations

import sqlite3

from bridge.callback_tokens import dynamic_callback_token
from bridge.cards import send_panel_message
from bridge.panel_utils import panel_label, panel_page
from bridge.rag_query import rag_embedding_coverage, rag_mode
from bridge.rag_repository import data_bank_document_versions, data_bank_documents


def send_databank_menu(
    token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None, page: int = 0, *, request_context
) -> None:
    mode = rag_mode(db, chat_id)
    docs = data_bank_documents(db, chat_id)
    total_chunks, indexed_chunks = rag_embedding_coverage(db, chat_id, app_settings=request_context.app_settings)
    options = []
    seen = set()
    for row in docs:
        name = str(row[1])
        if name not in seen:
            seen.add(name)
            options.append(name)
    page_options, current_page, total_pages = panel_page(options, page)
    rows = []
    for name in page_options:
        callback_token = dynamic_callback_token("rag_document", name, chat_id, db=request_context.db)
        rows.append(
            [
                {"text": "📄 " + panel_label(name), "callback_data": "enum:ragversions:" + callback_token},
                {"text": "🗑️", "callback_data": "enum:ragremove:" + callback_token},
            ]
        )
    if total_pages > 1:
        navigation = []
        if current_page > 0:
            navigation.append({"text": "⬅️ Previous", "callback_data": f"enum:ragpage:{current_page - 1}"})
        if current_page < total_pages - 1:
            navigation.append({"text": "Next ➡️", "callback_data": f"enum:ragpage:{current_page + 1}"})
        rows.append(navigation)
    rows.append([{"text": ("✅ " if mode == "on" else "") + "RAG on", "callback_data": "enum:rag:on"}])
    rows.append([{"text": ("✅ " if mode == "off" else "") + "RAG off", "callback_data": "enum:rag:off"}])
    rows.extend(
        [
            [
                {"text": "🔎 Search", "callback_data": "enum:rag:search"},
                {"text": "📚 Versions", "callback_data": "enum:rag:versions"},
            ],
            [{"text": "🔄 Reindex embeddings", "callback_data": "enum:rag:reindex"}],
            [{"text": "❌ Close", "callback_data": "enum:close"}],
        ]
    )
    send_panel_message(
        token,
        chat_id,
        f"Data Bank RAG: {mode}\nDocuments: {len(options)}\nEmbedding coverage: {indexed_chunks}/{total_chunks}",
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_databank_remove_confirm(
    token: str, chat_id: str, filename: str, message_id: int | None = None, *, request_context
) -> None:
    callback_token = dynamic_callback_token("rag_document", filename, chat_id, db=request_context.db)
    markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Confirm remove", "callback_data": "enum:ragremoveconfirm:" + callback_token},
                {"text": "❌ Cancel", "callback_data": "enum:rag:back"},
            ]
        ]
    }
    send_panel_message(
        token,
        chat_id,
        (
            "Remove all Data Bank versions named '"
            f"""{panel_label(filename)}"""
            "'? Indexed chunks will also be removed. This cannot be undone."
        ),
        markup,
        message_id,
        request_context=request_context,
    )


def send_databank_versions_menu(
    token: str,
    chat_id: str,
    db: sqlite3.Connection,
    message_id: int | None = None,
    filename: str | None = None,
    page: int = 0,
    *,
    request_context,
) -> None:
    docs = data_bank_documents(db, chat_id)
    if not filename:
        options = [str(row[1]) for row in docs]
        page_options, current_page, total_pages = panel_page(options, page)
        rows = [
            [
                {
                    "text": panel_label(name),
                    "callback_data": "enum:ragversions:"
                    + dynamic_callback_token("rag_document", name, chat_id, db=request_context.db),
                }
            ]
            for name in page_options
        ]
        if total_pages > 1:
            navigation = []
            if current_page > 0:
                navigation.append({"text": "⬅️ Previous", "callback_data": f"enum:ragversionspage:{current_page - 1}"})
            if current_page < total_pages - 1:
                navigation.append({"text": "Next ➡️", "callback_data": f"enum:ragversionspage:{current_page + 1}"})
            rows.append(navigation)
        rows.append(
            [
                {"text": "⬅️ Data Bank", "callback_data": "enum:rag:back"},
                {"text": "❌ Close", "callback_data": "enum:close"},
            ]
        )
        send_panel_message(
            token,
            chat_id,
            f"Choose a document to inspect versions (page {current_page + 1}/{total_pages}):",
            {"inline_keyboard": rows},
            message_id,
            request_context=request_context,
        )
        return
    versions = data_bank_document_versions(db, chat_id, filename)
    rows = []
    for _document_id, version_number, active, byte_size, chunks in versions:
        token_value = dynamic_callback_token(
            "rag_version", f"{filename}|{version_number}", chat_id, db=request_context.db
        )
        label = f"{'✅ ' if active else ''}v{version_number} ({chunks} chunks, {byte_size} bytes)"
        rows.append([{"text": label, "callback_data": "enum:ragactivate:" + token_value}])
    rows.append(
        [
            {"text": "⬅️ Documents", "callback_data": "enum:rag:versions"},
            {"text": "❌ Close", "callback_data": "enum:close"},
        ]
    )
    send_panel_message(
        token,
        chat_id,
        f"Versions for {filename}:\nChoose one to activate.",
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )


def send_databank_remove_menu(
    token: str, chat_id: str, db: sqlite3.Connection, message_id: int | None = None, page: int = 0, *, request_context
) -> None:
    docs = data_bank_documents(db, chat_id)
    options = [str(row[1]) for row in docs]
    page_options, current_page, total_pages = panel_page(options, page)
    rows = [
        [
            {
                "text": panel_label(filename),
                "callback_data": "enum:ragremove:"
                + dynamic_callback_token("rag_document", filename, chat_id, db=request_context.db),
            }
        ]
        for filename in page_options
    ]
    if total_pages > 1:
        navigation = []
        if current_page > 0:
            navigation.append({"text": "⬅️ Previous", "callback_data": f"enum:ragremovepage:{current_page - 1}"})
        if current_page < total_pages - 1:
            navigation.append({"text": "Next ➡️", "callback_data": f"enum:ragremovepage:{current_page + 1}"})
        rows.append(navigation)
    rows.append(
        [{"text": "⬅️ Back", "callback_data": "enum:rag:back"}, {"text": "❌ Close", "callback_data": "enum:close"}]
    )
    send_panel_message(
        token,
        chat_id,
        f"Choose a document to remove (page {current_page + 1}/{total_pages}):",
        {"inline_keyboard": rows},
        message_id,
        request_context=request_context,
    )

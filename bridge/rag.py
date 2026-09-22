from __future__ import annotations

from bridge.common import (
    sqlite3,
)

from bridge.database import (
    set_meta,
)

from bridge.rag_core import (
    _embedding_row,
    extract_pdf_data_bank_text,
    extract_data_bank_text,
    split_data_bank_chunks,
    rag_embedding_namespace,
    embedding_norm,
    rag_embedding_headers,
    embed_rag_text,
    embed_rag_batch,
    rag_semantic_candidate_limit,
    backfill_rag_embedding_signatures,
    add_data_bank_document,
    cached_rag_embedding,
    retrieve_data_bank,
    rag_mode,
    rag_retrieval_bundle,
    rag_context_for_prompt,
    rag_citation_footer,
    data_bank_documents,
    data_bank_document_versions,
    activate_data_bank_version,
    delete_data_bank_documents,
    rag_embedding_coverage,
    reindex_data_bank_documents,
    DEFAULT_SEMANTIC_CANDIDATE_LIMIT,
    MAX_SEMANTIC_CANDIDATE_LIMIT,
    cosine_similarity,
    embedding_signature,
    semantic_candidate_chunk_ids,
)

def handle_data_bank_command(db: sqlite3.Connection, token: str, chat_id: str, command_text: str) -> None:
    parts = command_text.split(None, 3)
    argument = parts[1].casefold() if len(parts) > 1 else "status"
    if argument in {"on", "off"}:
        set_meta(db, f"rag_mode:{chat_id}", argument)
    if argument in {"status", "on", "off"}:
        docs = data_bank_documents(db, chat_id)
        send_text(token, chat_id, f"Data Bank RAG: {rag_mode(db, chat_id)}\nDocuments: {len(docs)}\nChunks: {sum(int(row[3]) for row in docs)}")
        return
    if argument == "reindex":
        filename = parts[2].strip() if len(parts) > 2 else None
        total, indexed = reindex_data_bank_documents(db, chat_id, filename)
        send_text(token, chat_id, f"Data Bank reindex complete: {indexed}/{total} chunks indexed for the current embedding namespace.")
        return
    if argument == "versions":
        filename = parts[2].strip() if len(parts) > 2 else ""
        if not filename:
            send_text(token, chat_id, "Use /databank versions <filename>.")
            return
        versions = data_bank_document_versions(db, chat_id, filename)
        if not versions:
            send_text(token, chat_id, f"No Data Bank document named {filename}.")
            return
        lines = [
            f"- v{version_number}{' (active)' if active else ''}: {chunks} chunks, {byte_size} bytes"
            for _document_id, version_number, active, byte_size, chunks in versions
        ]
        send_text(token, chat_id, f"Versions for {filename}:\n" + "\n".join(lines))
        return
    if argument == "activate":
        filename = parts[2].strip() if len(parts) > 2 else ""
        raw_version = parts[3].strip().casefold().removeprefix("v") if len(parts) > 3 else ""
        try:
            version_number = int(raw_version)
        except ValueError:
            send_text(token, chat_id, "Use /databank activate <filename> <version>.")
            return
        if activate_data_bank_version(db, chat_id, filename, version_number):
            send_text(token, chat_id, f"Activated {filename} v{version_number}.")
        else:
            send_text(token, chat_id, f"Version not found: {filename} v{version_number}.")
        return
    if argument == "remove":
        filename = parts[2].strip() if len(parts) > 2 else ""
        confirmed = len(parts) > 3 and parts[3].casefold() == "confirm"
        if not filename:
            send_text(token, chat_id, "Use /databank remove <filename> confirm.")
        elif not confirmed:
            send_text(token, chat_id, f"This deletes every Data Bank copy named {filename}. Repeat: /databank remove {filename} confirm")
        else:
            removed = delete_data_bank_documents(db, chat_id, filename)
            send_text(token, chat_id, f"Removed {removed} Data Bank document(s) named {filename}.")
        return
    if argument == "list":
        docs = data_bank_documents(db, chat_id)
        lines = [f"- {row[1]} ({row[3]} chunks)" for row in docs[:20]]
        send_text(token, chat_id, "Data Bank documents:\n" + ("\n".join(lines) if lines else "No documents uploaded."))
        return
    if argument == "search":
        query = parts[2].strip() if len(parts) > 2 else ""
        results = retrieve_data_bank(db, chat_id, query)
        text = "\n\n".join(f"[{filename}]\n{content}" for filename, content, _ in results)
        send_text(token, chat_id, "Data Bank search:\n" + (text[:MAX_TELEGRAM_LENGTH] if text else "No matching chunks found."))
        return
    send_text(token, chat_id, "Use /databank on, /databank off, /databank list, /databank search <query>, /databank versions <filename>, /databank activate <filename> <version>, or /databank remove <filename> confirm.")


# Explicit late imports replace transitional dependency injection.
from bridge.common import MAX_TELEGRAM_LENGTH
from bridge.telegram import send_text

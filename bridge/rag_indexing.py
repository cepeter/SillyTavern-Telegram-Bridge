"""Data Bank version/index use cases; embedding I/O precedes owned write scopes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from bridge import rag_repository as repository
from bridge.document_extraction import extract_data_bank_text, split_data_bank_chunks
from bridge.embedding_port import EmbeddingPort
from bridge.embedding_values import _embedding_row, embedding_norm, rag_embedding_namespace
from bridge.limits import RAG_MAX_FILE_BYTES
from bridge.settings import AppSettings
from bridge.sqlite_store import optimize_database, write_transaction


def add_data_bank_document(
    db: sqlite3.Connection,
    chat_id: str,
    filename: str,
    raw: bytes,
    *,
    app_settings: AppSettings,
    embedding_port: EmbeddingPort,
) -> tuple[str, int]:
    if len(raw) > RAG_MAX_FILE_BYTES:
        raise ValueError("Data Bank file exceeds 10 MB")
    text = extract_data_bank_text(filename, raw, app_settings=app_settings)
    if not text:
        raise ValueError("Data Bank file contains no readable text")
    document_id = hashlib.sha256(raw).hexdigest()
    existing = repository.document_chunk_count(db, chat_id, document_id)
    if existing is not None:
        return "duplicate", existing
    chunks = split_data_bank_chunks(text)
    namespace = rag_embedding_namespace(app_settings=app_settings)
    cache_keys = [namespace + ":content:" + hashlib.sha256(content.encode("utf-8")).hexdigest() for content in chunks]
    vector_cache: dict[str, list[float]] = {}
    pending_cache_rows: list[tuple[str, int, str, float]] = []
    for offset in range(0, len(chunks), 32):
        batch_keys = cache_keys[offset : offset + 32]
        for cache_key, vector_json in repository.cached_content_vectors(db, batch_keys):
            try:
                vector_cache[cache_key] = json.loads(vector_json)
            except json.JSONDecodeError:
                continue
        missing = [
            (index, chunks[index])
            for index, cache_key in enumerate(batch_keys, start=offset)
            if cache_key not in vector_cache
        ]
        for batch_start in range(0, len(missing), 32):
            selected = missing[batch_start : batch_start + 32]
            vectors = embedding_port.embed_batch([item[1] for item in selected])
            for (index, _), vector in zip(selected, vectors, strict=False):
                if vector:
                    cache_key = cache_keys[index]
                    vector_cache[cache_key] = vector
                    pending_cache_rows.append(
                        (cache_key, len(vector), json.dumps(vector, separators=(",", ":")), embedding_norm(vector))
                    )
    now = time.time()
    with write_transaction(db):
        # Recheck after external work; another importer may have committed first.
        existing = repository.document_chunk_count(db, chat_id, document_id)
        if existing is not None:
            return "duplicate", existing
        version_number = repository.next_document_version(db, chat_id, filename[:255])
        if pending_cache_rows:
            repository.store_embedding_cache(db, [(*row, now) for row in pending_cache_rows])
        repository.insert_document(db, chat_id, document_id, filename[:255], len(raw), len(chunks), version_number, now)
        for index, content in enumerate(chunks):
            chunk_id = repository.insert_chunk(db, chat_id, document_id, filename[:255], index, content)
            vector = vector_cache.get(cache_keys[index])
            if vector:
                repository.store_embeddings(db, [(chunk_id, *_embedding_row(namespace, vector))])
        repository.deactivate_other_versions(db, chat_id, filename[:255], document_id, now)
    return ("versioned" if version_number > 1 else "added"), len(chunks)


def activate_data_bank_version(
    db: sqlite3.Connection,
    chat_id: str,
    filename: str,
    version_number: int,
) -> bool:
    with write_transaction(db):
        target = repository.find_document_version(db, chat_id, filename, version_number)
        if target is None:
            return False
        repository.activate_document(db, chat_id, filename, target, time.time())
    return True


def delete_data_bank_documents(db: sqlite3.Connection, chat_id: str, filename: str) -> int:
    with write_transaction(db):
        removed = repository.delete_document_rows(db, chat_id, filename)
    if not db.in_transaction:
        optimize_database(db)
    return removed


def reindex_data_bank_documents(
    db: sqlite3.Connection,
    chat_id: str,
    filename: str | None = None,
    *,
    app_settings: AppSettings,
    embedding_port: EmbeddingPort,
) -> tuple[int, int]:
    namespace = rag_embedding_namespace(app_settings=app_settings)
    documents = repository.active_document_rows(db, chat_id, filename)
    total = indexed = 0
    for document_id, _name in documents:
        rows = repository.document_chunks(db, chat_id, document_id)
        missing: list[tuple[int, str]] = []
        for chunk_id, content in rows:
            total += 1
            if not repository.embedding_exists(db, chunk_id, namespace):
                missing.append((int(chunk_id), str(content)))
        for offset in range(0, len(missing), 32):
            batch = missing[offset : offset + 32]
            vectors = embedding_port.embed_batch([content for _, content in batch])
            rows_to_store = [
                (chunk_id, *_embedding_row(namespace, vector))
                for (chunk_id, _content), vector in zip(batch, vectors, strict=False)
                if vector
            ]
            if rows_to_store:
                with write_transaction(db):
                    repository.store_embeddings(db, rows_to_store)
                indexed += len(rows_to_store)
    return total, indexed

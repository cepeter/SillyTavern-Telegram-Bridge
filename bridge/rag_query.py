"""Hybrid RAG retrieval with an explicit embedding port and canonical SQL repository."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time

from bridge import rag_repository as repository
from bridge.embedding_port import EmbeddingPort
from bridge.embedding_values import embedding_norm, rag_embedding_namespace
from bridge.metadata import get_meta
from bridge.rag_retrieval import cosine_similarity, embedding_signature, semantic_candidate_chunk_ids
from bridge.settings import AppSettings
from bridge.sqlite_store import write_transaction


def rag_semantic_candidate_limit(*, app_settings: AppSettings) -> int:
    return app_settings.rag_semantic_candidates


def cached_rag_embedding(
    db: sqlite3.Connection,
    text: str,
    *,
    app_settings: AppSettings,
    embedding_port: EmbeddingPort,
) -> list[float] | None:
    cache_key = (
        rag_embedding_namespace(app_settings=app_settings)
        + ":query:"
        + hashlib.sha256(text[:6000].encode("utf-8")).hexdigest()
    )
    row = repository.query_vector_row(db, cache_key)
    if row:
        try:
            vector = json.loads(row[0])
            if len(vector) == app_settings.rag_embedding_dimensions:
                return [float(value) for value in vector]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    vector = embedding_port.embed(text)
    if vector:
        with write_transaction(db):
            repository.store_embedding_cache(
                db,
                [
                    (
                        cache_key,
                        len(vector),
                        json.dumps(vector, separators=(",", ":")),
                        embedding_norm(vector),
                        time.time(),
                    )
                ],
            )
    return vector


def retrieve_data_bank(
    db: sqlite3.Connection,
    chat_id: str,
    query: str,
    limit: int = 5,
    *,
    app_settings: AppSettings,
    embedding_port: EmbeddingPort,
) -> list[tuple[str, str, str]]:
    terms = re.findall(r"[^\W_]{2,}", query.casefold(), flags=re.UNICODE)[:12]
    if not terms or not repository.has_active_documents(db, chat_id):
        return []
    candidates: dict[int, tuple[str, str, str, float]] = {}
    match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    lexical_rows = repository.lexical_hits(db, chat_id, match)
    for rank, (chunk_id, filename, content, document_id, _score) in enumerate(lexical_rows):
        candidates[int(chunk_id)] = (str(filename), str(content), str(document_id), 0.35 / (1.0 + rank))
    query_vector = cached_rag_embedding(db, query, app_settings=app_settings, embedding_port=embedding_port)
    if query_vector:
        query_norm = embedding_norm(query_vector)
        namespace = rag_embedding_namespace(app_settings=app_settings)
        semantic_ids = semantic_candidate_chunk_ids(
            db,
            chat_id,
            namespace,
            [int(row[0]) for row in lexical_rows],
            query_signature=embedding_signature(query_vector),
            candidate_limit=rag_semantic_candidate_limit(app_settings=app_settings),
        )
        for chunk_id, content, filename, document_id, vector_json, vector_norm in repository.candidate_vectors(
            db,
            chat_id,
            namespace,
            semantic_ids,
        ):
            try:
                score = (
                    cosine_similarity(query_vector, json.loads(vector_json), query_norm, float(vector_norm))
                    if vector_norm
                    else cosine_similarity(query_vector, json.loads(vector_json))
                )
                semantic_score = (score + 1.0) / 2.0
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            item = candidates.get(int(chunk_id), (str(filename), str(content), str(document_id), 0.0))
            candidates[int(chunk_id)] = (*item[:3], item[3] + 0.65 * semantic_score)
    ranked = sorted(candidates.values(), key=lambda item: item[3], reverse=True)
    return [(item[0], item[1], item[2]) for item in ranked[:limit]]


def rag_mode(db: sqlite3.Connection, chat_id: str) -> str:
    return get_meta(db, f"rag_mode:{chat_id}", "on")


def rag_embedding_coverage(db: sqlite3.Connection, chat_id: str, *, app_settings: AppSettings) -> tuple[int, int]:
    return repository.embedding_coverage(db, chat_id, rag_embedding_namespace(app_settings=app_settings))

"""Explicit RAG construction for startup; leaf workflows receive the service."""

from __future__ import annotations

from functools import partial

from bridge.embedding_port import EmbeddingPort
from bridge.limits import RAG_MAX_CONTEXT_CHARS
from bridge.rag_indexing import (
    activate_data_bank_version,
    add_data_bank_document,
    delete_data_bank_documents,
    reindex_data_bank_documents,
)
from bridge.rag_query import rag_embedding_coverage, rag_mode, retrieve_data_bank
from bridge.rag_repository import data_bank_document_versions, data_bank_documents
from bridge.rag_service import RagService
from bridge.settings import AppSettings


def build_rag_service(*, app_settings: AppSettings, embedding_port: EmbeddingPort) -> RagService:
    return RagService(
        retrieve_backend=partial(retrieve_data_bank, app_settings=app_settings, embedding_port=embedding_port),
        add_backend=partial(add_data_bank_document, app_settings=app_settings, embedding_port=embedding_port),
        reindex_backend=partial(reindex_data_bank_documents, app_settings=app_settings, embedding_port=embedding_port),
        documents_backend=data_bank_documents,
        versions_backend=data_bank_document_versions,
        activate_backend=activate_data_bank_version,
        remove_backend=delete_data_bank_documents,
        coverage_backend=partial(rag_embedding_coverage, app_settings=app_settings),
        mode_backend=rag_mode,
        context_limit=RAG_MAX_CONTEXT_CHARS,
    )

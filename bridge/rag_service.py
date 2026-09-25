"""RAG application API with explicit collaborators and bounded prompt context."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from bridge.rag_contracts import (
    ActivateVersion,
    DocumentRow,
    EmbeddingCoverage,
    IndexDocument,
    ListDocuments,
    ListVersions,
    QueryChunks,
    RagBundle,
    ReadRagMode,
    ReindexDocuments,
    RemoveDocuments,
    SearchResult,
    VersionRow,
)


@dataclass(frozen=True)
class RagService:
    retrieve_backend: QueryChunks
    add_backend: IndexDocument
    reindex_backend: ReindexDocuments
    documents_backend: ListDocuments
    versions_backend: ListVersions
    activate_backend: ActivateVersion
    remove_backend: RemoveDocuments
    coverage_backend: EmbeddingCoverage
    mode_backend: ReadRagMode
    context_limit: int

    def add_document(self, db: sqlite3.Connection, chat_id: str, filename: str, raw: bytes) -> tuple[str, int]:
        return self.add_backend(db, chat_id, filename, raw)

    def retrieve(self, db: sqlite3.Connection, chat_id: str, query: str, limit: int = 5) -> list[SearchResult]:
        return self.retrieve_backend(db, chat_id, query, limit=limit)

    def documents(self, db: sqlite3.Connection, chat_id: str) -> list[DocumentRow]:
        return self.documents_backend(db, chat_id)

    def versions(self, db: sqlite3.Connection, chat_id: str, filename: str) -> list[VersionRow]:
        return self.versions_backend(db, chat_id, filename)

    def activate(self, db: sqlite3.Connection, chat_id: str, filename: str, version_number: int) -> bool:
        return self.activate_backend(db, chat_id, filename, version_number)

    def remove(self, db: sqlite3.Connection, chat_id: str, filename: str) -> int:
        return self.remove_backend(db, chat_id, filename)

    def reindex(self, db: sqlite3.Connection, chat_id: str, filename: str | None = None) -> tuple[int, int]:
        return self.reindex_backend(db, chat_id, filename)

    def coverage(self, db: sqlite3.Connection, chat_id: str) -> tuple[int, int]:
        return self.coverage_backend(db, chat_id)

    def mode(self, db: sqlite3.Connection, chat_id: str) -> str:
        return self.mode_backend(db, chat_id)

    def bundle(self, db: sqlite3.Connection, chat_id: str, query: str, limit: int = 5) -> RagBundle:
        if self.mode(db, chat_id) != "on":
            return {"results": [], "context": "", "sources": []}
        results = self.retrieve(db, chat_id, query, limit=limit)
        context_parts: list[str] = []
        sources: list[str] = []
        remaining = self.context_limit
        for filename, content, _document_id in results:
            piece = f"[{filename}]\n{content}"
            if remaining <= 0:
                break
            included = piece[:remaining]
            if included:
                context_parts.append(included)
                if filename not in sources:
                    sources.append(filename)
                remaining -= len(included)
        return {"results": results, "context": "\n\n".join(context_parts), "sources": sources}

    def context_for_prompt(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        query: str,
        bundle: Mapping[str, object] | None = None,
    ) -> str:
        return str((bundle or self.bundle(db, chat_id, query)).get("context") or "")

    def citation_footer(
        self,
        db: sqlite3.Connection,
        chat_id: str,
        query: str,
        bundle: Mapping[str, object] | None = None,
    ) -> str:
        sources = cast(Sequence[str], (bundle or self.bundle(db, chat_id, query)).get("sources") or [])
        return "\n\nSources: " + ", ".join(f"[{name}]" for name in sources) if sources else ""

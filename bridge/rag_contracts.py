"""Named RAG and embedding call contracts, independent of adapters and settings."""

from __future__ import annotations

import sqlite3
from typing import Protocol, TypedDict

Vector = list[float]
SearchResult = tuple[str, str, str]
DocumentRow = tuple[str, str, int, int]
VersionRow = tuple[str, int, int, int, int]


class RagBundle(TypedDict):
    results: list[SearchResult]
    context: str
    sources: list[str]


class EmbedText(Protocol):
    def __call__(self, text: str) -> Vector | None: ...


class EmbedBatch(Protocol):
    def __call__(self, texts: list[str]) -> list[Vector | None]: ...


class QueryChunks(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str, query: str, limit: int = 5) -> list[SearchResult]: ...


class IndexDocument(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str, filename: str, raw: bytes) -> tuple[str, int]: ...


class ReindexDocuments(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str, filename: str | None = None) -> tuple[int, int]: ...


class ListDocuments(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str) -> list[DocumentRow]: ...


class ListVersions(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str, filename: str) -> list[VersionRow]: ...


class ActivateVersion(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str, filename: str, version_number: int) -> bool: ...


class RemoveDocuments(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str, filename: str) -> int: ...


class EmbeddingCoverage(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str) -> tuple[int, int]: ...


class ReadRagMode(Protocol):
    def __call__(self, db: sqlite3.Connection, chat_id: str) -> str: ...

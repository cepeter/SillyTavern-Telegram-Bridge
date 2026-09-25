"""Canonical rag repository owner."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence

from bridge.repository_contracts import require_active_transaction


def data_bank_documents(db: sqlite3.Connection, chat_id: str) -> list[tuple[str, str, int, int]]:
    """Return only active document versions used by retrieval."""
    return db.execute(
        "SELECT document_id,filename,byte_size,chunk_count FROM data_bank_documents "
        "WHERE chat_id=? AND active=1 ORDER BY updated_at DESC",
        (chat_id,),
    ).fetchall()


def data_bank_document_versions(
    db: sqlite3.Connection,
    chat_id: str,
    filename: str,
) -> list[tuple[str, int, int, int, int]]:
    return db.execute(
        "SELECT document_id,version_number,active,byte_size,chunk_count "
        "FROM data_bank_documents WHERE chat_id=? AND filename=? "
        "ORDER BY version_number DESC",
        (chat_id, filename),
    ).fetchall()


def document_chunk_count(db: sqlite3.Connection, chat_id: str, document_id: str) -> int | None:
    row = db.execute(
        "SELECT chunk_count FROM data_bank_documents WHERE chat_id=? AND document_id=?", (chat_id, document_id)
    ).fetchone()
    return int(row[0]) if row else None


def next_document_version(db: sqlite3.Connection, chat_id: str, filename: str) -> int:
    row = db.execute(
        "SELECT COALESCE(MAX(version_number),0) FROM data_bank_documents WHERE chat_id=? AND filename=?",
        (chat_id, filename),
    ).fetchone()
    return int(row[0] or 0) + 1


def cached_content_vectors(db: sqlite3.Connection, keys: Sequence[str]) -> list[tuple[str, str]]:
    if not keys:
        return []
    placeholders = ",".join("?" for _ in keys)
    return db.execute(
        f"SELECT cache_key,vector_json FROM rag_embedding_cache WHERE cache_key IN ({placeholders})",  # noqa: S608 -- bound values only
        keys,
    ).fetchall()


def query_vector_row(db: sqlite3.Connection, key: str) -> tuple[str, float] | None:
    return db.execute("SELECT vector_json,vector_norm FROM rag_embedding_cache WHERE cache_key=?", (key,)).fetchone()


def store_embedding_cache(db: sqlite3.Connection, rows: Sequence[tuple[str, int, str, float, float]]) -> None:
    require_active_transaction(db)
    db.executemany(
        "INSERT OR REPLACE INTO rag_embedding_cache("
        "cache_key,dimensions,vector_json,vector_norm,created_at) VALUES(?,?,?,?,?)",
        rows,
    )


def insert_document(
    db: sqlite3.Connection,
    chat_id: str,
    document_id: str,
    filename: str,
    byte_size: int,
    chunk_count: int,
    version_number: int,
    now: float,
) -> None:
    require_active_transaction(db)
    db.execute(
        "INSERT INTO data_bank_documents("
        "chat_id,document_id,filename,byte_size,chunk_count,version_number,active,created_at,updated_at"
        ") VALUES(?,?,?,?,?,?,?,?,?)",
        (chat_id, document_id, filename, byte_size, chunk_count, version_number, 1, now, now),
    )


def insert_chunk(
    db: sqlite3.Connection,
    chat_id: str,
    document_id: str,
    filename: str,
    index: int,
    content: str,
) -> int:
    require_active_transaction(db)
    cursor = db.execute(
        "INSERT INTO data_bank_chunks(chat_id,document_id,chunk_index,content) VALUES(?,?,?,?)",
        (chat_id, document_id, index, content),
    )
    chunk_id = cursor.lastrowid
    if chunk_id is None:
        raise RuntimeError("SQLite did not return the inserted chunk ID")
    db.execute(
        "INSERT INTO data_bank_fts(content,chat_id,document_id,filename,chunk_id) VALUES(?,?,?,?,?)",
        (content, chat_id, document_id, filename, chunk_id),
    )
    return chunk_id


def store_embeddings(db: sqlite3.Connection, rows: Sequence[tuple[int, str, int, str, int, float]]) -> None:
    require_active_transaction(db)
    db.executemany(
        "INSERT OR REPLACE INTO data_bank_embeddings("
        "chunk_id,embedding_namespace,dimensions,vector_json,vector_signature,vector_norm"
        ") VALUES(?,?,?,?,?,?)",
        rows,
    )


def deactivate_other_versions(
    db: sqlite3.Connection,
    chat_id: str,
    filename: str,
    document_id: str,
    now: float,
) -> None:
    require_active_transaction(db)
    db.execute(
        "UPDATE data_bank_documents SET active=0,updated_at=? "
        "WHERE chat_id=? AND filename=? AND document_id<>? AND active=1",
        (now, chat_id, filename, document_id),
    )


def find_document_version(db: sqlite3.Connection, chat_id: str, filename: str, version: int) -> str | None:
    row = db.execute(
        "SELECT document_id FROM data_bank_documents WHERE chat_id=? AND filename=? AND version_number=?",
        (chat_id, filename, int(version)),
    ).fetchone()
    return str(row[0]) if row else None


def activate_document(db: sqlite3.Connection, chat_id: str, filename: str, document_id: str, now: float) -> None:
    require_active_transaction(db)
    db.execute(
        "UPDATE data_bank_documents SET active=0,updated_at=? WHERE chat_id=? AND filename=?", (now, chat_id, filename)
    )
    db.execute(
        "UPDATE data_bank_documents SET active=1,updated_at=? WHERE chat_id=? AND document_id=?",
        (now, chat_id, document_id),
    )


def delete_document_rows(db: sqlite3.Connection, chat_id: str, filename: str) -> int:
    require_active_transaction(db)
    documents = db.execute(
        "SELECT document_id FROM data_bank_documents WHERE chat_id=? AND filename=?", (chat_id, filename)
    ).fetchall()
    for (document_id,) in documents:
        db.execute("DELETE FROM data_bank_fts WHERE chat_id=? AND document_id=?", (chat_id, document_id))
        db.execute(
            "DELETE FROM data_bank_embeddings WHERE chunk_id IN "
            "(SELECT chunk_id FROM data_bank_chunks WHERE chat_id=? AND document_id=?)",
            (chat_id, document_id),
        )
        db.execute("DELETE FROM data_bank_chunks WHERE chat_id=? AND document_id=?", (chat_id, document_id))
        db.execute("DELETE FROM data_bank_documents WHERE chat_id=? AND document_id=?", (chat_id, document_id))
    return len(documents)


def has_active_documents(db: sqlite3.Connection, chat_id: str) -> bool:
    return (
        db.execute("SELECT 1 FROM data_bank_documents WHERE chat_id=? AND active=1 LIMIT 1", (chat_id,)).fetchone()
        is not None
    )


def lexical_hits(db: sqlite3.Connection, chat_id: str, match: str) -> list[tuple[int, str, str, str, float]]:
    return db.execute(
        "SELECT CAST(f.chunk_id AS INTEGER),f.filename,c.content,c.document_id,bm25(data_bank_fts) "
        "FROM data_bank_fts f "
        "JOIN data_bank_chunks c ON c.chunk_id=CAST(f.chunk_id AS INTEGER) "
        "JOIN data_bank_documents d ON d.chat_id=c.chat_id AND d.document_id=c.document_id "
        "WHERE f.chat_id=? AND d.active=1 AND data_bank_fts MATCH ? "
        "ORDER BY bm25(data_bank_fts) LIMIT 50",
        (chat_id, match),
    ).fetchall()


def candidate_probe(db: sqlite3.Connection, chat_id: str, namespace: str, limit: int) -> list[tuple[int]]:
    return db.execute(
        "SELECT e.chunk_id FROM data_bank_embeddings e "
        "JOIN data_bank_chunks c ON c.chunk_id=e.chunk_id "
        "JOIN data_bank_documents d ON d.chat_id=c.chat_id AND d.document_id=c.document_id "
        "WHERE c.chat_id=? AND d.active=1 AND e.embedding_namespace=? ORDER BY e.chunk_id LIMIT ?",
        (str(chat_id), str(namespace), limit),
    ).fetchall()


def candidate_neighbors(
    db: sqlite3.Connection,
    chat_id: str,
    namespace: str,
    lexical: Sequence[int],
    radius: int,
    limit: int,
) -> list[tuple[int, int]]:
    if not lexical:
        return []
    placeholders = ",".join("?" for _ in lexical)
    return db.execute(
        "SELECT DISTINCT n.chunk_id, ABS(n.chunk_index-hit.chunk_index) AS distance "  # noqa: S608 -- bound values only
        "FROM data_bank_chunks hit JOIN data_bank_chunks n "
        "ON n.chat_id=hit.chat_id AND n.document_id=hit.document_id "
        "JOIN data_bank_embeddings e ON e.chunk_id=n.chunk_id AND e.embedding_namespace=? "
        f"WHERE hit.chat_id=? AND hit.chunk_id IN ({placeholders}) "
        "AND n.chunk_index BETWEEN hit.chunk_index-? AND hit.chunk_index+? "
        "ORDER BY distance, n.chunk_id LIMIT ?",
        (str(namespace), str(chat_id), *lexical, radius, radius, limit),
    ).fetchall()


def signature_rows(db: sqlite3.Connection, chat_id: str, namespace: str) -> Iterator[tuple[int, int]]:
    cursor = db.execute(
        "SELECT e.chunk_id,e.vector_signature FROM data_bank_embeddings e "
        "JOIN data_bank_chunks c ON c.chunk_id=e.chunk_id "
        "JOIN data_bank_documents d ON d.chat_id=c.chat_id AND d.document_id=c.document_id "
        "WHERE c.chat_id=? AND d.active=1 AND e.embedding_namespace=?",
        (str(chat_id), str(namespace)),
    )
    try:
        yield from cursor
    finally:
        cursor.close()


def candidate_vectors(
    db: sqlite3.Connection,
    chat_id: str,
    namespace: str,
    ids: Sequence[int],
) -> list[tuple[int, str, str, str, str, float]]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return db.execute(
        "SELECT e.chunk_id,c.content,d.filename,c.document_id,e.vector_json,e.vector_norm "  # noqa: S608 -- bound values only
        "FROM data_bank_embeddings e JOIN data_bank_chunks c ON c.chunk_id=e.chunk_id "
        "JOIN data_bank_documents d ON d.chat_id=c.chat_id AND d.document_id=c.document_id "
        f"WHERE c.chat_id=? AND d.active=1 AND e.embedding_namespace=? AND e.chunk_id IN ({placeholders})",
        (chat_id, namespace, *ids),
    ).fetchall()


def active_document_rows(db: sqlite3.Connection, chat_id: str, filename: str | None) -> list[tuple[str, str]]:
    query = "SELECT document_id,filename FROM data_bank_documents WHERE chat_id=? AND active=1"
    params = [chat_id]
    if filename:
        query += " AND filename=?"
        params.append(filename)
    return db.execute(query, params).fetchall()


def document_chunks(db: sqlite3.Connection, chat_id: str, document_id: str) -> list[tuple[int, str]]:
    return db.execute(
        "SELECT chunk_id,content FROM data_bank_chunks WHERE chat_id=? AND document_id=? ORDER BY chunk_index",
        (chat_id, document_id),
    ).fetchall()


def embedding_exists(db: sqlite3.Connection, chunk_id: int, namespace: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM data_bank_embeddings WHERE chunk_id=? AND embedding_namespace=?", (chunk_id, namespace)
        ).fetchone()
        is not None
    )


def embedding_coverage(db: sqlite3.Connection, chat_id: str, namespace: str) -> tuple[int, int]:
    total = int(
        db.execute(
            "SELECT COALESCE(SUM(chunk_count),0) FROM data_bank_documents WHERE chat_id=? AND active=1", (chat_id,)
        ).fetchone()[0]
    )
    indexed = int(
        db.execute(
            "SELECT COUNT(*) FROM data_bank_embeddings e "
            "JOIN data_bank_chunks c ON c.chunk_id=e.chunk_id "
            "JOIN data_bank_documents d ON d.chat_id=c.chat_id AND d.document_id=c.document_id "
            "WHERE c.chat_id=? AND d.active=1 AND e.embedding_namespace=?",
            (chat_id, namespace),
        ).fetchone()[0]
    )
    return total, indexed

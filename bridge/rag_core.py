"""Canonical Data Bank extraction, indexing, embedding, and retrieval."""

from __future__ import annotations

import hashlib
import html
import io
import json
import logging
import math
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from defusedxml import ElementTree as ET

from bridge.limits import (
    RAG_CHUNK_CHARS,
    RAG_CHUNK_OVERLAP,
    RAG_MAX_CONTEXT_CHARS,
    RAG_MAX_FILE_BYTES,
    RAG_SUPPORTED_SUFFIXES,
)
from bridge.metadata import get_meta
from bridge.network_security import strict_urlopen, validate_provider_endpoint
from bridge.rag_retrieval import cosine_similarity, embedding_signature, semantic_candidate_chunk_ids
from bridge.settings import AppSettings
from bridge.sqlite_store import optimize_database, write_transaction


def extract_pdf_data_bank_text(raw: bytes, *, app_settings: AppSettings) -> str:
    parser = Path(__file__).with_name("pdf_parser.py")
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed interpreter and parser; document passed on stdin
            [
                sys.executable,
                "-I",
                str(parser),
                "--max-bytes",
                str(RAG_MAX_FILE_BYTES),
                "--max-pages",
                str(app_settings.rag_max_pdf_pages),
                "--max-chars",
                str(app_settings.rag_max_extracted_chars),
            ],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=app_settings.rag_pdf_parse_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("PDF parsing timed out") from exc
    except OSError as exc:
        raise ValueError("PDF parser worker is unavailable") from exc
    try:
        result = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("PDF parser returned invalid output") from exc
    if not result.get("ok"):
        raise ValueError(str(result.get("error") or "invalid or unreadable PDF file"))
    return str(result.get("text") or "")


def extract_data_bank_text(filename: str, raw: bytes, *, app_settings: AppSettings) -> str:
    suffix = Path(filename).suffix.casefold()
    if suffix not in RAG_SUPPORTED_SUFFIXES:
        raise ValueError("unsupported Data Bank format")
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > 50 * 1024 * 1024 or (
                    info.compress_size and info.file_size / info.compress_size > 1000
                ):
                    raise ValueError("DOCX XML member is too large or highly compressed")
                xml = archive.read(info)
            root = ET.fromstring(xml)
            text = "\n".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("invalid DOCX file") from exc
    elif suffix == ".pdf":
        text = extract_pdf_data_bank_text(raw, app_settings=app_settings)
    else:
        text = raw.decode("utf-8", errors="replace")
        if suffix in {".html", ".htm", ".xml"}:
            text = re.sub(r"<[^>]+>", " ", text)
            text = html.unescape(text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()
    if len(normalized) > app_settings.rag_max_extracted_chars:
        raise ValueError(f"extracted document text exceeds {app_settings.rag_max_extracted_chars} characters")
    return normalized


def split_data_bank_chunks(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + RAG_CHUNK_CHARS)
        if end < len(text):
            boundary = text.rfind("\n", start + RAG_CHUNK_CHARS // 2, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - RAG_CHUNK_OVERLAP)
    return chunks


def rag_embedding_namespace(*, app_settings: AppSettings) -> str:
    revision = app_settings.rag_embedding_revision
    identity = (
        f"{app_settings.rag_embedding_url}|{app_settings.rag_embedding_model}|"
        f"{app_settings.rag_embedding_dimensions}|{revision}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def embedding_norm(vector: list[float]) -> float:
    return math.sqrt(sum(float(value) * float(value) for value in vector))


def _embedding_row(namespace: str, vector: list[float]) -> tuple:
    """Serialize a vector into the (namespace, dimensions, json, signature, norm) column tuple."""
    return (
        namespace,
        len(vector),
        json.dumps(vector, separators=(",", ":")),
        embedding_signature(vector),
        embedding_norm(vector),
    )


def rag_embedding_headers(*, app_settings: AppSettings) -> dict[str, str]:
    parsed = urllib.parse.urlparse(app_settings.rag_embedding_url)
    host = (parsed.hostname or "").casefold()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    validate_provider_endpoint(
        app_settings.rag_embedding_url, "SILLYTAVERN_RAG_ALLOWED_HOSTS", environ=app_settings.environ
    )
    key = app_settings.environ.get("SILLYTAVERN_RAG_EMBEDDING_API_KEY", "")
    if not key and not loopback:
        raise RuntimeError("dedicated SILLYTAVERN_RAG_EMBEDDING_API_KEY is required for external embedding endpoints")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _post_embedding_request(payload: dict, timeout: float, *, app_settings: AppSettings):
    request = urllib.request.Request(  # noqa: S310 -- Request is opened only through DNS-pinned strict_urlopen
        app_settings.rag_embedding_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=rag_embedding_headers(app_settings=app_settings),
        method="POST",
    )
    with strict_urlopen(
        request, timeout=timeout, allowed_env="SILLYTAVERN_RAG_ALLOWED_HOSTS", environ=app_settings.environ
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def embed_rag_text(text: str, *, app_settings: AppSettings) -> list[float] | None:
    try:
        result = _post_embedding_request(
            {"model": app_settings.rag_embedding_model, "input": text[:6000]}, 60, app_settings=app_settings
        )
        vector = (result.get("data") or [{}])[0].get("embedding") or []
        if len(vector) != app_settings.rag_embedding_dimensions:
            logging.warning("Unexpected RAG embedding dimensions: %s", len(vector))
            return None
        return [float(value) for value in vector]
    except Exception:
        logging.warning("RAG embedding unavailable; using lexical search", exc_info=True)
        return None


def embed_rag_batch(texts: list[str], *, app_settings: AppSettings) -> list[list[float] | None]:
    if not texts:
        return []
    try:
        result = _post_embedding_request(
            {"model": app_settings.rag_embedding_model, "input": [text[:6000] for text in texts]},
            120,
            app_settings=app_settings,
        )
        vectors = [None] * len(texts)
        for item in result.get("data") or []:
            index = int(item.get("index", 0))
            vector = item.get("embedding") or []
            if 0 <= index < len(vectors) and len(vector) == app_settings.rag_embedding_dimensions:
                vectors[index] = [float(value) for value in vector]
        return vectors
    except Exception:
        logging.warning("Batch RAG embedding unavailable; falling back to single requests", exc_info=True)
        return [embed_rag_text(text, app_settings=app_settings) for text in texts]


def rag_semantic_candidate_limit(*, app_settings: AppSettings) -> int:
    return app_settings.rag_semantic_candidates


def add_data_bank_document(
    db: sqlite3.Connection, chat_id: str, filename: str, raw: bytes, *, app_settings: AppSettings
) -> tuple[str, int]:
    if len(raw) > RAG_MAX_FILE_BYTES:
        raise ValueError("Data Bank file exceeds 10 MB")
    text = extract_data_bank_text(filename, raw, app_settings=app_settings)
    if not text:
        raise ValueError("Data Bank file contains no readable text")
    document_id = hashlib.sha256(raw).hexdigest()
    existing = db.execute(
        "SELECT chunk_count FROM data_bank_documents WHERE chat_id=? AND document_id=?",
        (chat_id, document_id),
    ).fetchone()
    if existing:
        return "duplicate", int(existing[0])

    chunks = split_data_bank_chunks(text)
    namespace = rag_embedding_namespace(app_settings=app_settings)
    cache_keys = [namespace + ":content:" + hashlib.sha256(content.encode("utf-8")).hexdigest() for content in chunks]
    vector_cache = {}
    pending_cache_rows = []
    for offset in range(0, len(chunks), 32):
        batch_keys = cache_keys[offset : offset + 32]
        placeholders = ",".join("?" for _ in batch_keys)
        for cache_key, vector_json in db.execute(
            f"SELECT cache_key,vector_json FROM rag_embedding_cache WHERE cache_key IN ({placeholders})",  # noqa: S608 -- SQL structure uses fixed columns/placeholders; all values are bound
            batch_keys,
        ).fetchall():
            try:
                vector_cache[cache_key] = json.loads(vector_json)
            except json.JSONDecodeError:
                continue
        missing = [
            (index, chunks[index])
            for index, cache_key in enumerate(cache_keys[offset : offset + 32], start=offset)
            if cache_key not in vector_cache
        ]
        for batch_start in range(0, len(missing), 32):
            selected = missing[batch_start : batch_start + 32]
            # Embedding may involve external model/network work. Do it before
            # opening the short SQLite write transaction below.
            vectors = embed_rag_batch([item[1] for item in selected], app_settings=app_settings)
            for (index, _), vector in zip(selected, vectors, strict=False):
                if vector:
                    cache_key = cache_keys[index]
                    vector_cache[cache_key] = vector
                    pending_cache_rows.append(
                        (
                            cache_key,
                            len(vector),
                            json.dumps(vector, separators=(",", ":")),
                            embedding_norm(vector),
                        )
                    )

    now = time.time()
    with write_transaction(db):
        # Re-check after external embedding work so a concurrent importer that
        # won the race is observed without creating duplicate document rows.
        existing = db.execute(
            "SELECT chunk_count FROM data_bank_documents WHERE chat_id=? AND document_id=?",
            (chat_id, document_id),
        ).fetchone()
        if existing:
            return "duplicate", int(existing[0])

        version_row = db.execute(
            "SELECT COALESCE(MAX(version_number),0) FROM data_bank_documents WHERE chat_id=? AND filename=?",
            (chat_id, filename[:255]),
        ).fetchone()
        version_number = int(version_row[0] or 0) + 1

        if pending_cache_rows:
            db.executemany(
                "INSERT OR REPLACE INTO rag_embedding_cache("
                "cache_key,dimensions,vector_json,vector_norm,created_at"
                ") VALUES(?,?,?,?,?)",
                [(*row, now) for row in pending_cache_rows],
            )
        db.execute(
            "INSERT INTO data_bank_documents("
            "chat_id,document_id,filename,byte_size,chunk_count,version_number,active,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (
                chat_id,
                document_id,
                filename[:255],
                len(raw),
                len(chunks),
                version_number,
                1,
                now,
                now,
            ),
        )
        for index, content in enumerate(chunks):
            cursor = db.execute(
                "INSERT INTO data_bank_chunks(chat_id,document_id,chunk_index,content) VALUES(?,?,?,?)",
                (chat_id, document_id, index, content),
            )
            chunk_id = cursor.lastrowid
            db.execute(
                "INSERT INTO data_bank_fts(content,chat_id,document_id,filename,chunk_id) VALUES(?,?,?,?,?)",
                (content, chat_id, document_id, filename[:255], chunk_id),
            )
            vector = vector_cache.get(cache_keys[index])
            if vector:
                db.execute(
                    (
                        "INSERT INTO data_bank_embeddings(chunk_id,embedding_namespace,dimensions"
                        ",vector_json,vector_signature,vector_norm) VALUES(?,?,?,?,?,?)"
                    ),
                    (chunk_id, *_embedding_row(namespace, vector)),
                )
        db.execute(
            "UPDATE data_bank_documents SET active=0,updated_at=? "
            "WHERE chat_id=? AND filename=? AND document_id<>? AND active=1",
            (now, chat_id, filename[:255], document_id),
        )
    return ("versioned" if version_number > 1 else "added"), len(chunks)


def cached_rag_embedding(db: sqlite3.Connection, text: str, *, app_settings: AppSettings) -> list[float] | None:
    cache_key = (
        rag_embedding_namespace(app_settings=app_settings)
        + ":query:"
        + hashlib.sha256(text[:6000].encode("utf-8")).hexdigest()
    )
    row = db.execute(
        "SELECT vector_json,vector_norm FROM rag_embedding_cache WHERE cache_key=?", (cache_key,)
    ).fetchone()
    if row:
        try:
            vector = json.loads(row[0])
            if len(vector) == app_settings.rag_embedding_dimensions:
                return [float(value) for value in vector]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    vector = embed_rag_text(text, app_settings=app_settings)
    if vector:
        db.execute(
            (
                "INSERT OR REPLACE INTO rag_embedding_cache(cache_key,dimensions,vector_j"
                "son,vector_norm,created_at) VALUES(?,?,?,?,?)"
            ),
            (cache_key, len(vector), json.dumps(vector, separators=(",", ":")), embedding_norm(vector), time.time()),
        )
        db.commit()
    return vector


def retrieve_data_bank(
    db: sqlite3.Connection, chat_id: str, query: str, limit: int = 5, *, app_settings: AppSettings
) -> list[tuple[str, str, str]]:
    terms = re.findall(r"[^\W_]{2,}", query.casefold(), flags=re.UNICODE)[:12]
    if not terms:
        return []
    if not db.execute("SELECT 1 FROM data_bank_documents WHERE chat_id=? AND active=1 LIMIT 1", (chat_id,)).fetchone():
        return []
    candidates = {}
    match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    lexical_rows = db.execute(
        "SELECT CAST(f.chunk_id AS INTEGER),f.filename,c.content,c.document_id,bm25(data_bank_fts) "
        "FROM data_bank_fts f "
        "JOIN data_bank_chunks c ON c.chunk_id=CAST(f.chunk_id AS INTEGER) "
        "JOIN data_bank_documents d ON d.chat_id=c.chat_id AND d.document_id=c.document_id "
        "WHERE f.chat_id=? AND d.active=1 AND data_bank_fts MATCH ? "
        "ORDER BY bm25(data_bank_fts) LIMIT 50",
        (chat_id, match),
    ).fetchall()
    for rank, (chunk_id, filename, content, document_id, _score) in enumerate(lexical_rows):
        lexical_score = 1.0 / (1.0 + rank)
        candidates[int(chunk_id)] = [str(filename), str(content), str(document_id), 0.35 * lexical_score]
    query_vector = cached_rag_embedding(db, query, app_settings=app_settings)
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
        if semantic_ids:
            placeholders = ",".join("?" for _ in semantic_ids)
            vector_rows = db.execute(
                "SELECT e.chunk_id,c.content,d.filename,c.document_id,e.vector_json,e.vector_norm "  # noqa: S608 -- SQL structure uses fixed columns/placeholders; all values are bound
                "FROM data_bank_embeddings e "
                "JOIN data_bank_chunks c ON c.chunk_id=e.chunk_id "
                "JOIN data_bank_documents d ON d.chat_id=c.chat_id AND d.document_id=c.document_id "
                f"WHERE c.chat_id=? AND d.active=1 AND e.embedding_namespace=? AND e.chunk_id IN ({placeholders})",
                (chat_id, namespace, *semantic_ids),
            ).fetchall()
        else:
            vector_rows = []
        for chunk_id, content, filename, document_id, vector_json, vector_norm in vector_rows:
            try:
                if vector_norm:
                    score = cosine_similarity(query_vector, json.loads(vector_json), query_norm, float(vector_norm))
                else:
                    score = cosine_similarity(query_vector, json.loads(vector_json))
                semantic_score = (score + 1.0) / 2.0
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            item = candidates.setdefault(int(chunk_id), [str(filename), str(content), str(document_id), 0.0])
            item[3] += 0.65 * semantic_score
    ranked = sorted(candidates.values(), key=lambda item: item[3], reverse=True)
    return [(item[0], item[1], item[2]) for item in ranked[:limit]]


def rag_mode(db: sqlite3.Connection, chat_id: str) -> str:
    return get_meta(db, f"rag_mode:{chat_id}", "on")


def rag_retrieval_bundle(
    db: sqlite3.Connection, chat_id: str, query: str, limit: int = 5, *, app_settings: AppSettings
) -> dict[str, object]:
    if rag_mode(db, chat_id) != "on":
        return {"results": [], "context": "", "sources": []}
    results = retrieve_data_bank(db, chat_id, query, limit=limit, app_settings=app_settings)
    context_parts = []
    sources = []
    remaining = RAG_MAX_CONTEXT_CHARS
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
    context = "\n\n".join(context_parts)
    return {"results": results, "context": context, "sources": sources}


def rag_context_for_prompt(
    db: sqlite3.Connection,
    chat_id: str,
    query: str,
    bundle: dict[str, object] | None = None,
    *,
    app_settings: AppSettings,
) -> str:
    return str((bundle or rag_retrieval_bundle(db, chat_id, query, app_settings=app_settings)).get("context") or "")


def rag_citation_footer(
    db: sqlite3.Connection,
    chat_id: str,
    query: str,
    bundle: dict[str, object] | None = None,
    *,
    app_settings: AppSettings,
) -> str:
    sources = list((bundle or rag_retrieval_bundle(db, chat_id, query, app_settings=app_settings)).get("sources") or [])
    return "\n\nSources: " + ", ".join(f"[{name}]" for name in sources) if sources else ""


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


def activate_data_bank_version(
    db: sqlite3.Connection,
    chat_id: str,
    filename: str,
    version_number: int,
) -> bool:
    target = db.execute(
        "SELECT document_id FROM data_bank_documents WHERE chat_id=? AND filename=? AND version_number=?",
        (chat_id, filename, int(version_number)),
    ).fetchone()
    if not target:
        return False
    now = time.time()
    db.execute(
        "UPDATE data_bank_documents SET active=0,updated_at=? WHERE chat_id=? AND filename=?",
        (now, chat_id, filename),
    )
    db.execute(
        "UPDATE data_bank_documents SET active=1,updated_at=? WHERE chat_id=? AND document_id=?",
        (now, chat_id, str(target[0])),
    )
    db.commit()
    return True


def delete_data_bank_documents(db: sqlite3.Connection, chat_id: str, filename: str) -> int:
    documents = db.execute(
        "SELECT document_id FROM data_bank_documents WHERE chat_id=? AND filename=?", (chat_id, filename)
    ).fetchall()
    for (document_id,) in documents:
        db.execute("DELETE FROM data_bank_fts WHERE chat_id=? AND document_id=?", (chat_id, document_id))
        db.execute(
            (
                "DELETE FROM data_bank_embeddings WHERE chunk_id IN (SELECT chunk_id FROM "
                "data_bank_chunks WHERE chat_id=? AND document_id=?)"
            ),
            (chat_id, document_id),
        )
        db.execute("DELETE FROM data_bank_chunks WHERE chat_id=? AND document_id=?", (chat_id, document_id))
        db.execute("DELETE FROM data_bank_documents WHERE chat_id=? AND document_id=?", (chat_id, document_id))
    db.commit()
    optimize_database(db)
    return len(documents)


def rag_embedding_coverage(db: sqlite3.Connection, chat_id: str, *, app_settings: AppSettings) -> tuple[int, int]:
    total = int(
        db.execute(
            "SELECT COALESCE(SUM(chunk_count),0) FROM data_bank_documents WHERE chat_id=? AND active=1",
            (chat_id,),
        ).fetchone()[0]
    )
    indexed = int(
        db.execute(
            "SELECT COUNT(*) FROM data_bank_embeddings e "
            "JOIN data_bank_chunks c ON c.chunk_id=e.chunk_id "
            "JOIN data_bank_documents d ON d.chat_id=c.chat_id AND d.document_id=c.document_id "
            "WHERE c.chat_id=? AND d.active=1 AND e.embedding_namespace=?",
            (chat_id, rag_embedding_namespace(app_settings=app_settings)),
        ).fetchone()[0]
    )
    return total, indexed


def reindex_data_bank_documents(
    db: sqlite3.Connection, chat_id: str, filename: str | None = None, *, app_settings: AppSettings
) -> tuple[int, int]:
    namespace = rag_embedding_namespace(app_settings=app_settings)
    params = [chat_id]
    query = "SELECT document_id,filename FROM data_bank_documents WHERE chat_id=? AND active=1"
    if filename:
        query += " AND filename=?"
        params.append(filename)
    documents = db.execute(query, params).fetchall()
    total = indexed = 0
    for document_id, _name in documents:
        rows = db.execute(
            "SELECT chunk_id,content FROM data_bank_chunks WHERE chat_id=? AND document_id=? ORDER BY chunk_index",
            (chat_id, document_id),
        ).fetchall()
        missing = []
        for chunk_id, content in rows:
            total += 1
            exists = db.execute(
                "SELECT 1 FROM data_bank_embeddings WHERE chunk_id=? AND embedding_namespace=?", (chunk_id, namespace)
            ).fetchone()
            if not exists:
                missing.append((int(chunk_id), str(content)))
        for offset in range(0, len(missing), 32):
            batch = missing[offset : offset + 32]
            # Keep the potentially slow embedding call outside the write lock.
            vectors = embed_rag_batch([content for _, content in batch], app_settings=app_settings)
            rows_to_store = [
                (chunk_id, *_embedding_row(namespace, vector))
                for (chunk_id, _content), vector in zip(batch, vectors, strict=False)
                if vector
            ]
            if rows_to_store:
                with write_transaction(db):
                    db.executemany(
                        "INSERT OR REPLACE INTO data_bank_embeddings("
                        "chunk_id,embedding_namespace,dimensions,vector_json,vector_signature,vector_norm"
                        ") VALUES(?,?,?,?,?,?)",
                        rows_to_store,
                    )
                indexed += len(rows_to_store)
    return total, indexed

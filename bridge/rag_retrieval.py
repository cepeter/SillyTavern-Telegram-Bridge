"""Pure retrieval helpers for Data Bank semantic search.

This module is imported normally (rather than executed into bridge.runtime) so
retrieval candidate selection can evolve independently from the compatibility
runtime namespace.
"""
from __future__ import annotations

import math
import sqlite3

try:
    import numpy as _numpy
except ImportError:
    _numpy = None


DEFAULT_SEMANTIC_CANDIDATE_LIMIT = 384
MAX_SEMANTIC_CANDIDATE_LIMIT = 2048
DEFAULT_SEMANTIC_SAMPLE_WINDOWS = 12


def cosine_similarity(left: list[float], right: list[float], left_norm: float | None = None, right_norm: float | None = None) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    if _numpy is not None:
        left_array = _numpy.asarray(left, dtype=float)
        right_array = _numpy.asarray(right, dtype=float)
        numerator = float(_numpy.dot(left_array, right_array))
        denominator = float(left_norm or _numpy.linalg.norm(left_array)) * float(right_norm or _numpy.linalg.norm(right_array))
        return numerator / denominator if denominator else 0.0
    denominator = float(left_norm or math.sqrt(sum(value * value for value in left))) * float(right_norm or math.sqrt(sum(value * value for value in right)))
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0


def semantic_candidate_chunk_ids(
    db: sqlite3.Connection,
    chat_id: str,
    embedding_namespace: str,
    lexical_chunk_ids: list[int] | tuple[int, ...],
    *,
    candidate_limit: int = DEFAULT_SEMANTIC_CANDIDATE_LIMIT,
    neighbor_radius: int = 2,
    sample_windows: int = DEFAULT_SEMANTIC_SAMPLE_WINDOWS,
) -> tuple[int, ...]:
    """Return a bounded semantic shortlist without decoding the whole corpus.

    Small corpora remain exact. Larger corpora prioritize FTS-hit neighborhoods
    and fill the remaining budget with deterministic samples spread across the
    chat's embedding ID range.
    """
    candidate_limit = max(1, min(int(candidate_limit), MAX_SEMANTIC_CANDIDATE_LIMIT))
    neighbor_radius = max(0, min(int(neighbor_radius), 8))
    sample_windows = max(1, min(int(sample_windows), 64))

    probe = db.execute(
        "SELECT e.chunk_id "
        "FROM data_bank_embeddings e "
        "JOIN data_bank_chunks c ON c.chunk_id=e.chunk_id "
        "WHERE c.chat_id=? AND e.embedding_namespace=? "
        "ORDER BY e.chunk_id LIMIT ?",
        (str(chat_id), str(embedding_namespace), candidate_limit + 1),
    ).fetchall()
    if len(probe) <= candidate_limit:
        return tuple(int(row[0]) for row in probe)

    selected: list[int] = []
    seen: set[int] = set()

    def add(chunk_id: int) -> None:
        value = int(chunk_id)
        if value not in seen and len(selected) < candidate_limit:
            seen.add(value)
            selected.append(value)

    lexical = tuple(dict.fromkeys(int(value) for value in lexical_chunk_ids if value is not None))
    if lexical:
        placeholders = ",".join("?" for _ in lexical)
        rows = db.execute(
            "SELECT DISTINCT n.chunk_id, ABS(n.chunk_index-hit.chunk_index) AS distance "
            "FROM data_bank_chunks hit "
            "JOIN data_bank_chunks n "
            "  ON n.chat_id=hit.chat_id AND n.document_id=hit.document_id "
            "JOIN data_bank_embeddings e "
            "  ON e.chunk_id=n.chunk_id AND e.embedding_namespace=? "
            f"WHERE hit.chat_id=? AND hit.chunk_id IN ({placeholders}) "
            "  AND n.chunk_index BETWEEN hit.chunk_index-? AND hit.chunk_index+? "
            "ORDER BY distance, n.chunk_id LIMIT ?",
            (
                str(embedding_namespace),
                str(chat_id),
                *lexical,
                neighbor_radius,
                neighbor_radius,
                candidate_limit,
            ),
        ).fetchall()
        for chunk_id, _distance in rows:
            add(int(chunk_id))

    if len(selected) >= candidate_limit:
        return tuple(selected)

    minimum = int(probe[0][0])
    maximum_row = db.execute(
        "SELECT e.chunk_id "
        "FROM data_bank_embeddings e "
        "JOIN data_bank_chunks c ON c.chunk_id=e.chunk_id "
        "WHERE c.chat_id=? AND e.embedding_namespace=? "
        "ORDER BY e.chunk_id DESC LIMIT 1",
        (str(chat_id), str(embedding_namespace)),
    ).fetchone()
    maximum = int(maximum_row[0]) if maximum_row else minimum

    remaining = candidate_limit - len(selected)
    windows = min(sample_windows, remaining)
    per_window = max(1, math.ceil(remaining / windows))
    for index in range(windows):
        if len(selected) >= candidate_limit:
            break
        if windows == 1 or maximum <= minimum:
            anchor = minimum
        else:
            anchor = minimum + ((maximum - minimum) * index // (windows - 1))
        rows = db.execute(
            "SELECT e.chunk_id "
            "FROM data_bank_embeddings e "
            "JOIN data_bank_chunks c ON c.chunk_id=e.chunk_id "
            "WHERE c.chat_id=? AND e.embedding_namespace=? AND e.chunk_id>=? "
            "ORDER BY e.chunk_id LIMIT ?",
            (str(chat_id), str(embedding_namespace), anchor, per_window),
        ).fetchall()
        for (chunk_id,) in rows:
            add(int(chunk_id))

    if len(selected) < candidate_limit:
        rows = db.execute(
            "SELECT e.chunk_id "
            "FROM data_bank_embeddings e "
            "JOIN data_bank_chunks c ON c.chunk_id=e.chunk_id "
            "WHERE c.chat_id=? AND e.embedding_namespace=? "
            "ORDER BY e.chunk_id DESC LIMIT ?",
            (
                str(chat_id),
                str(embedding_namespace),
                candidate_limit - len(selected),
            ),
        ).fetchall()
        for (chunk_id,) in rows:
            add(int(chunk_id))

    return tuple(selected)

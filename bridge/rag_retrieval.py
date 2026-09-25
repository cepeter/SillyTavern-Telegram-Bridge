"""Bounded semantic candidate selection and vector math for Data Bank search.

Large corpora use a compact angular signature to shortlist candidates globally.
Only the shortlisted JSON vectors are decoded for exact cosine scoring.
"""

from __future__ import annotations

import heapq
import math
import sqlite3
from typing import Any

from bridge import rag_repository as repository

_numpy: Any
try:
    import numpy as _numpy
except ImportError:
    _numpy = None


DEFAULT_SEMANTIC_CANDIDATE_LIMIT = 384
MAX_SEMANTIC_CANDIDATE_LIMIT = 2048
EMBEDDING_SIGNATURE_BITS = 63
_MASK64 = (1 << 64) - 1


def cosine_similarity(
    left: list[float], right: list[float], left_norm: float | None = None, right_norm: float | None = None
) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    if _numpy is not None:
        left_array = _numpy.asarray(left, dtype=float)
        right_array = _numpy.asarray(right, dtype=float)
        numerator = float(_numpy.dot(left_array, right_array))
        denominator = float(left_norm or _numpy.linalg.norm(left_array)) * float(
            right_norm or _numpy.linalg.norm(right_array)
        )
        return numerator / denominator if denominator else 0.0
    denominator = float(left_norm or math.sqrt(sum(value * value for value in left))) * float(
        right_norm or math.sqrt(sum(value * value for value in right))
    )
    return sum(a * b for a, b in zip(left, right, strict=False)) / denominator if denominator else 0.0


def _mix_dimension(index: int) -> int:
    value = ((int(index) + 1) * 0x9E3779B97F4A7C15) & _MASK64
    value ^= value >> 30
    value = (value * 0xBF58476D1CE4E5B9) & _MASK64
    value ^= value >> 27
    value = (value * 0x94D049BB133111EB) & _MASK64
    value ^= value >> 31
    return value


def embedding_signature(vector: list[float] | tuple[float, ...]) -> int:
    """Build a compact deterministic angular sketch in one pass over a vector."""
    if not vector:
        return 0
    accumulators = [0.0] * EMBEDDING_SIGNATURE_BITS
    for index, raw_value in enumerate(vector):
        value = float(raw_value)
        mixed = _mix_dimension(index)
        bucket = mixed % EMBEDDING_SIGNATURE_BITS
        accumulators[bucket] += value if (mixed >> 63) else -value
    signature = 0
    for bit, total in enumerate(accumulators):
        if total > 0:
            signature |= 1 << bit
    return signature


def signature_distance(left: int, right: int) -> int:
    return (int(left) ^ int(right)).bit_count()


def semantic_candidate_chunk_ids(
    db: sqlite3.Connection,
    chat_id: str,
    embedding_namespace: str,
    lexical_chunk_ids: list[int] | tuple[int, ...],
    *,
    query_signature: int | None = None,
    candidate_limit: int = DEFAULT_SEMANTIC_CANDIDATE_LIMIT,
    neighbor_radius: int = 2,
) -> tuple[int, ...]:
    """Return a bounded semantic shortlist without decoding the whole corpus.

    Small corpora remain exact. Larger corpora prioritize FTS-hit neighborhoods,
    then globally rank compact embedding signatures by Hamming distance.
    """
    candidate_limit = max(1, min(int(candidate_limit), MAX_SEMANTIC_CANDIDATE_LIMIT))
    neighbor_radius = max(0, min(int(neighbor_radius), 8))

    probe = repository.candidate_probe(db, chat_id, embedding_namespace, candidate_limit + 1)
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
        rows = repository.candidate_neighbors(
            db, chat_id, embedding_namespace, lexical, neighbor_radius, candidate_limit
        )
        for chunk_id, _distance in rows:
            add(int(chunk_id))

    if len(selected) >= candidate_limit:
        return tuple(selected)

    if query_signature is not None:
        remaining = candidate_limit - len(selected)
        nearest: list[tuple[int, int, int]] = []
        signatures = repository.signature_rows(db, chat_id, embedding_namespace)
        for chunk_id, vector_signature in signatures:
            chunk_id = int(chunk_id)
            if chunk_id in seen:
                continue
            distance = signature_distance(int(vector_signature), int(query_signature))
            entry = (-distance, -chunk_id, chunk_id)
            if len(nearest) < remaining:
                heapq.heappush(nearest, entry)
            elif entry > nearest[0]:
                heapq.heapreplace(nearest, entry)
        for _negative_distance, _negative_chunk_id, chunk_id in sorted(nearest, key=lambda item: (-item[0], item[2])):
            add(chunk_id)

    return tuple(selected)

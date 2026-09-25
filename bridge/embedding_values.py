"""Canonical embedding values owner."""

from __future__ import annotations

import hashlib
import json
import math

from bridge.rag_retrieval import embedding_signature
from bridge.settings import AppSettings


def rag_embedding_namespace(*, app_settings: AppSettings) -> str:
    revision = app_settings.rag_embedding_revision
    identity = (
        f"{app_settings.rag_embedding_url}|{app_settings.rag_embedding_model}|"
        f"{app_settings.rag_embedding_dimensions}|{revision}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def embedding_norm(vector: list[float]) -> float:
    return math.sqrt(sum(float(value) * float(value) for value in vector))


def _embedding_row(namespace: str, vector: list[float]) -> tuple[str, int, str, int, float]:
    """Serialize a vector into the (namespace, dimensions, json, signature, norm) column tuple."""
    return (
        namespace,
        len(vector),
        json.dumps(vector, separators=(",", ":")),
        embedding_signature(vector),
        embedding_norm(vector),
    )

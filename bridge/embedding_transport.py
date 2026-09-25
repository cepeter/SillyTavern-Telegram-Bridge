"""Canonical embedding transport owner."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

from bridge.network_security import strict_urlopen, validate_provider_endpoint
from bridge.settings import AppSettings


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


def _post_embedding_request(payload: dict, timeout: float, *, app_settings: AppSettings) -> dict[str, Any]:
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
        vectors: list[list[float] | None] = [None] * len(texts)
        for item in result.get("data") or []:
            index = int(item.get("index", 0))
            vector = item.get("embedding") or []
            if 0 <= index < len(vectors) and len(vector) == app_settings.rag_embedding_dimensions:
                vectors[index] = [float(value) for value in vector]
        return vectors
    except Exception:
        logging.warning("Batch RAG embedding unavailable; falling back to single requests", exc_info=True)
        return [embed_rag_text(text, app_settings=app_settings) for text in texts]

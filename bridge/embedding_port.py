"""Application-owned embedding port; no provider credentials or HTTP transport."""

from __future__ import annotations

from dataclasses import dataclass

from bridge.rag_contracts import EmbedBatch, EmbedText, Vector


@dataclass(frozen=True)
class EmbeddingPort:
    embed_backend: EmbedText
    batch_backend: EmbedBatch

    def embed(self, text: str) -> Vector | None:
        return self.embed_backend(text)

    def embed_batch(self, texts: list[str]) -> list[Vector | None]:
        return self.batch_backend(texts)

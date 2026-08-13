"""Hybrid retrieval: lexical AST search plus an optional embedding channel."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Protocol
from urllib.request import Request, urlopen

from .core import CodeChunk, CodeIndex


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class RetrievalResult:
    score: float
    chunk: CodeChunk
    channels: tuple[str, ...]


class OpenAICompatibleEmbeddingProvider:
    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: int = 20):
        self.base_url, self.api_key, self.model, self.timeout_s = base_url.rstrip("/"), api_key, model, timeout_s

    def embed(self, texts: list[str]) -> list[list[float]]:
        request = Request(f"{self.base_url}/embeddings", data=__import__("json").dumps({"model": self.model, "input": texts}).encode("utf-8"),
                          headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=self.timeout_s) as response:
            payload = __import__("json").loads(response.read().decode("utf-8"))
        return [item["embedding"] for item in sorted(payload["data"], key=lambda item: item["index"])]


def embedding_provider_from_env() -> EmbeddingProvider | None:
    base_url = os.getenv("REPOPILOT_EMBEDDING_BASE_URL")
    api_key = os.getenv("REPOPILOT_EMBEDDING_API_KEY")
    model = os.getenv("REPOPILOT_EMBEDDING_MODEL")
    return OpenAICompatibleEmbeddingProvider(base_url, api_key, model) if base_url and api_key and model else None


def _cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0


class HybridRetriever:
    """Reciprocal-rank fusion over local lexical and optional semantic rankings."""

    def __init__(self, index: CodeIndex, embedding_provider: EmbeddingProvider | None = None):
        self.index, self.embedding_provider = index, embedding_provider

    def search(self, query: str, top_k: int = 5) -> list[RetrievalResult]:
        if not self.index.chunks:
            self.index.build()
        lexical = self.index.search(query, top_k=max(len(self.index.chunks), top_k))
        rankings: list[list[CodeChunk]] = [[chunk for _, chunk in lexical]]
        semantic_chunks: list[CodeChunk] = []
        if self.embedding_provider:
            try:
                vectors = self.embedding_provider.embed([query] + [chunk.content for chunk in self.index.chunks])
                query_vector, chunk_vectors = vectors[0], vectors[1:]
                semantic_chunks = [chunk for _, chunk in sorted(zip((_cosine(query_vector, vector) for vector in chunk_vectors), self.index.chunks),
                                                                key=lambda item: item[0], reverse=True)]
                rankings.append(semantic_chunks)
            except Exception:
                semantic_chunks = []
        fused: dict[CodeChunk, float] = {}
        channels: dict[CodeChunk, list[str]] = {}
        for channel, ranking in zip(("lexical", "semantic"), rankings):
            for rank, chunk in enumerate(ranking, start=1):
                fused[chunk] = fused.get(chunk, 0) + 1 / (60 + rank)
                channels.setdefault(chunk, []).append(channel)
        return [RetrievalResult(score, chunk, tuple(channels[chunk])) for chunk, score in
                sorted(fused.items(), key=lambda item: (-item[1], item[0].path, item[0].start_line))[:top_k]]

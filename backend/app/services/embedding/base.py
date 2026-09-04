"""Embedding provider protocol and shared validation."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Protocol


class EmbeddingUnavailable(RuntimeError):
    """Raised when dense retrieval cannot be used for this request/job."""


@dataclass(slots=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    model: str
    dimension: int
    usage: dict[str, int]


class EmbeddingProvider(Protocol):
    model: str
    dimension: int

    def embed_documents(
        self,
        texts: list[str],
        *,
        instruction: str | None = None,
    ) -> EmbeddingBatch:
        ...

    def embed_query(
        self,
        text: str,
        *,
        instruction: str | None = None,
    ) -> EmbeddingBatch:
        ...

    # Kept during the migration to the split interface for external adapters.
    def embed_texts(self, texts: list[str]) -> EmbeddingBatch:
        ...


def embed_documents(
    provider: EmbeddingProvider | Any,
    texts: list[str],
    *,
    instruction: str | None = None,
) -> EmbeddingBatch:
    """Call the document embedding interface with a legacy-adapter fallback."""

    method = getattr(provider, "embed_documents", None)
    if callable(method):
        return method(texts, instruction=instruction)
    legacy = getattr(provider, "embed_texts", None)
    if not callable(legacy):
        raise EmbeddingUnavailable("embedding provider has no document method")
    return legacy(texts)


def embed_query(
    provider: EmbeddingProvider | Any,
    text: str,
    *,
    instruction: str | None = None,
) -> EmbeddingBatch:
    """Call the query embedding interface with a legacy-adapter fallback."""

    method = getattr(provider, "embed_query", None)
    if callable(method):
        return method(text, instruction=instruction)
    legacy = getattr(provider, "embed_texts", None)
    if not callable(legacy):
        raise EmbeddingUnavailable("embedding provider has no query method")
    return legacy([text])


def embedding_document_config_hash(provider: EmbeddingProvider | Any) -> str:
    """Return the stable identity of the document-vector projection."""

    payload = {
        "provider": str(getattr(provider, "provider", "embedding")),
        "model": str(getattr(provider, "model", "")),
        "dimension": int(getattr(provider, "dimension", 0) or 0),
        "document_instruction": str(
            getattr(provider, "document_instruction", "") or ""
        ).strip(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_vectors(
    vectors: list[list[float]],
    *,
    expected_count: int,
    expected_dimension: int,
) -> list[list[float]]:
    if len(vectors) != int(expected_count):
        raise EmbeddingUnavailable(
            f"embedding count mismatch: expected {expected_count}, got {len(vectors)}"
        )
    normalized: list[list[float]] = []
    for index, vector in enumerate(vectors):
        if len(vector) != int(expected_dimension):
            raise EmbeddingUnavailable(
                f"embedding dimension mismatch at {index}: expected {expected_dimension}, got {len(vector)}"
            )
        values = [float(value) for value in vector]
        if not values or not any(abs(value) > 0 for value in values):
            raise EmbeddingUnavailable(f"embedding vector {index} is all zero")
        if not all(math.isfinite(value) for value in values):
            raise EmbeddingUnavailable(f"embedding vector {index} contains non-finite value")
        normalized.append(values)
    return normalized

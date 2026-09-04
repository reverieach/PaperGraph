"""OpenAI-compatible DashScope text-embedding-v4 provider."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from .base import EmbeddingBatch, EmbeddingUnavailable, validate_vectors

logger = logging.getLogger(__name__)


class DashScopeEmbeddingProvider:
    provider = "dashscope"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        dimension: int | None = None,
        timeout: float = 60.0,
        max_batch_size: int = 10,
        max_attempts: int = 2,
        document_instruction: str | None = None,
        query_instruction: str | None = None,
    ) -> None:
        self.api_key = (api_key or os.getenv("EMBED_API_KEY") or "").strip()
        self.base_url = (base_url or os.getenv("EMBED_BASE_URL") or "").strip()
        self.model = (model or os.getenv("EMBED_MODEL_NAME") or "text-embedding-v4").strip()
        try:
            self.dimension = int(dimension or os.getenv("EMBED_DIMENSION") or 1024)
        except (TypeError, ValueError):
            self.dimension = 1024
        self.timeout = max(2.0, float(timeout))
        self.max_batch_size = max(1, min(10, int(max_batch_size)))
        self.max_attempts = max(1, min(4, int(max_attempts)))
        # text-embedding-v4's OpenAI-compatible endpoint accepts plain input,
        # not a provider-specific instruction field.  Prefixes are therefore
        # opt-in and explicit.  Leaving them empty preserves the previously
        # verified embedding space until a Golden dev split validates a change.
        self.document_instruction = (
            document_instruction
            if document_instruction is not None
            else os.getenv("RAG_EMBED_DOCUMENT_INSTRUCTION", "")
        ).strip()
        self.query_instruction = (
            query_instruction
            if query_instruction is not None
            else os.getenv("RAG_EMBED_QUERY_INSTRUCTION", "")
        ).strip()
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if not self.api_key:
            raise EmbeddingUnavailable("EMBED_API_KEY is not configured")
        if not self.base_url:
            raise EmbeddingUnavailable("EMBED_BASE_URL is not configured")
        if self._client is None:
            try:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    timeout=self.timeout,
                )
            except Exception as exc:
                raise EmbeddingUnavailable(f"embedding client initialization failed: {exc}") from exc
        return self._client

    @staticmethod
    def _apply_instruction(values: list[str], instruction: str | None) -> list[str]:
        prefix = str(instruction or "").strip()
        if not prefix:
            return values
        return [f"{prefix}\n\n{value}" for value in values]

    def embed_documents(
        self,
        texts: list[str],
        *,
        instruction: str | None = None,
    ) -> EmbeddingBatch:
        return self._embed(
            texts,
            instruction=(
                self.document_instruction if instruction is None else instruction
            ),
        )

    def embed_query(
        self,
        text: str,
        *,
        instruction: str | None = None,
    ) -> EmbeddingBatch:
        return self._embed(
            [text],
            instruction=(self.query_instruction if instruction is None else instruction),
        )

    def embed_texts(self, texts: list[str]) -> EmbeddingBatch:
        """Backward-compatible document path for old callers/adapters."""

        return self.embed_documents(texts)

    def _embed(
        self,
        texts: list[str],
        *,
        instruction: str | None = None,
    ) -> EmbeddingBatch:
        values = [str(text or "").strip() for text in texts]
        if not values:
            return EmbeddingBatch([], self.model, self.dimension, {})
        if any(not value for value in values):
            raise EmbeddingUnavailable("embedding input contains empty text")
        values = self._apply_instruction(values, instruction)
        vectors: list[list[float]] = []
        total_usage: dict[str, int] = {}
        for start in range(0, len(values), self.max_batch_size):
            batch = values[start : start + self.max_batch_size]
            response = self._create_with_retry(batch)
            data = getattr(response, "data", None)
            if data is None and isinstance(response, dict):
                data = response.get("data")
            if not isinstance(data, list):
                raise EmbeddingUnavailable("embedding response has no data list")
            indexed: list[tuple[int, list[float]]] = []
            for fallback_index, item in enumerate(data):
                if isinstance(item, dict):
                    index = item.get("index", fallback_index)
                    vector = item.get("embedding")
                else:
                    index = getattr(item, "index", fallback_index)
                    vector = getattr(item, "embedding", None)
                try:
                    index = int(index)
                except (TypeError, ValueError) as exc:
                    raise EmbeddingUnavailable("embedding item index is invalid") from exc
                if not isinstance(vector, list):
                    raise EmbeddingUnavailable("embedding item has no vector")
                indexed.append((index, [float(value) for value in vector]))
            indexed.sort(key=lambda item: item[0])
            batch_vectors = validate_vectors(
                [vector for _, vector in indexed],
                expected_count=len(batch),
                expected_dimension=self.dimension,
            )
            vectors.extend(batch_vectors)
            usage = getattr(response, "usage", None)
            if usage is None and isinstance(response, dict):
                usage = response.get("usage")
            if usage:
                if isinstance(usage, dict):
                    raw_usage = usage
                else:
                    raw_usage = {
                        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                    }
                for key, value in raw_usage.items():
                    try:
                        total_usage[key] = total_usage.get(key, 0) + int(value or 0)
                    except (TypeError, ValueError):
                        continue
        return EmbeddingBatch(vectors, self.model, self.dimension, total_usage)

    def _create_with_retry(self, batch: list[str]) -> Any:
        client = self._get_client()
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            started = time.perf_counter()
            try:
                response = client.embeddings.create(
                    model=self.model,
                    input=batch,
                    dimensions=self.dimension,
                )
                logger.info(
                    "embedding_batch_succeeded",
                    extra={"model": self.model, "batch_size": len(batch), "latency_ms": int((time.perf_counter() - started) * 1000)},
                )
                return response
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    time.sleep(0.5 * (2**attempt))
        raise EmbeddingUnavailable(f"embedding request failed: {last_error}") from last_error

"""DashScope compatible qwen reranker adapter."""

from __future__ import annotations

import os
import time

from .base import RerankResult, RerankerUnavailable, validate_rerank_results


class DashScopeReranker:
    provider = "dashscope"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
        max_documents: int = 50,
        max_attempts: int = 2,
    ) -> None:
        self.api_key = (api_key or os.getenv("RERANK_API_KEY") or "").strip()
        self.endpoint = (endpoint or os.getenv("RERANK_ENDPOINT") or "").strip()
        self.model = (model or os.getenv("RERANK_MODEL_NAME") or "qwen3-rerank").strip()
        self.timeout = max(2.0, float(timeout))
        self.max_documents = max(1, min(100, int(max_documents)))
        self.max_attempts = max(1, min(4, int(max_attempts)))

    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int | None = None,
        instruction: str | None = None,
    ) -> list[RerankResult]:
        values = [str(document or "").strip() for document in documents]
        if not values:
            return []
        if not str(query or "").strip():
            raise RerankerUnavailable("rerank query is empty")
        if len(values) > self.max_documents:
            values = values[: self.max_documents]
        if not self.api_key:
            raise RerankerUnavailable("RERANK_API_KEY is not configured")
        if not self.endpoint:
            raise RerankerUnavailable("RERANK_ENDPOINT is not configured")
        query_value = str(query).strip()
        task_instruction = str(instruction or "").strip()
        if task_instruction:
            # The compatible endpoint has no independently verified
            # ``instruction`` field.  Keep a static task policy in the query
            # field and clearly delimit the user question instead of sending
            # an undocumented payload key that may be silently ignored.
            query_value = f"任务要求：{task_instruction}\n用户问题：{query_value}"
        payload = {
            "model": self.model,
            # qwen3-rerank's compatible endpoint uses a flat request body;
            # the nested ``input`` shape belongs to the native text-rerank
            # endpoint and returns a misleading missing-field 400 here.
            "query": query_value,
            "documents": values,
            "top_n": max(1, min(int(top_n or len(values)), len(values))),
        }
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                import httpx

                response = httpx.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                body = response.json()
                raw_results = body.get("results") if isinstance(body, dict) else None
                if not isinstance(raw_results, list):
                    raise RerankerUnavailable("rerank response has no results list")
                parsed: list[RerankResult] = []
                for item in raw_results:
                    if not isinstance(item, dict):
                        continue
                    raw_index = item.get("index")
                    raw_score = item.get("relevance_score", item.get("score"))
                    if raw_index is None or raw_score is None:
                        raise RerankerUnavailable(
                            "rerank result is missing index or score"
                        )
                    parsed.append(
                        RerankResult(
                            index=int(raw_index),
                            score=float(raw_score),
                        )
                    )
                return validate_rerank_results(parsed, document_count=len(values))
            except RerankerUnavailable:
                raise
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    time.sleep(0.5 * (2**attempt))
        raise RerankerUnavailable(f"rerank request failed: {last_error}") from last_error

"""Reranker protocol and response validation."""

from __future__ import annotations

import math
import inspect
from dataclasses import dataclass
from typing import Any, Protocol


class RerankerUnavailable(RuntimeError):
    """Raised when the optional precision stage cannot be used."""


@dataclass(slots=True)
class RerankResult:
    index: int
    score: float


class Reranker(Protocol):
    model: str

    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int | None = None,
        instruction: str | None = None,
    ) -> list[RerankResult]:
        ...


def rerank_documents(
    reranker: Reranker | Any,
    query: str,
    documents: list[str],
    *,
    top_n: int | None = None,
    instruction: str | None = None,
) -> list[RerankResult]:
    """Call a task-aware reranker while keeping old local adapters usable."""

    method = getattr(reranker, "rerank", None)
    if not callable(method):
        raise RerankerUnavailable("reranker has no rerank method")
    try:
        parameters = inspect.signature(method).parameters.values()
        supports_instruction = any(
            parameter.name == "instruction"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        # Built-in/remote wrappers may not expose a signature.  Prefer the
        # modern interface in that case; a provider error remains observable.
        supports_instruction = True
    if supports_instruction:
        return method(
            query,
            documents,
            top_n=top_n,
            instruction=instruction,
        )
    return method(query, documents, top_n=top_n)


def validate_rerank_results(
    results: list[RerankResult], *, document_count: int
) -> list[RerankResult]:
    seen: set[int] = set()
    out: list[RerankResult] = []
    for item in results:
        index = int(item.index)
        score = float(item.score)
        if index < 0 or index >= int(document_count) or index in seen:
            raise RerankerUnavailable(f"invalid rerank result index: {index}")
        if not math.isfinite(score):
            raise RerankerUnavailable("rerank score is not finite")
        seen.add(index)
        out.append(RerankResult(index=index, score=score))
    return out

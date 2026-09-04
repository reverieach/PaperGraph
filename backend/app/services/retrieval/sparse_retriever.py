"""Independent unicode61 and trigram sparse recall.

BM25 scores from separate FTS tokenizers are not comparable.  This adapter
therefore returns two ranked lists and lets ``HybridChunkRetriever`` combine
their ranks with weighted reciprocal-rank fusion instead of adding raw scores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...repositories.document_repository import DocumentRepository
from .academic_query_planner import (
    QueryPlan,
    build_trigram_query,
    build_unicode61_query,
)


@dataclass(slots=True)
class SparseRetrievalResult:
    unicode_rows: list[dict[str, Any]] = field(default_factory=list)
    trigram_rows: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    degradation_reasons: list[str] = field(default_factory=list)


class DualSparseRetriever:
    """Execute the two sparse projections without conflating their scores."""

    def __init__(self, repository: DocumentRepository) -> None:
        self.repository = repository

    def retrieve(
        self,
        *,
        user_id: int,
        paper_ids: list[int],
        plan: QueryPlan,
        limit: int,
    ) -> SparseRetrievalResult:
        result = SparseRetrievalResult()
        scope = list(dict.fromkeys(int(value) for value in paper_ids))[:400]
        if not scope:
            return result
        bounded_limit = max(1, min(int(limit), 100))

        unicode_query = build_unicode61_query(plan.unicode_terms)
        if unicode_query:
            if not self.repository.has_fts():
                result.degraded = True
                result.degradation_reasons.append("unicode61_fts_unavailable")
            else:
                try:
                    result.unicode_rows = self.repository.search_fts(
                        user_id=int(user_id),
                        paper_ids=scope,
                        match_query=unicode_query,
                        limit=bounded_limit,
                    )
                except Exception as exc:
                    result.degraded = True
                    result.degradation_reasons.append(
                        f"unicode61_search_unavailable:{type(exc).__name__}"
                    )

        # Trigram is deliberately used only for questions containing CJK.  On
        # English-only questions unicode61 has better term semantics, while a
        # CJK/mixed question benefits from substring recall over original text.
        trigram_query = build_trigram_query(plan.trigram_terms)
        if plan.has_cjk and trigram_query:
            if not self.repository.has_trigram_fts():
                result.degraded = True
                result.degradation_reasons.append("trigram_fts_unavailable")
            else:
                try:
                    result.trigram_rows = self.repository.search_trigram_fts(
                        user_id=int(user_id),
                        paper_ids=scope,
                        match_query=trigram_query,
                        limit=bounded_limit,
                    )
                except Exception as exc:
                    result.degraded = True
                    result.degradation_reasons.append(
                        f"trigram_search_unavailable:{type(exc).__name__}"
                    )
        return result


__all__ = ["DualSparseRetriever", "SparseRetrievalResult"]

"""Scope-safe, relevance-gated retrieval for user-confirmed Memory.

The project intentionally separates Memory *writing* (draft -> user review ->
commit) from Memory *reading*.  This module only reads canonical SQLite Memory
rows and never asks an LLM to create, promote, or mutate a memory item.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ...repositories.memory_repository import MemoryRepository
from ..retrieval.academic_query_planner import (
    AcademicQueryPlanner,
    QueryPlan,
    build_trigram_query,
    build_unicode61_query,
)


_CJK_ONLY_RE = re.compile(r"^[\u3400-\u9fff]+$")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryHit:
    memory_id: str
    user_id: int
    scope_type: str
    scope_id: str
    kind: str
    content: str
    score: float
    lexical_score: float
    importance: float
    inclusion_reason: tuple[str, ...]
    citation_allowed: bool = False
    source_type: str = "confirmed_memory"

    def to_context_item(self) -> dict[str, Any]:
        """Structured handoff for ContextBuilder; Memory is never PDF evidence."""

        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "kind": self.kind,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "score": self.score,
            "importance": self.importance,
            "inclusion_reason": list(self.inclusion_reason),
            "citation_allowed": False,
            "source_type": self.source_type,
        }


@dataclass(slots=True)
class MemoryRetrievalResult:
    query_plan: QueryPlan
    hits: list[MemoryHit] = field(default_factory=list)
    degraded: bool = False
    degradation_reasons: list[str] = field(default_factory=list)


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _contains_term(content: str, term: str) -> bool:
    text = str(content or "")
    candidate = str(term or "").strip()
    if not candidate:
        return False
    if _CJK_ONLY_RE.fullmatch(candidate):
        return candidate in text
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(candidate)}(?![A-Za-z0-9_])",
            text,
            flags=re.IGNORECASE,
        )
    )


class MemoryRetriever:
    """Retrieve only relevant confirmed Memory under explicit scope quotas."""

    def __init__(
        self,
        repository: MemoryRepository,
        *,
        query_planner: AcademicQueryPlanner | None = None,
        paper_limit: int = 3,
        user_limit: int = 2,
        minimum_relevance: float = 0.04,
        rrf_k: int = 20,
    ) -> None:
        self.repository = repository
        self.query_planner = query_planner or AcademicQueryPlanner()
        self.paper_limit = max(0, min(int(paper_limit), 8))
        self.user_limit = max(0, min(int(user_limit), 8))
        self.minimum_relevance = max(0.0, min(float(minimum_relevance), 1.0))
        self.rrf_k = max(1, int(rrf_k))

    @staticmethod
    def _ranks(rows: list[dict[str, Any]]) -> dict[str, int]:
        ranks: dict[str, int] = {}
        for index, row in enumerate(rows, 1):
            memory_id = str(row.get("id") or "")
            if memory_id and memory_id not in ranks:
                ranks[memory_id] = index
        return ranks

    @staticmethod
    def _safe_search(
        result: MemoryRetrievalResult,
        search: Any,
        *,
        reason: str,
    ) -> list[dict[str, Any]]:
        try:
            return list(search())
        except Exception as exc:
            result.degraded = True
            result.degradation_reasons.append(
                f"{reason}:{type(exc).__name__}"
            )
            logger.warning(
                "Memory sparse retrieval degraded (%s: %s)",
                reason,
                type(exc).__name__,
            )
            logger.debug("Memory sparse retrieval exception", exc_info=True)
            return []

    def retrieve(
        self,
        *,
        user_id: int,
        paper_id: int,
        query: str,
    ) -> MemoryRetrievalResult:
        plan = self.query_planner.plan(query)
        result = MemoryRetrievalResult(query_plan=plan)
        candidates = self.repository.list_retrievable_memories(
            user_id=int(user_id), paper_id=int(paper_id), limit=200
        )
        if not candidates:
            return result

        # The Reader opening has no user question.  Preserve a compact paper
        # summary if one exists, but never inject global user preferences just
        # because the relevance signal is absent.
        if not plan.normalized_query:
            paper_items = [
                item for item in candidates if item["scope_type"] == "paper"
            ][: self.paper_limit]
            result.hits = [
                MemoryHit(
                    memory_id=str(item["id"]),
                    user_id=int(item["user_id"]),
                    scope_type=str(item["scope_type"]),
                    scope_id=str(item["scope_id"]),
                    kind=str(item["kind"]),
                    content=str(item["content"]),
                    score=0.0,
                    lexical_score=0.0,
                    importance=float(item.get("importance") or 0.5),
                    inclusion_reason=("opening_paper_scope_fallback",),
                )
                for item in paper_items
            ]
            return result

        # unicode61 is useful for English/acronyms.  It intentionally excludes
        # raw CJK terms because ``memories_fts`` stores canonical memory text,
        # not the document chunker's character-spaced sparse projection.
        unicode_terms = [
            term for term in plan.unicode_terms if not _CJK_ONLY_RE.fullmatch(term)
        ]
        unicode_query = build_unicode61_query(unicode_terms)
        unicode_rows: list[dict[str, Any]] = []
        if unicode_query:
            if self.repository.has_memory_fts():
                unicode_rows = self._safe_search(
                    result,
                    lambda: self.repository.search_retrievable_memories_fts(
                        user_id=int(user_id),
                        paper_id=int(paper_id),
                        match_query=unicode_query,
                        limit=100,
                    ),
                    reason="memory_unicode61_unavailable",
                )
            else:
                result.degraded = True
                result.degradation_reasons.append("memory_unicode61_fts_unavailable")

        trigram_query = build_trigram_query(plan.trigram_terms)
        trigram_rows: list[dict[str, Any]] = []
        if plan.has_cjk and trigram_query:
            if self.repository.has_memory_trigram_fts():
                trigram_rows = self._safe_search(
                    result,
                    lambda: self.repository.search_retrievable_memories_fts(
                        user_id=int(user_id),
                        paper_id=int(paper_id),
                        match_query=trigram_query,
                        trigram=True,
                        limit=100,
                    ),
                    reason="memory_trigram_unavailable",
                )
            else:
                result.degraded = True
                result.degradation_reasons.append("memory_trigram_fts_unavailable")

        unicode_rank = self._ranks(unicode_rows)
        trigram_rank = self._ranks(trigram_rows)
        lexical_terms = _ordered_unique(
            [
                *plan.unicode_terms,
                *plan.trigram_terms,
                *plan.cross_language_terms,
            ]
        )[:24]
        candidates_by_id = {str(item["id"]): item for item in candidates}
        scored: list[MemoryHit] = []
        for memory_id, item in candidates_by_id.items():
            reasons: list[str] = []
            rrf_score = 0.0
            if memory_id in unicode_rank:
                rrf_score += 1.0 / (self.rrf_k + unicode_rank[memory_id])
                reasons.append("unicode61_fts")
            if memory_id in trigram_rank:
                rrf_score += 1.0 / (self.rrf_k + trigram_rank[memory_id])
                reasons.append("trigram_fts")
            matched_terms = [
                term
                for term in lexical_terms
                if _contains_term(str(item["content"]), term)
            ]
            overlap = len(matched_terms) / max(1, min(len(lexical_terms), 5))
            if matched_terms:
                reasons.append("lexical_overlap")
            # A memory needs a direct lexical hit.  There is deliberately no
            # recency-only global fallback for a question, which is the source
            # of the former unrelated long-memory pollution.
            if not reasons:
                continue
            scope_type = str(item["scope_type"])
            scope_bonus = 0.04 if scope_type == "paper" else 0.015
            importance = float(item.get("importance") or 0.5)
            score = rrf_score + (0.12 * overlap) + scope_bonus + (0.02 * importance)
            if score < self.minimum_relevance:
                continue
            reasons.append(f"scope:{scope_type}")
            scored.append(
                MemoryHit(
                    memory_id=memory_id,
                    user_id=int(item["user_id"]),
                    scope_type=scope_type,
                    scope_id=str(item["scope_id"]),
                    kind=str(item["kind"]),
                    content=str(item["content"]),
                    score=score,
                    lexical_score=rrf_score + (0.12 * overlap),
                    importance=importance,
                    inclusion_reason=tuple(reasons),
                )
            )

        scored.sort(
            key=lambda hit: (
                -hit.score,
                -hit.importance,
                hit.memory_id,
            )
        )
        paper_hits: list[MemoryHit] = []
        user_hits: list[MemoryHit] = []
        seen_content: set[str] = set()
        for hit in scored:
            content_key = re.sub(r"\s+", " ", hit.content).casefold()
            if content_key in seen_content:
                continue
            if hit.scope_type == "paper" and len(paper_hits) < self.paper_limit:
                paper_hits.append(hit)
                seen_content.add(content_key)
            elif hit.scope_type == "user" and len(user_hits) < self.user_limit:
                user_hits.append(hit)
                seen_content.add(content_key)
        result.hits = [*paper_hits, *user_hits]
        return result


def format_memory_context(hits: list[MemoryHit]) -> str:
    """Compatibility renderer for old callers until ContextPackage replaces it."""

    if not hits:
        return ""
    return "【用户确认的相关记忆】\n" + "\n".join(
        f"- [{hit.scope_type}/{hit.kind}] {hit.content}" for hit in hits
    )


__all__ = [
    "MemoryHit",
    "MemoryRetriever",
    "MemoryRetrievalResult",
    "format_memory_context",
]

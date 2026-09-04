"""User-scoped bilingual hybrid retrieval over canonical paper chunks.

Sparse unicode61, CJK trigram and dense LanceDB retrieval remain independent
ranked lists.  Their raw scores use different scales, so the service fuses
ranks with weighted reciprocal-rank fusion and optionally applies a task-aware
reranker.  Any projection can be unavailable without making a paper unreadable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from ...infrastructure.vector.lancedb_store import (
    LanceDBVectorStore,
    VectorHit,
    VectorStoreUnavailable,
)
from ...repositories.document_repository import DocumentRepository
from ..embedding.base import (
    EmbeddingProvider,
    EmbeddingUnavailable,
    embedding_document_config_hash,
    embed_query,
)
from ..rerank.base import Reranker, RerankerUnavailable, rerank_documents
from .academic_query_planner import AcademicQueryPlanner, QueryPlan
from .sparse_retriever import DualSparseRetriever


_LEGACY_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./+-]*|[\u4e00-\u9fff]+")


def build_fts_query(query: str) -> str:
    """Legacy unicode61 renderer retained for API/test compatibility.

    New retrieval uses :class:`AcademicQueryPlanner` plus
    :class:`DualSparseRetriever`.  Keeping this small helper stable avoids
    silently changing older callers that explicitly constructed a one-index
    FTS query.
    """

    tokens = _LEGACY_TOKEN_RE.findall(str(query or "").strip())
    unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        clean = token.strip()
        if clean and re.fullmatch(r"[\u4e00-\u9fff]+", clean):
            clean = " ".join(clean)
        key = clean.lower()
        if clean and key not in seen:
            unique.append(clean)
            seen.add(key)
    return " OR ".join(
        f'"{token.replace(chr(34), chr(34) * 2)}"' for token in unique[:32]
    )


@dataclass(slots=True)
class HybridHit:
    chunk_uid: str
    paper_id: int
    document_version_id: str
    content_type: str
    display_text: str
    section_path: list[str]
    page_start: int
    page_end: int
    rrf_score: float
    sparse_score: float | None = None
    sparse_unicode_score: float | None = None
    sparse_trigram_score: float | None = None
    dense_score: float | None = None
    rerank_score: float | None = None
    structural_score: float = 0.0
    sources: tuple[str, ...] = ()


@dataclass(slots=True)
class HybridRetrievalResult:
    query: str
    query_plan: QueryPlan | None = None
    hits: list[HybridHit] = field(default_factory=list)
    sparse_count: int = 0
    sparse_unicode_count: int = 0
    sparse_trigram_count: int = 0
    dense_count: int = 0
    candidate_count: int = 0
    rerank_candidate_count: int = 0
    degraded: bool = False
    degradation_reasons: list[str] = field(default_factory=list)


class HybridChunkRetriever:
    """Retrieve active chunks with repeated scope checks and explicit fallback."""

    def __init__(
        self,
        repository: DocumentRepository,
        *,
        vector_store: LanceDBVectorStore | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        reranker: Reranker | None = None,
        min_rerank_score: float | None = None,
        rrf_k: int = 60,
        rrf_weights: Mapping[str, float] | None = None,
        query_planner: AcademicQueryPlanner | None = None,
        sparse_retriever: DualSparseRetriever | None = None,
    ) -> None:
        self.repository = repository
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.reranker = reranker
        self.min_rerank_score = (
            float(min_rerank_score) if min_rerank_score is not None else None
        )
        self.rrf_k = max(1, int(rrf_k))
        requested_weights = dict(rrf_weights or {})
        self.rrf_weights = {
            "unicode": max(0.0, float(requested_weights.get("unicode", 1.0))),
            "trigram": max(0.0, float(requested_weights.get("trigram", 1.0))),
            "dense": max(0.0, float(requested_weights.get("dense", 1.0))),
        }
        self.query_planner = query_planner or AcademicQueryPlanner()
        self.sparse_retriever = sparse_retriever or DualSparseRetriever(repository)

    @staticmethod
    def _append_degradation(result: HybridRetrievalResult, *reasons: str) -> None:
        for reason in reasons:
            if reason and reason not in result.degradation_reasons:
                result.degradation_reasons.append(reason)
        if reasons:
            result.degraded = True

    @staticmethod
    def _ranked_rows(rows: list[dict[str, Any]]) -> dict[str, tuple[int, dict[str, Any]]]:
        ranked: dict[str, tuple[int, dict[str, Any]]] = {}
        for index, row in enumerate(rows, 1):
            uid = str(row.get("chunk_uid") or "")
            if uid and uid not in ranked:
                ranked[uid] = (index, row)
        return ranked

    @staticmethod
    def _structural_score(
        plan: QueryPlan,
        *,
        content_type: str,
        section_path: list[str],
    ) -> float:
        """Apply only explicit user source constraints as bounded priors.

        This is not a hidden relevance model.  It merely honours a question
        such as "what does the abstract say" or "which columns are in Table
        1" when semantically similar later prose would otherwise crowd out
        the requested document structure.
        """

        score = 0.0
        section = " > ".join(str(value) for value in section_path).casefold()
        if plan.section_preferences and any(
            str(preference).casefold() in section
            for preference in plan.section_preferences
        ):
            score += 0.03
        if plan.content_type_preferences and str(content_type) in set(
            plan.content_type_preferences
        ):
            score += 0.012
        return score

    @staticmethod
    def _candidate_sort_key(hit: HybridHit) -> tuple[float, float, int, str]:
        return (
            -(float(hit.rrf_score) + float(hit.structural_score)),
            -float(hit.rrf_score),
            int(hit.page_start),
            str(hit.chunk_uid),
        )

    @staticmethod
    def _rerank_sort_key(hit: HybridHit) -> tuple[float, float, float, int, str]:
        score = float(hit.rerank_score) if hit.rerank_score is not None else float("-inf")
        # DashScope-compatible rerank scores are calibrated to [0, 1].  Apply
        # the small structural prior only on that known scale; custom/local
        # rerankers retain their native ordering rather than receiving an
        # arbitrary score offset.
        adjusted = score
        if 0.0 <= score <= 1.0:
            adjusted += float(hit.structural_score) * 4.0
        return (
            -adjusted,
            -score,
            -float(hit.rrf_score),
            int(hit.page_start),
            str(hit.chunk_uid),
        )

    def retrieve(
        self,
        *,
        user_id: int,
        paper_ids: list[int],
        query: str,
        limit: int = 10,
        sparse_limit: int | None = None,
        dense_limit: int | None = None,
    ) -> HybridRetrievalResult:
        plan = self.query_planner.plan(query)
        result = HybridRetrievalResult(query=plan.normalized_query, query_plan=plan)
        if not plan.normalized_query or not paper_ids:
            return result
        paper_scope = list(dict.fromkeys(int(value) for value in paper_ids))[:400]
        max_limit = max(1, min(int(limit), 100))
        # Ten final context chunks need a wider *retrieval* pool.  In
        # particular, cross-language and table evidence can be semantically
        # relevant yet rank outside a 3x lexical pool before reranking.  The
        # pool remains capped at 100 and only expands when a dense or rerank
        # branch exists; sparse-only fallback retains its cheaper 3x bound.
        has_precision_branch = bool(
            self.reranker is not None
            or (self.vector_store is not None and self.embedding_provider is not None)
        )
        has_explicit_structure_or_language_bridge = bool(
            plan.cross_language_terms
            or plan.section_preferences
            or plan.content_type_preferences
        )
        candidate_multiplier = (
            10
            if has_precision_branch and has_explicit_structure_or_language_bridge
            else 5
            if has_precision_branch
            else 3
        )
        candidate_limit = max(
            1,
            min(int(sparse_limit or max_limit * candidate_multiplier), 100),
        )

        sparse = self.sparse_retriever.retrieve(
            user_id=int(user_id),
            paper_ids=paper_scope,
            plan=plan,
            limit=candidate_limit,
        )
        result.sparse_unicode_count = len(sparse.unicode_rows)
        result.sparse_trigram_count = len(sparse.trigram_rows)
        result.sparse_count = len(
            {
                str(row.get("chunk_uid"))
                for row in [*sparse.unicode_rows, *sparse.trigram_rows]
                if str(row.get("chunk_uid") or "")
            }
        )
        if sparse.degraded:
            self._append_degradation(result, *sparse.degradation_reasons)

        dense_hits: list[VectorHit] = []
        active_version_ids: list[str] = []
        dense_version_ids: list[str] = []
        vector_store = self.vector_store
        dense_configured = vector_store is not None and self.embedding_provider is not None
        if not dense_configured:
            # A sparse-only request should not issue an active-version query
            # per paper merely to discover that its dense branch is disabled.
            # This matters for bounded public scorecards as well as a user's
            # larger local library.
            self._append_degradation(result, "dense_retrieval_not_configured")
        else:
            active_versions = [
                version
                for paper_id in paper_scope
                if (
                    version := self.repository.get_active_version(
                        user_id=int(user_id), paper_id=paper_id
                    )
                )
            ]
            active_version_ids = [str(version["id"]) for version in active_versions]
            provider_name = str(getattr(self.embedding_provider, "provider", "embedding"))
            model_name = str(getattr(self.embedding_provider, "model", ""))
            dimension = int(getattr(self.embedding_provider, "dimension", 0) or 0)
            config_hash = embedding_document_config_hash(self.embedding_provider)
            dense_version_ids = [
                str(version["id"])
                for version in active_versions
                if str(version.get("embedding_status") or "") == "ready"
                and int(version.get("embedding_indexed_count") or 0) > 0
                and str(version.get("embedding_provider") or "") == provider_name
                and str(version.get("embedding_model") or "") == model_name
                and int(version.get("embedding_dimension") or 0) == dimension
                and str(version.get("embedding_config_hash") or "") == config_hash
            ]
        if dense_configured:
            assert vector_store is not None
            assert self.embedding_provider is not None
            if not active_version_ids:
                self._append_degradation(result, "no_active_document_version")
            elif not dense_version_ids:
                self._append_degradation(result, "dense_index_not_ready_or_mismatched")
            else:
                if len(dense_version_ids) < len(active_version_ids):
                    self._append_degradation(result, "dense_index_partial_scope")
                try:
                    embedding = embed_query(self.embedding_provider, plan.dense_query)
                    if len(embedding.vectors) != 1:
                        raise EmbeddingUnavailable("query embedding count mismatch")
                    dense_hits = vector_store.search(
                        embedding.vectors[0],
                        user_id=int(user_id),
                        paper_ids=paper_scope,
                        document_version_ids=dense_version_ids,
                        limit=max(
                            1,
                            min(
                                int(dense_limit or max_limit * candidate_multiplier),
                                100,
                            ),
                        ),
                    )
                except (EmbeddingUnavailable, VectorStoreUnavailable, ValueError) as exc:
                    self._append_degradation(
                        result, f"dense_search_unavailable:{type(exc).__name__}"
                    )
                except Exception as exc:
                    # Provider/SDK errors are isolated from the reading path.  The
                    # result still exposes sparse evidence and a machine-readable
                    # reason for tracing instead of silently falling back.
                    self._append_degradation(
                        result, f"dense_search_error:{type(exc).__name__}"
                    )
        result.dense_count = len(dense_hits)

        unicode_rank = self._ranked_rows(sparse.unicode_rows)
        trigram_rank = self._ranked_rows(sparse.trigram_rows)
        dense_rank = {
            hit.chunk_uid: (index, hit)
            for index, hit in enumerate(dense_hits, 1)
            if str(hit.chunk_uid)
        }
        ordered_uids = list(
            dict.fromkeys(
                [*unicode_rank.keys(), *trigram_rank.keys(), *dense_rank.keys()]
            )
        )
        rows = self.repository.get_chunks_by_uid(
            user_id=int(user_id), chunk_uids=ordered_uids, active_only=True
        )
        row_by_uid = {str(row["chunk_uid"]): row for row in rows}
        hits: list[HybridHit] = []
        for uid in ordered_uids:
            row = row_by_uid.get(uid)
            if row is None:
                continue
            unicode_item = unicode_rank.get(uid)
            trigram_item = trigram_rank.get(uid)
            dense_item = dense_rank.get(uid)
            rrf = 0.0
            sources: list[str] = []
            if unicode_item:
                rrf += self.rrf_weights["unicode"] / (
                    self.rrf_k + unicode_item[0]
                )
                # Keep ``bm25`` as the compatibility source identifier for
                # unicode61; trigram is explicitly distinguishable below.
                sources.append("bm25")
            if trigram_item:
                rrf += self.rrf_weights["trigram"] / (
                    self.rrf_k + trigram_item[0]
                )
                sources.append("bm25_trigram")
            if dense_item:
                rrf += self.rrf_weights["dense"] / (self.rrf_k + dense_item[0])
                sources.append("vector")
            section_path_raw = row.get("section_path_json") or "[]"
            try:
                section_path_value = json.loads(section_path_raw)
            except (TypeError, ValueError):
                section_path_value = []
            unicode_score = (
                float(unicode_item[1]["bm25_score"])
                if unicode_item and unicode_item[1].get("bm25_score") is not None
                else None
            )
            trigram_score = (
                float(trigram_item[1]["bm25_score"])
                if trigram_item and trigram_item[1].get("bm25_score") is not None
                else None
            )
            hits.append(
                HybridHit(
                    chunk_uid=uid,
                    paper_id=int(row.get("paper_id") or 0),
                    document_version_id=str(row.get("document_version_id") or ""),
                    content_type=str(row.get("content_type") or "paragraph"),
                    display_text=str(row.get("display_text") or ""),
                    section_path=(
                        [str(value) for value in section_path_value]
                        if isinstance(section_path_value, list)
                        else []
                    ),
                    page_start=int(row.get("page_start") or 0),
                    page_end=int(row.get("page_end") or 0),
                    rrf_score=rrf,
                    sparse_score=(
                        unicode_score if unicode_score is not None else trigram_score
                    ),
                    sparse_unicode_score=unicode_score,
                    sparse_trigram_score=trigram_score,
                    dense_score=float(dense_item[1].score) if dense_item else None,
                    structural_score=self._structural_score(
                        plan,
                        content_type=str(row.get("content_type") or "paragraph"),
                        section_path=(
                            [str(value) for value in section_path_value]
                            if isinstance(section_path_value, list)
                            else []
                        ),
                    ),
                    sources=tuple(sources),
                )
            )
        hits.sort(key=self._candidate_sort_key)
        result.candidate_count = len(hits)

        if self.reranker is not None and hits:
            try:
                provider_max_documents = getattr(self.reranker, "max_documents", 100)
                try:
                    provider_max_documents = max(
                        1, min(100, int(provider_max_documents))
                    )
                except (TypeError, ValueError):
                    provider_max_documents = 100
                rerank_candidate_limit = min(
                    len(hits),
                    provider_max_documents,
                    max(max_limit, max_limit * candidate_multiplier),
                )
                rerank_candidates = hits[:rerank_candidate_limit]
                result.rerank_candidate_count = len(rerank_candidates)
                reranked = rerank_documents(
                    self.reranker,
                    plan.normalized_query,
                    [item.display_text for item in rerank_candidates],
                    # Request scores for the bounded candidate pool and apply
                    # the explicit structural preference locally.  Requesting
                    # only top-k from the provider makes an abstract/table
                    # preference impossible to honour when its evidence is
                    # initially ranked just below k.
                    top_n=rerank_candidate_limit,
                    instruction=plan.rerank_instruction,
                )
                reordered: list[HybridHit] = []
                used: set[int] = set()
                for item in reranked:
                    if item.index < 0 or item.index >= len(rerank_candidates):
                        raise RerankerUnavailable("rerank index outside candidate list")
                    used.add(item.index)
                    hit = rerank_candidates[item.index]
                    hit.rerank_score = float(item.score)
                    hit.sources = tuple(dict.fromkeys((*hit.sources, "rerank")))
                    reordered.append(hit)
                reordered.extend(
                    hit
                    for index, hit in enumerate(rerank_candidates)
                    if index not in used
                )
                reordered.sort(key=self._rerank_sort_key)
                hits = reordered + hits[len(rerank_candidates) :]
                if self.min_rerank_score is not None:
                    eligible = [
                        hit
                        for hit in hits
                        if hit.rerank_score is not None
                        and hit.rerank_score >= self.min_rerank_score
                    ]
                    if eligible:
                        if len(eligible) < len(hits):
                            result.degradation_reasons.append(
                                "low_rerank_candidates_filtered"
                            )
                        hits = eligible
                    else:
                        # A threshold is a precision preference, never a
                        # license to erase every evidence candidate.  Preserve
                        # the best reranked hit and make the policy visible.
                        hits = hits[:1]
                        result.degradation_reasons.append(
                            "low_rerank_all_candidates_kept_top"
                        )
            except (RerankerUnavailable, ValueError, TypeError) as exc:
                self._append_degradation(
                    result, f"rerank_unavailable:{type(exc).__name__}"
                )
        result.hits = hits[:max_limit]
        return result


__all__ = [
    "HybridChunkRetriever",
    "HybridHit",
    "HybridRetrievalResult",
    "build_fts_query",
]

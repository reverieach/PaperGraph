
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, cast
from collections.abc import Iterable, Mapping

from fastapi import BackgroundTasks, HTTPException
from starlette.concurrency import run_in_threadpool

from ...agents import PaperAnalysisAgent
from ...agents.support.reader_reference_lookup_tool import READER_RELATED_FROM_BIBLIOGRAPHY, READER_RELATED_FROM_PRE_SEARCH
from ...services.citation import EvidenceRegistry
from ...services.context import ContextPackage, DynamicContextBuilder, TokenCounter
from .reader_trace import ReaderRequestTrace

logger = logging.getLogger(__name__)

_OPENING_PROMPT = (
    "请用中文写一段不超过 380 字的导读：研究问题、核心方法、实验与结论的阅读要点。"
    "仅依据当前提供的摘要与 canonical 证据组织表述；勿单列「不确定处」「局限」或待查清单（用户追问时再说明材料范围即可）。"
)
_NO_HISTORY_PLACEHOLDER = "（尚无对话历史）"
_MANUAL_MEMORY_REPLY = (
    "我不会自动写入论文记忆或长期用户记忆。完成阅读后，请点击右上角"
    "「总结本次阅读」，在弹窗中检查候选内容并确认保存。"
)
_MEMORY_WRITE_PATTERNS = (
    re.compile(r"(?:请|帮我|替我|给我|希望你|我要你).{0,8}(?:记住|记下来)"),
    re.compile(r"^\s*(?:记住|记下来)"),
    re.compile(r"(?:保存|加入|写入).{0,10}(?:记忆|偏好|研究目标)"),
)

# The Reader has one document-side budget. Canonical tools may add evidence in
# later function-call turns, so the initial package explicitly reserves part
# of it instead of silently letting the real prompt exceed its declared cap.
# The latest user question is bounded separately and is never silently cut.
_READER_CONTEXT_TOKEN_BUDGET = 3_600
_READER_TOOL_CONTEXT_TOKEN_RESERVE = 800
_READER_CANONICAL_INITIAL_CONTEXT_TOKEN_BUDGET = (
    _READER_CONTEXT_TOKEN_BUDGET - _READER_TOOL_CONTEXT_TOKEN_RESERVE
)
_READER_MAX_QUERY_TOKENS = 800


@dataclass(slots=True)
class _RagContextBuild:
    package: ContextPackage | None
    active_document: bool = False
    degradation_reasons: tuple[str, ...] = ()
    retrieval_trace: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _ReaderPreparedContext:
    paper: Any
    package: ContextPackage
    pdf_ref_text: str
    pdf_parsing: bool
    pdf_pages: list[dict[str, Any]]
    context_mode: str
    document_version_id: str | None = None
    degradation_reasons: tuple[str, ...] = field(default_factory=tuple)
    memory_hit_count: int = 0
    memory_degradation_reasons: tuple[str, ...] = field(default_factory=tuple)
    retrieval_trace: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.package.text


def _is_explicit_memory_write_request(text: str) -> bool:
    value = str(text or "").strip()
    return bool(value) and any(pattern.search(value) for pattern in _MEMORY_WRITE_PATTERNS)

class PaperReaderService:
    def __init__(self, db: Any, agent: Any | None = None) -> None:
        self._db = db
        # Reader Agent carries recommendation pagination state, so it must be
        # request-scoped rather than the process-global analysis singleton.
        self._agent = agent or PaperAnalysisAgent()

    @property
    def db(self) -> Any:
        return self._db

    @staticmethod
    def _format_reader_history(turns: Iterable[Any]) -> str:
        lines: list[str] = []
        tail = list(turns or [])[-24:]
        for t in tail:
            if isinstance(t, Mapping):
                role_value = t.get("role")
                content_value = t.get("content")
            else:
                role_value = getattr(t, "role", None)
                content_value = getattr(t, "content", None)
            role = str(role_value or "").strip().lower()
            content = str(content_value or "").strip()
            if not content:
                continue
            if role not in ("user", "assistant"):
                role = "user"
            label = "用户" if role == "user" else "助手"
            lines.append(f"{label}：{content}")
        return "\n\n".join(lines)

    @staticmethod
    def _validate_user_message(user_message: str) -> str:
        """Reject oversized questions rather than silently cutting user intent."""

        value = str(user_message or "").strip()
        if not value:
            raise HTTPException(status_code=422, detail="问题不能为空")
        counter = TokenCounter()
        token_count = counter.count(value)
        if token_count > _READER_MAX_QUERY_TOKENS:
            raise HTTPException(
                status_code=422,
                detail=(
                    "问题过长，当前阅读助手最多接受 "
                    f"{_READER_MAX_QUERY_TOKENS} 个本地估算 Token；请拆分后重试。"
                ),
            )
        return value

    async def _build_reader_context(
        self,
        paper_id: int,
        *,
        user_id: int,
        user_message: str = "",
        history_lines: str = "",
    ) -> _ReaderPreparedContext:
        from ...repositories.memory_repository import MemoryRepository
        from ...services.memory.retriever import MemoryRetriever
        from .paper_reader_context import build_reader_context_for_paper

        paper = await run_in_threadpool(
            self._db.get_paper_by_id,
            int(paper_id),
            user_id=int(user_id),
        )
        if not paper:
            raise HTTPException(status_code=404, detail="文献不存在")

        # An active canonical version is authoritative.  Resolve it before
        # touching the legacy excerpt/cache path so a canonical Reader request
        # never eagerly reparses the full PDF in the background.
        from ...repositories.document_repository import DocumentRepository

        active_document: dict[str, Any] | None = None
        initial_degradations: list[str] = []
        active_document_lookup_failed = False
        try:
            active_document = await run_in_threadpool(
                DocumentRepository(self._db.db_path).get_active_version,
                user_id=int(user_id),
                paper_id=int(paper_id),
            )
        except Exception as exc:
            active_document_lookup_failed = True
            logger.warning(
                "paper_reader.active_document_lookup_failed",
                extra={
                    "paper_id": int(paper_id),
                    "user_id": int(user_id),
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )
            initial_degradations.append(
                f"active_document_lookup_failed:{type(exc).__name__}"
            )

        base_ctx = ""
        pdf_ref_text = ""
        pdf_parsing = False
        pdf_pages: list[dict[str, Any]] = []
        if active_document is None and not active_document_lookup_failed:
            # Legacy papers remain readable while they await canonical ingest.
            # Only this explicit fallback may load the old PDF cache.
            paper, base_ctx, pdf_ref_text, pdf_parsing, pdf_pages = await run_in_threadpool(
                build_reader_context_for_paper,
                self._db,
                paper_id,
                user_id=int(user_id),
            )
            if not paper:
                raise HTTPException(status_code=404, detail="文献不存在")

        memory_result = await run_in_threadpool(
            MemoryRetriever(MemoryRepository(self._db.db_path)).retrieve,
            user_id=int(user_id),
            paper_id=int(paper_id),
            query=user_message,
        )
        if memory_result.degraded:
            logger.info(
                "paper_reader.memory_retrieval_degraded",
                extra={
                    "paper_id": int(paper_id),
                    "reasons": list(memory_result.degradation_reasons),
                },
            )
        memory_items = [hit.to_context_item() for hit in memory_result.hits]
        rag_result = _RagContextBuild(
            package=None,
            active_document=active_document is not None or active_document_lookup_failed,
            degradation_reasons=tuple(initial_degradations),
        )
        if active_document is not None and str(user_message or "").strip():
            rag_result = await run_in_threadpool(
                self._build_rag_context_package,
                paper=paper,
                paper_id=int(paper_id),
                user_id=int(user_id),
                query=str(user_message),
                memory_context=memory_items,
                history_lines=history_lines,
                active_document=active_document,
            )
        elif active_document is not None:
            rag_result = await run_in_threadpool(
                self._build_canonical_opening_context_package,
                paper=paper,
                paper_id=int(paper_id),
                user_id=int(user_id),
                memory_context=memory_items,
                history_lines=history_lines,
                active_document=active_document,
            )
        elif active_document_lookup_failed:
            rag_result = _RagContextBuild(
                package=None,
                active_document=True,
                degradation_reasons=tuple(initial_degradations),
            )
        elif not rag_result.degradation_reasons:
            rag_result = _RagContextBuild(
                package=None,
                active_document=False,
                degradation_reasons=("no_active_document_version",),
            )

        combined_degradations = tuple(
            dict.fromkeys([*initial_degradations, *rag_result.degradation_reasons])
        )
        if rag_result.package is not None:
            package = rag_result.package
            context_mode = (
                "hybrid_rag_v2"
                if str(user_message or "").strip()
                else "canonical_opening_v2"
            )
        elif active_document is not None or active_document_lookup_failed:
            # Do not silently replace a failed canonical request with the old
            # full-PDF excerpt.  The user gets a bounded, explicit degraded
            # package instead of an answer that looks grounded but is not.
            package = DynamicContextBuilder(
                max_tokens=_READER_CANONICAL_INITIAL_CONTEXT_TOKEN_BUDGET,
                max_evidence=10,
            ).build(
                paper_metadata=self._paper_metadata_for_rag(paper),
                memories=memory_items,
                history=history_lines,
                tool_results=[
                    "当前论文的 canonical 索引状态或本轮检索上下文暂不可用；"
                    "请说明证据不足，不要编造 PDF 证据或页码引用。"
                ],
                query=str(user_message or ""),
            )
            context_mode = "canonical_degraded"
        else:
            # Legacy papers remain readable while they await canonical ingest.
            # Their content is deliberately marked non-citable: a PDF excerpt
            # is not a replacement for a versioned, retrieved chunk.
            fallback_tool_results: list[str] = []
            if rag_result.degradation_reasons:
                fallback_tool_results.append(
                    "本轮本地检索暂不可用或索引尚未就绪；请基于现有材料回答，"
                    "不要编造 PDF 证据或页码引用。"
                )
            package = DynamicContextBuilder(
                max_tokens=_READER_CONTEXT_TOKEN_BUDGET,
                max_evidence=10,
            ).build(
                paper_metadata=self._paper_metadata_for_rag(paper),
                memories=memory_items,
                history=history_lines,
                tool_results=fallback_tool_results,
                legacy_context=base_ctx,
                query=str(user_message or ""),
            )
            context_mode = "legacy_fallback"
        return _ReaderPreparedContext(
            paper=paper,
            package=package,
            pdf_ref_text=pdf_ref_text,
            pdf_parsing=pdf_parsing,
            pdf_pages=pdf_pages,
            context_mode=context_mode,
            document_version_id=(
                str(active_document.get("id") or "") or None
                if active_document is not None
                else None
            ),
            degradation_reasons=combined_degradations,
            memory_hit_count=len(memory_result.hits),
            memory_degradation_reasons=tuple(memory_result.degradation_reasons),
            retrieval_trace=dict(rag_result.retrieval_trace),
        )

    @staticmethod
    def _paper_metadata_for_rag(paper: Any) -> str:
        authors = ", ".join(
            str(getattr(author, "name", "") or "").strip()
            for author in (getattr(paper, "authors", None) or [])
            if str(getattr(author, "name", "") or "").strip()
        )
        lines = [
            f"标题：{str(getattr(paper, 'title', '') or '').strip()}",
            f"作者：{authors or '—'}",
            f"年份：{getattr(paper, 'year', None) or '—'}",
            f"来源/期刊：{str(getattr(paper, 'journal', '') or '').strip() or '—'}",
            f"DOI：{str(getattr(paper, 'doi', '') or '').strip() or '—'}",
            f"摘要：{str(getattr(paper, 'abstract', '') or '').strip() or '（无摘要）'}",
        ]
        keywords = getattr(paper, "keywords", None) or []
        if keywords:
            lines.append("关键词：" + ", ".join(str(value) for value in keywords[:24]))
        return "\n".join(lines)

    def _build_rag_context_package(
        self,
        *,
        paper: Any,
        paper_id: int,
        user_id: int,
        query: str,
        memory_context: list[dict[str, Any]] | str,
        history_lines: str = "",
        active_document: Mapping[str, Any] | None = None,
    ) -> _RagContextBuild:
        """Build a query-specific, canonical context package when indexed.

        The caller owns the legacy fallback.  This method never appends a full
        PDF excerpt after a successful relevance guard, because that would
        negate retrieval and turn unrelated text into de facto context.
        """

        try:
            from ...infrastructure.vector.lancedb_store import LanceDBVectorStore
            from ...repositories.document_repository import DocumentRepository
            from ...services.embedding.dashscope_embedding import DashScopeEmbeddingProvider
            from ...services.ingest.factory import resolve_rag_storage_paths
            from ...services.rerank.dashscope_reranker import DashScopeReranker
            from ...services.retrieval.evidence_expander import EvidenceExpander
            from ...services.retrieval.hybrid import HybridChunkRetriever
            from ...settings import get_settings

            repository = DocumentRepository(self._db.db_path)
            active = (
                dict(active_document)
                if active_document is not None
                else repository.get_active_version(
                    user_id=int(user_id), paper_id=int(paper_id)
                )
            )
            if not active:
                return _RagContextBuild(
                    package=None,
                    active_document=False,
                    degradation_reasons=("no_active_document_version",),
                )
            settings = get_settings()
            embedding: DashScopeEmbeddingProvider | None = None
            vector_store: LanceDBVectorStore | None = None
            if settings.rag_embedding_enabled:
                candidate_embedding = DashScopeEmbeddingProvider()
                if candidate_embedding.api_key and candidate_embedding.base_url:
                    embedding = candidate_embedding
                    _, vector_path = resolve_rag_storage_paths(self._db.db_path)
                    vector_store = LanceDBVectorStore(
                        vector_path,
                        dimension=embedding.dimension,
                    )
            reranker: DashScopeReranker | None = None
            if settings.rag_rerank_enabled:
                candidate_reranker = DashScopeReranker()
                if candidate_reranker.api_key and candidate_reranker.endpoint:
                    reranker = candidate_reranker
            memory_items = (
                memory_context
                if isinstance(memory_context, list)
                else ([{"content": memory_context}] if memory_context else [])
            )
            retrieval = HybridChunkRetriever(
                repository,
                vector_store=vector_store,
                embedding_provider=embedding,
                reranker=reranker,
                # A model score is not portable across languages/tasks.  The
                # cutoff is opt-in and stays unset until the Golden dev split
                # provides a calibrated threshold.
                min_rerank_score=settings.rag_rerank_min_score,
            ).retrieve(
                user_id=int(user_id), paper_ids=[int(paper_id)], query=query, limit=10
            )
            plan = retrieval.query_plan
            retrieval_trace: dict[str, Any] = {
                "mode": "hybrid",
                "sparse_count": int(retrieval.sparse_count),
                "sparse_unicode_count": int(retrieval.sparse_unicode_count),
                "sparse_trigram_count": int(retrieval.sparse_trigram_count),
                "dense_count": int(retrieval.dense_count),
                "candidate_count": int(retrieval.candidate_count),
                "rerank_candidate_count": int(retrieval.rerank_candidate_count),
                "returned_hit_count": len(retrieval.hits),
                "query_language": str(getattr(plan, "language", "unknown")),
                "query_task": str(getattr(plan, "task", "unknown")),
                "cross_language_term_count": len(
                    getattr(plan, "cross_language_terms", ()) or ()
                ),
                "section_preferences": list(
                    getattr(plan, "section_preferences", ()) or ()
                ),
                "content_type_preferences": list(
                    getattr(plan, "content_type_preferences", ()) or ()
                ),
                "degradation_reasons": list(retrieval.degradation_reasons),
            }
            if not retrieval.hits:
                # Once a paper has a canonical active version, a completed
                # retrieval with no hits must not re-inject its legacy full
                # excerpt.  That would make a relevance guard meaningless and
                # lets unrelated middle sections dominate the prompt.
                reason = (
                    "本轮没有达到相关性阈值的本地 PDF 片段；不要把论文元数据当作正文证据。"
                    if "low_rerank_candidates_filtered" in retrieval.degradation_reasons
                    else "本轮未从当前论文的 canonical PDF 索引召回相关片段；"
                    "请说明证据不足，不要把元数据或记忆当作论文正文证据。"
                )
                built = DynamicContextBuilder(
                    max_tokens=_READER_CANONICAL_INITIAL_CONTEXT_TOKEN_BUDGET,
                    max_evidence=10,
                ).build(
                    paper_metadata=self._paper_metadata_for_rag(paper),
                    memories=memory_items,
                    history=history_lines,
                    tool_results=[reason],
                    query=query,
                    query_plan=retrieval.query_plan,
                )
                return _RagContextBuild(
                    package=built if built.text.strip() else None,
                    active_document=True,
                    degradation_reasons=tuple(retrieval.degradation_reasons),
                    retrieval_trace=retrieval_trace,
                )
            expansion = EvidenceExpander(repository).expand(
                user_id=int(user_id),
                hits=retrieval.hits,
                max_anchor_hits=4,
                neighbor_radius=1,
                max_chunks=12,
            )
            all_degradations = tuple(
                dict.fromkeys(
                    [*retrieval.degradation_reasons, *expansion.degradation_reasons]
                )
            )
            retrieval_trace.update(
                {
                    "expansion_anchor_count": int(expansion.anchor_count),
                    "expansion_parent_count": int(expansion.parent_count),
                    "expansion_neighbor_count": int(expansion.neighbor_count),
                    "expanded_chunk_count": len(expansion.chunks),
                    "expansion_degradation_reasons": list(
                        expansion.degradation_reasons
                    ),
                }
            )
            builder = DynamicContextBuilder(
                max_tokens=_READER_CANONICAL_INITIAL_CONTEXT_TOKEN_BUDGET,
                max_evidence=10,
            )
            built = builder.build(
                paper_metadata=self._paper_metadata_for_rag(paper),
                retrieved_chunks=expansion.chunks or retrieval.hits,
                memories=memory_items,
                history=history_lines,
                query=query,
                query_plan=retrieval.query_plan,
            )
            if built.text.strip():
                return _RagContextBuild(
                    package=built,
                    active_document=True,
                    degradation_reasons=all_degradations,
                    retrieval_trace=retrieval_trace,
                )
            return _RagContextBuild(
                package=None,
                active_document=True,
                degradation_reasons=all_degradations,
                retrieval_trace=retrieval_trace,
            )
        except Exception as exc:
            # Provider/index failures are isolated from the reading path, but
            # remain observable in structured logs and the prepared context.
            logger.warning(
                "paper_reader.rag_context_failed",
                extra={
                    "paper_id": int(paper_id),
                    "user_id": int(user_id),
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )
            return _RagContextBuild(
                package=None,
                active_document=False,
                degradation_reasons=(f"rag_context_failed:{type(exc).__name__}",),
                retrieval_trace={
                    "mode": "hybrid",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                },
            )

    def _build_canonical_opening_context_package(
        self,
        *,
        paper: Any,
        paper_id: int,
        user_id: int,
        memory_context: list[dict[str, Any]] | str,
        history_lines: str = "",
        active_document: Mapping[str, Any] | None = None,
    ) -> _RagContextBuild:
        """Build a bounded canonical opening without consulting legacy PDF text."""

        try:
            from ...repositories.document_repository import DocumentRepository

            repository = DocumentRepository(self._db.db_path)
            active = (
                dict(active_document)
                if active_document is not None
                else repository.get_active_version(
                    user_id=int(user_id), paper_id=int(paper_id)
                )
            )
            if not active:
                return _RagContextBuild(
                    package=None,
                    active_document=False,
                    degradation_reasons=("no_active_document_version",),
                )
            parents = repository.list_chunks(
                user_id=int(user_id),
                paper_id=int(paper_id),
                document_version_id=str(active["id"]),
                level="parent",
                limit=32,
            )
            memory_items = (
                memory_context
                if isinstance(memory_context, list)
                else ([{"content": memory_context}] if memory_context else [])
            )
            if not parents:
                built = DynamicContextBuilder(
                    max_tokens=_READER_CANONICAL_INITIAL_CONTEXT_TOKEN_BUDGET,
                    max_evidence=8,
                ).build(
                    paper_metadata=self._paper_metadata_for_rag(paper),
                    memories=memory_items,
                    history=history_lines,
                    tool_results=[
                        "当前 canonical 文档尚无可用于导读的 parent chunk；"
                        "请说明材料不足，不要编造正文结论或页码。"
                    ],
                    query=_OPENING_PROMPT,
                )
                return _RagContextBuild(
                    package=built if built.text.strip() else None,
                    active_document=True,
                    degradation_reasons=("canonical_opening_no_parent_chunks",),
                    retrieval_trace={
                        "mode": "canonical_opening",
                        "parent_candidate_count": 0,
                    },
                )
            built = DynamicContextBuilder(
                max_tokens=_READER_CANONICAL_INITIAL_CONTEXT_TOKEN_BUDGET,
                max_evidence=8,
            ).build(
                paper_metadata=self._paper_metadata_for_rag(paper),
                retrieved_chunks=parents,
                memories=memory_items,
                history=history_lines,
                query=_OPENING_PROMPT,
            )
            return _RagContextBuild(
                package=built if built.text.strip() else None,
                active_document=True,
                retrieval_trace={
                    "mode": "canonical_opening",
                    "parent_candidate_count": len(parents),
                },
            )
        except Exception as exc:
            logger.warning(
                "paper_reader.canonical_opening_context_failed",
                extra={
                    "paper_id": int(paper_id),
                    "user_id": int(user_id),
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )
            return _RagContextBuild(
                package=None,
                active_document=True,
                degradation_reasons=(
                    f"canonical_opening_context_failed:{type(exc).__name__}",
                ),
                retrieval_trace={
                    "mode": "canonical_opening",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                },
            )

    def _build_rag_context(
        self,
        *,
        paper: Any,
        paper_id: int,
        user_id: int,
        query: str,
        memory_context: list[dict[str, Any]] | str,
        fallback_context: str = "",
    ) -> str | None:
        """Compatibility facade for older callers and focused regression tests."""

        del fallback_context
        result = self._build_rag_context_package(
            paper=paper,
            paper_id=paper_id,
            user_id=user_id,
            query=query,
            memory_context=memory_context,
        )
        return result.package.text if result.package is not None else None

    def _schedule_pdf_excerpt(
        self,
        paper_id: int,
        ctx: str,
        background_tasks: BackgroundTasks,
        *,
        user_id: int,
    ) -> None:
        from .paper_reader_context import compute_and_cache_excerpt

        try:
            pdf_path = self._db.get_library_pdf_abspath(
                paper_id,
                user_id=int(user_id),
            )
            has_legacy_excerpt = (
                "【PDF 正文摘录" in ctx
                or "旧版 PDF 摘录" in ctx
            )
            if pdf_path and not has_legacy_excerpt:
                background_tasks.add_task(
                    compute_and_cache_excerpt,
                    self._db.db_path,
                    paper_id,
                    pdf_path,
                    user_id=int(user_id),
                )
        except Exception as exc:
            logger.debug("paper_reader.schedule_pdf_excerpt_failed", extra={"paper_id": paper_id}, exc_info=exc)

    async def _ensure_opening_turn_safe(
        self,
        *,
        user_id: int,
        paper_id: int,
        conversation_id: str | None,
        opening_text: str,
    ) -> None:
        from .paper_reader_history import ensure_opening_turn

        await run_in_threadpool(
            ensure_opening_turn,
            self._db.db_path,
            user_id=int(user_id),
            paper_id=int(paper_id),
            conversation_id=conversation_id,
            opening_text=opening_text,
        )

    async def _append_history(
        self,
        *,
        user_id: int,
        paper_id: int,
        conversation_id: str | None,
        user_message: str,
        reply: str,
        metadata: str | None = None,
    ) -> None:
        from .paper_reader_history import append_exchange

        await run_in_threadpool(
            append_exchange,
            self._db.db_path,
            user_id=int(user_id),
            paper_id=int(paper_id),
            conversation_id=conversation_id,
            user_message=user_message,
            assistant_reply=reply,
            assistant_metadata=metadata,
        )

    def _build_reader_snap(
        self,
        prepared: _ReaderPreparedContext,
        *,
        user_id: int,
        paper_id: int,
    ) -> dict[str, Any]:
        """Attach request-scoped evidence metadata to the tool snapshot.

        The registry is intentionally held only in the current request's snap;
        it is not written to SQLite and cannot bleed into another paper,
        user, or conversation.  An empty registry is still meaningful for a
        canonical request that passed a relevance guard with no evidence.
        """

        from .paper_reader_context import build_reader_snap

        canonical_mode = bool(prepared.document_version_id) or prepared.context_mode in {
            "canonical_rag",  # compatibility for focused pre-existing tests
            "hybrid_rag_v2",
            "canonical_opening_v2",
            "canonical_degraded",
        }
        if canonical_mode:
            # Do not pass legacy full text, cached pages, or a local PDF path
            # into a canonical request snapshot.  Canonical document tools use
            # the repository and the request's active version instead.
            reader_snap = build_reader_snap(prepared.paper)
        else:
            reader_snap = build_reader_snap(
                prepared.paper,
                pdf_text_for_references=prepared.pdf_ref_text,
                pdf_pages=prepared.pdf_pages,
            )
        reader_snap["_context_mode"] = prepared.context_mode
        reader_snap["_context_policy"] = prepared.package.policy_name
        reader_snap["_context_tokens"] = prepared.package.token_estimate
        if canonical_mode:
            reader_snap["_evidence_registry"] = EvidenceRegistry.from_context_package(
                prepared.package,
                user_id=int(user_id),
                paper_id=int(paper_id),
            )
            if prepared.document_version_id:
                reader_snap.update(
                    {
                        "_canonical_db_path": str(self._db.db_path),
                        "_canonical_user_id": int(user_id),
                        "_canonical_document_version_id": prepared.document_version_id,
                        "_tool_context_token_budget": _READER_TOOL_CONTEXT_TOKEN_RESERVE,
                    }
                )
        else:
            try:
                pdf_path = self._db.get_library_pdf_abspath(
                    paper_id,
                    user_id=int(user_id),
                )
                if pdf_path:
                    reader_snap["_pdf_abspath"] = pdf_path
            except Exception as exc:
                logger.debug(
                    "paper_reader.reader_snap_pdf_path_failed",
                    extra={"paper_id": int(paper_id), "error_type": type(exc).__name__},
                )
        return reader_snap

    async def get_opening(
        self,
        *,
        user_id: int,
        paper_id: int,
        conversation_id: str | None,
        background_tasks: BackgroundTasks,
        request_id: str | None = None,
    ) -> dict:
        from .reader_opening_cache import get_cached_opening, set_cached_opening
        from .paper_reader_history import ensure_conversation

        trace = ReaderRequestTrace(
            request_id=str(request_id or ""),
            operation="opening",
            user_id=int(user_id),
            paper_id=int(paper_id),
        )
        stage_started = time.perf_counter()
        conversation_id = await run_in_threadpool(
            ensure_conversation,
            self._db.db_path,
            user_id=int(user_id),
            paper_id=int(paper_id),
            conversation_id=conversation_id,
        )
        trace.measure("conversation", stage_started)

        stage_started = time.perf_counter()
        prepared = await self._build_reader_context(
            paper_id,
            user_id=int(user_id),
        )
        trace.measure("context", stage_started)
        trace.record_prepared(prepared)
        reader_snap = self._build_reader_snap(
            prepared,
            user_id=int(user_id),
            paper_id=int(paper_id),
        )
        reader_snap["_request_id"] = trace.request_id
        if prepared.context_mode == "legacy_fallback" and prepared.pdf_parsing:
            self._schedule_pdf_excerpt(
                paper_id,
                prepared.text,
                background_tasks,
                user_id=int(user_id),
            )

        # The legacy cache predates versioned canonical documents and is keyed
        # only by paper_id.  Never let a pre-ingest opening masquerade as the
        # current canonical version; a version-aware cache can be introduced
        # later with a migration.
        if prepared.context_mode != "legacy_fallback":
            cached, fresh = None, False
        else:
            cached, fresh = await run_in_threadpool(
                get_cached_opening,
                self._db.db_path,
                paper_id,
                user_id=int(user_id),
                max_age_hours=72,
            )
        if cached and fresh:
            op = cached.strip()
            await self._ensure_opening_turn_safe(
                user_id=int(user_id),
                paper_id=paper_id,
                conversation_id=conversation_id,
                opening_text=op,
            )
            trace.fields["opening_cache"] = "fresh"
            trace.emit(status="ok")
            return {
                "opening": op,
                "pdf_parsing": prepared.pdf_parsing,
                "conversation_id": conversation_id,
                "context_mode": prepared.context_mode,
                "degradation_flags": list(prepared.degradation_reasons),
            }

        if cached and not fresh:
            # Prevent concurrent refresh: use a process-level set of paper_ids being refreshed
            if not hasattr(self, '_refreshing_openings'):
                self._refreshing_openings: set[int] = set()
            if paper_id not in self._refreshing_openings:
                self._refreshing_openings.add(paper_id)
                def _refresh() -> None:
                    try:
                        opening2, _, _, _ = self._agent.paper_reader_reply(
                            prepared.text,
                            _NO_HISTORY_PLACEHOLDER,
                            _OPENING_PROMPT,
                            reader_snap,
                            context_is_packaged=True,
                        )
                        set_cached_opening(
                            self._db.db_path,
                            paper_id,
                            opening2.strip(),
                            user_id=int(user_id),
                        )
                    except Exception as exc:
                        logger.warning("paper_reader.opening_refresh_failed", extra={"paper_id": paper_id}, exc_info=exc)
                    finally:
                        self._refreshing_openings.discard(paper_id)

                background_tasks.add_task(_refresh)
            op = cached.strip()
            await self._ensure_opening_turn_safe(
                user_id=int(user_id),
                paper_id=paper_id,
                conversation_id=conversation_id,
                opening_text=op,
            )
            trace.fields["opening_cache"] = "stale_refresh_scheduled"
            trace.emit(status="ok")
            return {
                "opening": op,
                "pdf_parsing": prepared.pdf_parsing,
                "conversation_id": conversation_id,
                "context_mode": prepared.context_mode,
                "degradation_flags": list(prepared.degradation_reasons),
            }

        stage_started = time.perf_counter()
        opening, opening_related, _, opening_citations = await run_in_threadpool(
            lambda: self._agent.paper_reader_reply(
                prepared.text,
                _NO_HISTORY_PLACEHOLDER,
                _OPENING_PROMPT,
                reader_snap,
                context_is_packaged=True,
            )
        )
        trace.measure("agent", stage_started)
        trace.record_agent_result(
            reader_snap=reader_snap,
            citation_count=len(opening_citations or []),
            related_count=len(opening_related or []),
        )
        op = opening.strip()
        if prepared.context_mode == "legacy_fallback":
            await run_in_threadpool(
                set_cached_opening,
                self._db.db_path,
                paper_id,
                op,
                user_id=int(user_id),
            )
        await self._ensure_opening_turn_safe(
            user_id=int(user_id),
            paper_id=paper_id,
            conversation_id=conversation_id,
            opening_text=op,
        )
        trace.fields["opening_cache"] = "miss"
        trace.emit(status="ok")
        return {
            "opening": op,
            "pdf_parsing": prepared.pdf_parsing,
            "conversation_id": conversation_id,
            "context_mode": prepared.context_mode,
            "degradation_flags": list(prepared.degradation_reasons),
        }

    async def process_chat(
        self,
        *,
        user_id: int,
        paper_id: int,
        conversation_id: str | None,
        messages: list[Any],
        user_message: str,
        background_tasks: BackgroundTasks,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        from .paper_reader_history import ensure_conversation, list_turns

        trace = ReaderRequestTrace(
            request_id=str(request_id or ""),
            operation="chat",
            user_id=int(user_id),
            paper_id=int(paper_id),
        )
        user_message = self._validate_user_message(user_message)

        stage_started = time.perf_counter()
        conversation_id = await run_in_threadpool(
            ensure_conversation,
            self._db.db_path,
            user_id=int(user_id),
            paper_id=int(paper_id),
            conversation_id=conversation_id,
        )
        trace.measure("conversation", stage_started)
        # The API still accepts ``messages`` for front-end compatibility, but
        # it is no longer a prompt source.  Persisted, scoped turns are the
        # only factual conversation history and resist client-side injection.
        if messages:
            logger.debug(
                "paper_reader.client_history_ignored",
                extra={"paper_id": int(paper_id), "message_count": len(messages)},
            )
        if _is_explicit_memory_write_request(user_message):
            stage_started = time.perf_counter()
            await self._append_history(
                user_id=int(user_id),
                paper_id=paper_id,
                conversation_id=conversation_id,
                user_message=user_message,
                reply=_MANUAL_MEMORY_REPLY,
            )
            trace.measure("history_write", stage_started)
            trace.fields["memory_write_policy"] = "manual_confirmation_required"
            trace.emit(status="ok")
            return {
                "reply": _MANUAL_MEMORY_REPLY,
                "pdf_parsing": False,
                "related_papers": [],
                "related_hints": [],
                "kg_edges": [],
                "citations": [],
                "conversation_id": conversation_id,
                "context_mode": "manual_memory_guidance",
                "degradation_flags": [],
            }
        stage_started = time.perf_counter()
        server_turns = await run_in_threadpool(
            list_turns,
            self._db.db_path,
            user_id=int(user_id),
            paper_id=int(paper_id),
            conversation_id=conversation_id,
            limit=24,
        )
        trace.measure("history_read", stage_started)
        server_history = self._format_reader_history(server_turns)
        stage_started = time.perf_counter()
        prepared = await self._build_reader_context(
            paper_id,
            user_id=int(user_id),
            user_message=user_message,
            history_lines=server_history,
        )
        trace.measure("context", stage_started)
        trace.record_prepared(prepared)
        reader_snap = self._build_reader_snap(
            prepared,
            user_id=int(user_id),
            paper_id=int(paper_id),
        )
        reader_snap["_request_id"] = trace.request_id
        if prepared.context_mode == "legacy_fallback":
            self._schedule_pdf_excerpt(
                paper_id,
                prepared.text,
                background_tasks,
                user_id=int(user_id),
            )

        stage_started = time.perf_counter()
        reply, related_papers, related_sources, citations = await run_in_threadpool(
            lambda: self._agent.paper_reader_reply(
                prepared.text,
                server_history,
                user_message,
                reader_snap,
                context_is_packaged=True,
            )
        )
        trace.measure("agent", stage_started)
        trace.record_agent_result(
            reader_snap=reader_snap,
            citation_count=len(citations or []),
            related_count=len(related_papers or []),
        )
        rs = list(related_sources or [])
        related_hints: list[dict[str, Any]] = [
            {
                "ref_idx": i,
                "title": getattr(p, "title", None),
                "reason": (
                    "来自当前文献参考文献题录（OpenAlex 解析）"
                    if i - 1 < len(rs) and rs[i - 1] == READER_RELATED_FROM_BIBLIOGRAPHY
                    else "基于论文主题相似度匹配"
                    if i - 1 < len(rs) and rs[i - 1] == READER_RELATED_FROM_PRE_SEARCH
                    else "来自用户给定英文短语或外部题名检索（OpenAlex）"
                ),
            }
            for i, p in enumerate(related_papers or [], start=1)
        ]

        # Serialize metadata for history (related papers count + citations count)
        import json as _json
        _meta = _json.dumps({
            "related_count": len(related_papers or []),
            "citation_count": len(citations or []),
        })
        stage_started = time.perf_counter()
        await self._append_history(
            user_id=int(user_id),
            paper_id=paper_id,
            conversation_id=conversation_id,
            user_message=user_message,
            reply=reply,
            metadata=_meta,
        )
        trace.measure("history_write", stage_started)

        trace.emit(status="ok")
        return {
            "reply": reply.strip(),
            "pdf_parsing": prepared.pdf_parsing,
            "related_papers": related_papers,
            "related_hints": related_hints,
            "kg_edges": [],
            "citations": citations or [],
            "conversation_id": conversation_id,
            "context_mode": prepared.context_mode,
            "degradation_flags": list(prepared.degradation_reasons),
        }

    async def get_history(
        self,
        *,
        paper_id: int,
        limit: int,
        user_id: int,
        conversation_id: str | None,
    ) -> list[dict[str, Any]]:
        from .paper_reader_history import list_turns

        paper = await run_in_threadpool(
            self._db.get_paper_by_id,
            int(paper_id),
            user_id=int(user_id),
        )
        if not paper:
            raise HTTPException(status_code=404, detail="文献不存在")
        return cast(
            list[dict[str, Any]],
            await run_in_threadpool(
                list_turns,
                self._db.db_path,
                user_id=int(user_id),
                paper_id=int(paper_id),
                conversation_id=conversation_id,
                limit=int(limit),
            ),
        )

from __future__ import annotations

from types import SimpleNamespace

from app.repositories.document_repository import DocumentRepository
from app.services.reader.paper_reader_service import PaperReaderService
from app.services.retrieval.hybrid import HybridChunkRetriever, HybridRetrievalResult


def test_low_relevance_rag_does_not_fall_back_to_full_paper_excerpt(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        DocumentRepository,
        "get_active_version",
        lambda self, *, user_id, paper_id: {"id": "active-version"},
    )

    def _no_relevant_hits(self, **kwargs):
        return HybridRetrievalResult(
            query=str(kwargs["query"]),
            hits=[],
            degradation_reasons=["low_rerank_candidates_filtered"],
        )

    monkeypatch.setattr(HybridChunkRetriever, "retrieve", _no_relevant_hits)
    db = SimpleNamespace(db_path=str(tmp_path / "papers.db"))
    service = PaperReaderService(db=db, agent=object())
    paper = SimpleNamespace(
        title="A Paper",
        authors=[],
        year=2025,
        journal="Test",
        doi="",
        abstract="A short abstract.",
        keywords=[],
    )
    context = service._build_rag_context(
        paper=paper,
        paper_id=1,
        user_id=1,
        query="水的沸点是多少？",
        memory_context="",
        fallback_context="LEGACY_FULL_PAPER_EXCERPT",
    )
    assert context
    assert "LEGACY_FULL_PAPER_EXCERPT" not in context
    assert "没有达到相关性阈值" in context

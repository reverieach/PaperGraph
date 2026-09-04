from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from app.domain.document import CanonicalDocument, DocumentChunk, DocumentPage, ParseQualityReport
from app.infrastructure.db import Database, run_migrations
from app.infrastructure.vector.lancedb_store import LanceDBVectorStore, VectorRecord
from app.repositories.document_repository import DocumentRepository
from app.services.embedding.base import EmbeddingBatch, EmbeddingUnavailable, validate_vectors
from app.services.embedding.dashscope_embedding import DashScopeEmbeddingProvider
from app.services.embedding.indexer import DocumentEmbeddingIndexer
from app.services.retrieval.hybrid import HybridChunkRetriever, build_fts_query
from app.services.rerank.base import RerankResult


def test_embedding_validation_rejects_bad_shape_and_nonfinite_values() -> None:
    valid = validate_vectors([[1.0, 0.2]], expected_count=1, expected_dimension=2)
    assert valid == [[1.0, 0.2]]
    with pytest.raises(EmbeddingUnavailable):
        validate_vectors([[1.0]], expected_count=1, expected_dimension=2)
    with pytest.raises(EmbeddingUnavailable):
        validate_vectors([[math.inf, 0.0]], expected_count=1, expected_dimension=2)
    with pytest.raises(EmbeddingUnavailable):
        validate_vectors([[0.0, 0.0]], expected_count=1, expected_dimension=2)


def test_dashscope_embedding_exposes_separate_document_and_query_paths() -> None:
    captured: list[list[str]] = []

    class _Embeddings:
        def create(self, **kwargs):
            captured.append(list(kwargs["input"]))
            return {
                "data": [
                    {"index": index, "embedding": [1.0, 0.1]}
                    for index, _ in enumerate(kwargs["input"])
                ],
                "usage": {"total_tokens": 1},
            }

    class _Client:
        embeddings = _Embeddings()

    provider = DashScopeEmbeddingProvider(
        api_key="secret",
        base_url="https://example.invalid/v1",
        dimension=2,
        document_instruction="document retrieval",
        query_instruction="query retrieval",
    )
    provider._client = _Client()
    assert len(provider.embed_documents(["paper passage"]).vectors) == 1
    assert len(provider.embed_query("reader question").vectors) == 1
    assert captured == [
        ["document retrieval\n\npaper passage"],
        ["query retrieval\n\nreader question"],
    ]


def test_lancedb_vector_store_upsert_search_and_scope_filter(tmp_path) -> None:
    store = LanceDBVectorStore(str(tmp_path / "vectors"), dimension=3)
    store.upsert(
        [
            VectorRecord("c1", 1, 10, "v1", "paragraph", [1.0, 0.0, 0.0]),
            VectorRecord("c2", 1, 11, "v2", "paragraph", [0.0, 1.0, 0.0]),
            VectorRecord("c3", 2, 10, "v3", "paragraph", [1.0, 0.0, 0.0]),
        ]
    )
    assert store.count() == 3
    hits = store.search([1.0, 0.0, 0.0], user_id=1, paper_ids=[10], limit=5)
    assert [hit.chunk_uid for hit in hits] == ["c1"]
    assert hits[0].score > 0.9
    assert store.search([1.0, 0.0, 0.0], user_id=2, paper_ids=[10], limit=5)[0].chunk_uid == "c3"
    store.upsert([VectorRecord("c1", 1, 10, "v1", "paragraph", [0.0, 0.0, 1.0])])
    assert store.count() == 3
    assert store.search([0.0, 0.0, 1.0], user_id=1, paper_ids=[10], limit=1)[0].chunk_uid == "c1"


@dataclass
class _FakeEmbeddingProvider:
    provider: str = "fake"
    model: str = "fake-v1"
    dimension: int = 3

    def embed_texts(self, texts: list[str]) -> EmbeddingBatch:
        vectors = []
        for text in texts:
            marker = text.lower()
            vectors.append(
                [
                    1.0 if "retrieval" in marker else 0.1,
                    1.0 if "memory" in marker else 0.1,
                    1.0 if "pdf" in marker else 0.1,
                ]
            )
        return EmbeddingBatch(vectors, self.model, self.dimension, {})


@dataclass
class _FakeReranker:
    model: str = "fake-reranker"

    def rerank(self, query: str, documents: list[str], *, top_n: int | None = None) -> list[RerankResult]:
        # Put the memory chunk first to prove that the precision stage, not
        # lexical order, controls the final candidate ordering.
        ranked = sorted(
            range(len(documents)),
            key=lambda index: "memory" in documents[index].lower(),
            reverse=True,
        )
        return [
            RerankResult(index=index, score=0.99 if rank == 0 else 0.10)
            for rank, index in enumerate(ranked)
        ]


@dataclass
class _FailingEmbeddingProvider(_FakeEmbeddingProvider):
    def embed_texts(self, texts: list[str]) -> EmbeddingBatch:
        raise EmbeddingUnavailable("simulated provider outage")


def _seed_document(db_path: str) -> tuple[DocumentRepository, str]:
    run_migrations(db_path)
    now = 1700000000
    with Database(db_path).transaction() as conn:
        conn.execute(
            "INSERT INTO auth_users(id,username,password_hash,status,created_at,updated_at) VALUES(1,'u','x','active',?,?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO papers(id,user_id,title,created_at,updated_at) VALUES(10,1,'RAG paper',?,?)",
            (now, now),
        )
    repository = DocumentRepository(db_path)
    version_id = repository.create_or_get_version(
        user_id=1,
        paper_id=10,
        file_hash="hash",
        file_size=12,
        parser_id="test",
        parser_version="1",
        parser_config_hash="config",
        chunker_version="chunk-v1",
    )
    document = CanonicalDocument(
        document_version_id=version_id,
        user_id=1,
        paper_id=10,
        file_hash="hash",
        parser_id="test",
        parser_version="1",
        pages=[DocumentPage(page_index=1, text="retrieval memory pdf")],
        quality=ParseQualityReport(
            page_count=1, non_empty_page_count=1, block_count=2,
            text_char_count=40, pages_with_provenance=1, score=1.0,
        ),
    )
    chunks = [
        DocumentChunk(
            chunk_uid="chunk-a", document_version_id=version_id, user_id=1,
            paper_id=10, parent_chunk_uid=None, level="child", ordinal=0,
            content_type="paragraph", section_path=["Method"], page_start=1,
            page_end=1, block_uids=[], display_text="retrieval and pdf",
            embedding_text="retrieval and pdf", sparse_text="retrieval and pdf",
            text_hash="a", token_count=4, chunker_version="chunk-v1",
        ),
        DocumentChunk(
            chunk_uid="chunk-b", document_version_id=version_id, user_id=1,
            paper_id=10, parent_chunk_uid=None, level="child", ordinal=1,
            content_type="paragraph", section_path=["Memory"], page_start=1,
            page_end=1, block_uids=[], display_text="memory design",
            embedding_text="memory design", sparse_text="memory design",
            text_hash="b", token_count=3, chunker_version="chunk-v1",
        ),
    ]
    repository.persist_document(document, chunks)
    return repository, version_id


def test_embedding_indexer_projects_staging_chunks_and_is_idempotent(tmp_path) -> None:
    db_path = str(tmp_path / "papers.db")
    repository, version_id = _seed_document(db_path)
    store = LanceDBVectorStore(str(tmp_path / "vectors"), dimension=3)
    indexer = DocumentEmbeddingIndexer(
        repository, _FakeEmbeddingProvider(), store, batch_size=1
    )

    report = indexer.index_version(user_id=1, paper_id=10, document_version_id=version_id)
    assert report.requested_chunks == report.indexed_chunks == 2
    assert store.count_version(version_id) == 2
    version = repository.get_version(user_id=1, document_version_id=version_id)
    assert version["embedding_status"] == "ready"
    assert isinstance(version["embedding_config_hash"], str)
    assert len(version["embedding_config_hash"]) == 64
    # A retry replaces, rather than duplicates, the version projection.
    report_retry = indexer.index_version(user_id=1, paper_id=10, document_version_id=version_id)
    assert report_retry.indexed_chunks == 2
    assert store.count() == 2
    assert store.search([1.0, 0.0, 1.0], user_id=1, paper_ids=[10], limit=2)[0].chunk_uid == "chunk-a"


def test_hybrid_retrieval_fuses_bm25_and_vector_and_keeps_sparse_fallback(tmp_path) -> None:
    assert build_fts_query("retrieval: memory?") == '"retrieval" OR "memory"'
    assert build_fts_query("强化学习") == '"强 化 学 习"'
    db_path = str(tmp_path / "papers.db")
    repository, version_id = _seed_document(db_path)
    assert repository.activate_version(
        user_id=1, paper_id=10, document_version_id=version_id
    )
    store = LanceDBVectorStore(str(tmp_path / "vectors"), dimension=3)
    provider = _FakeEmbeddingProvider()
    DocumentEmbeddingIndexer(repository, provider, store).index_version(
        user_id=1, paper_id=10, document_version_id=version_id
    )

    result = HybridChunkRetriever(
        repository, vector_store=store, embedding_provider=provider
    ).retrieve(user_id=1, paper_ids=[10], query="retrieval", limit=2)
    assert result.hits
    assert result.hits[0].chunk_uid == "chunk-a"
    assert set(result.hits[0].sources) == {"bm25", "vector"}
    assert not result.degraded

    sparse_only = HybridChunkRetriever(repository).retrieve(
        user_id=1, paper_ids=[10], query="retrieval", limit=2
    )
    assert sparse_only.hits[0].chunk_uid == "chunk-a"
    assert sparse_only.degraded
    assert "dense_retrieval_not_configured" in sparse_only.degradation_reasons

    reranked = HybridChunkRetriever(
        repository,
        vector_store=store,
        embedding_provider=provider,
        reranker=_FakeReranker(),
    ).retrieve(user_id=1, paper_ids=[10], query="retrieval memory", limit=2)
    assert reranked.hits[0].chunk_uid == "chunk-b"
    assert reranked.hits[0].rerank_score == pytest.approx(0.99)
    assert "rerank" in reranked.hits[0].sources
    assert not reranked.degraded

    repository.set_embedding_status(
        user_id=1,
        document_version_id=version_id,
        status="ready",
        indexed_count=2,
        provider="fake",
        model="different-model",
        dimension=3,
    )
    mismatched = HybridChunkRetriever(
        repository,
        vector_store=store,
        embedding_provider=provider,
    ).retrieve(user_id=1, paper_ids=[10], query="retrieval", limit=2)
    assert mismatched.hits and mismatched.hits[0].sources == ("bm25",)
    assert mismatched.dense_count == 0
    assert "dense_index_not_ready_or_mismatched" in mismatched.degradation_reasons

    # The same chunk UID must not leak to another user even when LanceDB has a
    # row for it; SQLite hydration is the final ownership gate.
    assert not HybridChunkRetriever(
        repository, vector_store=store, embedding_provider=provider
    ).retrieve(user_id=2, paper_ids=[10], query="retrieval").hits


def test_embedding_indexer_persists_failed_projection_and_cleans_partial_rows(tmp_path) -> None:
    db_path = str(tmp_path / "papers.db")
    repository, version_id = _seed_document(db_path)
    store = LanceDBVectorStore(str(tmp_path / "vectors"), dimension=3)
    indexer = DocumentEmbeddingIndexer(
        repository, _FailingEmbeddingProvider(), store, batch_size=1
    )
    with pytest.raises(EmbeddingUnavailable, match="simulated provider outage"):
        indexer.index_version(user_id=1, paper_id=10, document_version_id=version_id)
    version = repository.get_version(user_id=1, document_version_id=version_id)
    assert version["embedding_status"] == "failed"
    assert store.count_version(version_id) == 0

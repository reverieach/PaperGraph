"""Build the rebuildable dense index from canonical child chunks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...infrastructure.vector.lancedb_store import LanceDBVectorStore, VectorRecord
from ...repositories.document_repository import DocumentRepository
from .base import (
    EmbeddingProvider,
    EmbeddingUnavailable,
    embedding_document_config_hash,
    embed_documents,
    validate_vectors,
)


@dataclass(slots=True)
class EmbeddingIndexReport:
    document_version_id: str
    requested_chunks: int
    indexed_chunks: int
    provider: str
    model: str
    dimension: int
    config_hash: str
    degraded: bool = False
    error: str | None = None


class DocumentEmbeddingIndexer:
    """Project SQLite child chunks into a scoped LanceDB vector table.

    SQLite remains authoritative.  A version is deleted and rebuilt as one
    projection operation, so retrying a job cannot leave duplicate vectors.
    On failure the partial projection is removed and the caller can keep
    BM25/FTS available as a documented degraded mode.
    """

    def __init__(
        self,
        repository: DocumentRepository,
        provider: EmbeddingProvider,
        vector_store: LanceDBVectorStore,
        *,
        batch_size: int = 10,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.vector_store = vector_store
        self.batch_size = max(1, min(64, int(batch_size)))

    def index_version(
        self,
        *,
        user_id: int,
        paper_id: int,
        document_version_id: str,
    ) -> EmbeddingIndexReport:
        version = self.repository.get_version(
            user_id=int(user_id), document_version_id=document_version_id
        )
        if version is None or int(version.get("paper_id") or 0) != int(paper_id):
            raise ValueError("document version does not belong to user/paper")
        chunks = self.repository.list_version_chunks(
            user_id=int(user_id),
            paper_id=int(paper_id),
            document_version_id=document_version_id,
            level="child",
        )
        if not chunks:
            raise EmbeddingUnavailable("document version has no child chunks")

        provider_name = str(getattr(self.provider, "provider", "embedding"))
        model = str(getattr(self.provider, "model", "unknown"))
        dimension = int(getattr(self.provider, "dimension", 0))
        config_hash = embedding_document_config_hash(self.provider)
        index_version = f"{provider_name}:{model}:{dimension}:{config_hash[:12]}"
        self.repository.set_embedding_status(
            user_id=int(user_id),
            document_version_id=document_version_id,
            status="running",
            indexed_count=0,
            error=None,
            provider=provider_name,
            model=model,
            dimension=dimension,
            config_hash=config_hash,
        )
        indexed = 0
        try:
            self.vector_store.delete_version(document_version_id)
            for start in range(0, len(chunks), self.batch_size):
                batch = chunks[start : start + self.batch_size]
                texts = [str(row.get("embedding_text") or row.get("display_text") or "").strip() for row in batch]
                if any(not text for text in texts):
                    raise EmbeddingUnavailable("a child chunk has empty embedding text")
                result = embed_documents(self.provider, texts)
                vectors = validate_vectors(
                    result.vectors,
                    expected_count=len(batch),
                    expected_dimension=dimension,
                )
                records = [
                    VectorRecord(
                        chunk_uid=str(row["chunk_uid"]),
                        user_id=int(user_id),
                        paper_id=int(paper_id),
                        document_version_id=document_version_id,
                        content_type=str(row.get("content_type") or "paragraph"),
                        vector=vector,
                        index_version=index_version,
                    )
                    for row, vector in zip(batch, vectors, strict=True)
                ]
                indexed += self.vector_store.upsert(records)
            expected = len(chunks)
            actual = self.vector_store.count_version(document_version_id)
            if actual != expected:
                raise EmbeddingUnavailable(
                    f"vector projection count mismatch: expected {expected}, got {actual}"
                )
            self.repository.set_embedding_status(
                user_id=int(user_id),
                document_version_id=document_version_id,
                status="ready",
                indexed_count=indexed,
                error=None,
                provider=provider_name,
                model=model,
                dimension=dimension,
                config_hash=config_hash,
            )
        except Exception as exc:
            self.repository.set_embedding_status(
                user_id=int(user_id),
                document_version_id=document_version_id,
                status="failed",
                indexed_count=0,
                error=f"{type(exc).__name__}: {exc}"[:1000],
                provider=provider_name,
                model=model,
                dimension=dimension,
                config_hash=config_hash,
            )
            # A failed projection must never be mistaken for a complete index.
            try:
                self.vector_store.delete_version(document_version_id)
            except Exception:
                pass
            raise
        return EmbeddingIndexReport(
            document_version_id=document_version_id,
            requested_chunks=len(chunks),
            indexed_chunks=indexed,
            provider=provider_name,
            model=model,
            dimension=dimension,
            config_hash=config_hash,
        )

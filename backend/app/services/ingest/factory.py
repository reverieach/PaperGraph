"""Production construction of the document ingestion worker."""

from __future__ import annotations

from pathlib import Path

from ...infrastructure.vector.lancedb_store import LanceDBVectorStore
from ...repositories.document_repository import DocumentRepository
from ...settings import get_settings
from ..embedding.dashscope_embedding import DashScopeEmbeddingProvider
from ..embedding.indexer import DocumentEmbeddingIndexer
from .service import IngestService
from .worker import IngestWorker


def _resolve(path_value: str, fallback: Path) -> str:
    if not path_value.strip():
        return str(fallback)
    candidate = Path(path_value).expanduser()
    return str(candidate if candidate.is_absolute() else fallback.parent / candidate)


def resolve_rag_storage_paths(db_path: str) -> tuple[str, str]:
    """Resolve artifact/vector roots consistently for ingest and retrieval."""

    settings = get_settings()
    data_dir = Path(db_path).expanduser().resolve().parent
    return (
        _resolve(settings.rag_artifacts_dir, data_dir / "rag_artifacts"),
        _resolve(settings.rag_vectors_dir, data_dir / "rag_vectors"),
    )


def build_ingest_service(db_path: str) -> IngestService:
    settings = get_settings()
    # Resolve defaults beside the supplied database, not beside the process
    # cwd/global settings.  This keeps isolated test databases and future
    # per-tenant deployments from sharing vector/artifact projections.
    artifacts_root, vectors_root = resolve_rag_storage_paths(db_path)
    indexer = None
    if settings.rag_embedding_enabled:
        provider = DashScopeEmbeddingProvider()
        if provider.api_key and provider.base_url:
            indexer = DocumentEmbeddingIndexer(
                DocumentRepository(db_path),
                provider,
                LanceDBVectorStore(vectors_root, dimension=provider.dimension),
            )
    return IngestService(
        db_path,
        artifacts_root=artifacts_root,
        docling_artifacts_path=(settings.rag_docling_artifacts_path or None),
        docling_staging_root=(settings.rag_docling_staging_dir or None),
        docling_ocr_mode=settings.rag_docling_ocr_mode,
        device=settings.rag_device,
        embedding_indexer=indexer,
    )


def build_ingest_worker(db_path: str) -> IngestWorker:
    return IngestWorker(db_path, service=build_ingest_service(db_path))


__all__ = [
    "build_ingest_service",
    "build_ingest_worker",
    "resolve_rag_storage_paths",
]

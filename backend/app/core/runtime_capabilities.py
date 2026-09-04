"""Side-effect-free runtime capability probes for operators and tests."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import sqlite3
from typing import Any

from ..services.embedding.dashscope_embedding import DashScopeEmbeddingProvider
from ..services.rerank.dashscope_reranker import DashScopeReranker
from ..settings import Settings, get_settings


def _distribution_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _package_capability(module: str, distribution: str | None = None) -> dict[str, Any]:
    available = importlib.util.find_spec(module) is not None
    return {
        "available": available,
        "version": _distribution_version(distribution or module) if available else None,
    }


def _sqlite_capabilities() -> dict[str, Any]:
    """Probe only an in-memory database; never mutate the application DB."""

    result: dict[str, Any] = {
        "version": sqlite3.sqlite_version,
        "fts5": False,
        "trigram": False,
    }
    conn = sqlite3.connect(":memory:")
    try:
        try:
            conn.execute("CREATE VIRTUAL TABLE probe_fts USING fts5(content)")
            result["fts5"] = True
        except sqlite3.OperationalError:
            return result
        try:
            conn.execute("CREATE VIRTUAL TABLE probe_trigram USING fts5(content, tokenize='trigram')")
            result["trigram"] = True
        except sqlite3.OperationalError:
            pass
    finally:
        conn.close()
    return result


def collect_runtime_capabilities(
    *,
    settings: Settings | None = None,
    embedded_worker_alive: bool | None = None,
) -> dict[str, Any]:
    """Return safe configuration/availability facts, never credentials or paths."""

    effective_settings = settings or get_settings()
    embedding = DashScopeEmbeddingProvider()
    reranker = DashScopeReranker()
    return {
        "sqlite": _sqlite_capabilities(),
        "packages": {
            "docling": _package_capability("docling"),
            "lancedb": _package_capability("lancedb"),
            "tiktoken": _package_capability("tiktoken"),
            "pyarrow": _package_capability("pyarrow"),
            "rapidocr": _package_capability("rapidocr"),
            "onnxruntime": _package_capability("onnxruntime"),
            "torch": _package_capability("torch"),
        },
        "embedding": {
            "enabled": bool(effective_settings.rag_embedding_enabled),
            "configured": bool(embedding.api_key and embedding.base_url),
            "model": embedding.model,
            "dimension": embedding.dimension,
        },
        "rerank": {
            "enabled": bool(effective_settings.rag_rerank_enabled),
            "configured": bool(reranker.api_key and reranker.endpoint),
            "model": reranker.model,
        },
        "ingest_worker": {
            "embedded_local_dev_enabled": bool(effective_settings.rag_ingest_worker_enabled),
            "embedded_local_dev_alive": embedded_worker_alive,
            "external_worker_required": not bool(effective_settings.rag_ingest_worker_enabled),
        },
    }


__all__ = ["collect_runtime_capabilities"]

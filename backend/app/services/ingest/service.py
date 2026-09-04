"""Deterministic PDF -> canonical document -> chunks ingestion workflow."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...domain.document import CanonicalDocument, stable_hash
from ...repositories.document_repository import DocumentRepository
from ..embedding.indexer import DocumentEmbeddingIndexer, EmbeddingIndexReport
from .chunking import CHUNKER_VERSION, HierarchicalChunker
from .parsers import (
    PARSER_ADAPTER_VERSION,
    DoclingParser,
    ParseResult,
    PyMuPDFParser,
    file_sha256,
)
from .quality import ParseQualityGate, QualityGateResult

logger = logging.getLogger(__name__)

# Retry is useful for process, network, or SQLite persistence faults.  It is
# not useful for an unchanged input that cannot be opened, is encrypted, or
# consistently fails the configured quality gate.  Keep this policy based on
# explicit codes rather than brittle text matching so the Worker and UI can
# explain the same outcome deterministically.
PERMANENT_INGEST_ERROR_CODES = frozenset(
    {
        "PDF_FILE_MISSING",
        "PDF_HASH_FAILED",
        "PDF_ENCRYPTED",
        "PDF_INVALID",
        "QUALITY_GATE_FAILED",
    }
)
TERMINAL_PARSER_ERROR_CODES = frozenset({"PDF_ENCRYPTED", "PDF_INVALID"})


def is_retryable_ingest_error(error_code: str | None) -> bool:
    """Whether the durable Worker should spend another attempt on this code."""

    return str(error_code or "").strip().upper() not in PERMANENT_INGEST_ERROR_CODES


@dataclass(slots=True)
class IngestReport:
    status: str
    paper_id: int
    user_id: int
    document_version_id: str | None = None
    job_id: str | None = None
    parser_id: str | None = None
    parser_version: str | None = None
    page_count: int = 0
    block_count: int = 0
    chunk_count: int = 0
    parent_count: int = 0
    child_count: int = 0
    embedding_indexed_count: int = 0
    embedding_model: str | None = None
    embedding_error: str | None = None
    quality_score: float = 0.0
    flags: list[str] = field(default_factory=list)
    error: str | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "paper_id": self.paper_id,
            "user_id": self.user_id,
            "document_version_id": self.document_version_id,
            "job_id": self.job_id,
            "parser_id": self.parser_id,
            "parser_version": self.parser_version,
            "page_count": self.page_count,
            "block_count": self.block_count,
            "chunk_count": self.chunk_count,
            "parent_count": self.parent_count,
            "child_count": self.child_count,
            "embedding_indexed_count": self.embedding_indexed_count,
            "embedding_model": self.embedding_model,
            "embedding_error": self.embedding_error,
            "quality_score": self.quality_score,
            "flags": list(self.flags),
            "error": self.error,
            "error_code": self.error_code,
        }


class IngestService:
    """Run one idempotent ingestion without holding a DB transaction."""

    def __init__(
        self,
        db_path: str,
        *,
        artifacts_root: str | None = None,
        docling_artifacts_path: str | None = None,
        docling_staging_root: str | None = None,
        docling_ocr_mode: str = "auto",
        device: str = "auto",
        gate: ParseQualityGate | None = None,
        chunker: HierarchicalChunker | None = None,
        embedding_indexer: DocumentEmbeddingIndexer | None = None,
    ) -> None:
        self.db_path = str(db_path)
        root = Path(artifacts_root or (Path(db_path).parent / "rag_artifacts"))
        self.artifacts_root = root
        self.docling_artifacts_path = docling_artifacts_path
        self.docling_staging_root = docling_staging_root
        self.docling_ocr_mode = str(docling_ocr_mode or "auto").strip().lower()
        if self.docling_ocr_mode not in {"auto", "always", "never"}:
            raise ValueError("docling_ocr_mode must be auto, always, or never")
        self.device = device
        self.gate = gate or ParseQualityGate()
        self.chunker = chunker or HierarchicalChunker()
        self.repository = DocumentRepository(self.db_path)
        self.embedding_indexer = embedding_indexer

    def ingest_pdf(
        self,
        *,
        user_id: int,
        paper_id: int,
        pdf_path: str,
        paper_title: str = "",
        parser_mode: str = "standard",
        job_id: str | None = None,
        worker_id: str | None = None,
        finalize_job: bool = True,
    ) -> IngestReport:
        user_id, paper_id = int(user_id), int(paper_id)
        if not os.path.isfile(pdf_path):
            report = IngestReport(
                "failed",
                paper_id,
                user_id,
                job_id=job_id,
                error="PDF file does not exist",
                error_code="PDF_FILE_MISSING",
            )
            self._job_progress(
                job_id,
                "failed",
                1.0,
                status="failed",
                error=report.error,
                error_code=report.error_code,
                worker_id=worker_id,
                finalize=finalize_job,
            )
            return report
        if parser_mode not in {"standard", "fallback", "auto"}:
            raise ValueError("parser_mode must be standard, fallback, or auto")
        try:
            file_hash, file_size = file_sha256(pdf_path)
        except Exception as exc:
            report = IngestReport(
                "failed",
                paper_id,
                user_id,
                job_id=job_id,
                error=str(exc),
                error_code="PDF_HASH_FAILED",
            )
            self._job_progress(
                job_id,
                "failed",
                1.0,
                status="failed",
                error=report.error,
                error_code=report.error_code,
                worker_id=worker_id,
                finalize=finalize_job,
            )
            return report

        primary = DoclingParser(
            artifacts_path=self.docling_artifacts_path,
            staging_root=self.docling_staging_root,
            device=self.device,
            ocr_mode=self.docling_ocr_mode,
            do_table_structure=True,
        )
        fallback = PyMuPDFParser()
        config_hash = stable_hash(
            {
                "device": self.device,
                "ocr_mode": self.docling_ocr_mode,
                "do_table_structure": True,
                "max_num_pages": None,
                # Parsing behavior is part of canonical identity.  Bump this
                # when adapter semantics change so previously misclassified
                # documents are rebuilt instead of silently reused.
                "adapter_revision": PARSER_ADAPTER_VERSION,
            }
        )
        embedding_provider_name = None
        embedding_model = None
        embedding_dimension = None
        if self.embedding_indexer is not None:
            provider = self.embedding_indexer.provider
            embedding_provider_name = str(getattr(provider, "provider", "embedding"))
            embedding_model = str(getattr(provider, "model", "")) or None
            embedding_dimension = int(getattr(provider, "dimension", 0) or 0) or None
        candidates: list[DoclingParser | PyMuPDFParser] = (
            [fallback] if parser_mode == "fallback" else [primary, fallback]
        )
        flags: list[str] = []
        last_error: str | None = None
        last_error_code: str | None = None
        for index, parser in enumerate(candidates):
            version_id = self.repository.create_or_get_version(
                user_id=user_id,
                paper_id=paper_id,
                file_hash=file_hash,
                file_size=file_size,
                parser_id=parser.parser_id,
                parser_version=parser.parser_version,
                parser_config_hash=config_hash,
                chunker_version=self.chunker.config.version,
                embedding_provider=embedding_provider_name,
                embedding_model=embedding_model,
                embedding_dimension=embedding_dimension,
            )
            existing = self.repository.get_version(
                user_id=user_id,
                document_version_id=version_id,
            )
            if self._is_reusable_version(existing):
                return self._reuse_version(
                    existing or {},
                    user_id=user_id,
                    paper_id=paper_id,
                    job_id=job_id,
                    worker_id=worker_id,
                    finalize_job=finalize_job,
                )
            self._job_progress(
                job_id,
                "parsing",
                0.2 + index * 0.15,
                worker_id=worker_id,
            )
            parsed = parser.parse(
                pdf_path,
                document_version_id=version_id,
                user_id=user_id,
                paper_id=paper_id,
                file_hash=file_hash,
            )
            if parsed.document is None:
                last_error = parsed.error or f"{parser.parser_id} returned no document"
                last_error_code = parsed.error_code or last_error_code
                self.repository.set_version_status(
                    user_id=user_id,
                    document_version_id=version_id,
                    status="failed",
                    error_code=parsed.error_code or "PARSER_FAILED",
                    error_message=last_error,
                )
                terminal_failure = str(parsed.error_code or "").upper() in TERMINAL_PARSER_ERROR_CODES
                if parser is primary and not terminal_failure:
                    flags.append("docling_failed_fallback_pymupdf")
                if terminal_failure:
                    flags.append("terminal_pdf_input_error")
                    break
                continue
            gate = self.gate.evaluate(parsed.document)
            flags.extend(parsed.flags)
            flags.extend(gate.flags)
            if not gate.accepted:
                last_error = ";".join(gate.flags) or "parse quality gate failed"
                self.repository.set_version_status(
                    user_id=user_id,
                    document_version_id=version_id,
                    status="failed",
                    error_code="QUALITY_GATE_FAILED",
                    error_message=last_error,
                )
                if parser is primary:
                    flags.append("docling_quality_gate_fallback_pymupdf")
                continue
            if parser is fallback:
                flags.append("pymupdf_fallback")
            if gate.status == "degraded":
                flags.append("quality_degraded")
            return self._persist_ready(
                parsed,
                gate,
                file_size=file_size,
                paper_title=paper_title,
                user_id=user_id,
                paper_id=paper_id,
                job_id=job_id,
                worker_id=worker_id,
                finalize_job=finalize_job,
                flags=flags,
                version_id=version_id,
            )
        report = IngestReport(
            "failed",
            paper_id,
            user_id,
            job_id=job_id,
            flags=sorted(set(flags)),
            error=last_error or "all parsers failed",
            error_code=last_error_code or "PARSER_FAILED",
        )
        self._job_progress(
            job_id,
            "failed",
            1.0,
            status="failed",
            error=report.error,
            error_code=report.error_code,
            worker_id=worker_id,
            finalize=finalize_job,
        )
        return report

    @staticmethod
    def _is_reusable_version(version: dict[str, Any] | None) -> bool:
        if not version:
            return False
        return (
            str(version.get("status") or "") in {
                "ready",
                "degraded",
                "active",
                "superseded",
            }
            and int(version.get("chunk_count") or 0) > 0
            and int(version.get("page_count") or 0) > 0
        )

    @staticmethod
    def _stored_quality_flags(version: dict[str, Any]) -> list[str]:
        try:
            payload = json.loads(str(version.get("quality_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        values = payload.get("flags") if isinstance(payload, dict) else []
        return sorted({str(value) for value in values or [] if str(value).strip()})

    def _reuse_version(
        self,
        version: dict[str, Any],
        *,
        user_id: int,
        paper_id: int,
        job_id: str | None,
        worker_id: str | None,
        finalize_job: bool,
    ) -> IngestReport:
        """Reuse an immutable canonical version and repair its projection."""

        version_id = str(version["id"])
        flags = self._stored_quality_flags(version)
        embedding_report: EmbeddingIndexReport | None = None
        embedding_error: str | None = None
        child_count = len(
            self.repository.list_version_chunks(
                user_id=user_id,
                paper_id=paper_id,
                document_version_id=version_id,
                level="child",
            )
        )
        parent_count = max(0, int(version.get("chunk_count") or 0) - child_count)
        self._job_progress(
            job_id,
            "reusing_canonical_version",
            0.75,
            worker_id=worker_id,
        )

        if self.embedding_indexer is not None:
            provider = self.embedding_indexer.provider
            expected_provider = str(getattr(provider, "provider", "embedding"))
            expected_model = str(getattr(provider, "model", ""))
            expected_dimension = int(getattr(provider, "dimension", 0) or 0)
            projection_current = (
                str(version.get("embedding_status") or "") == "ready"
                and str(version.get("embedding_provider") or "") == expected_provider
                and str(version.get("embedding_model") or "") == expected_model
                and int(version.get("embedding_dimension") or 0) == expected_dimension
                and int(version.get("embedding_indexed_count") or 0)
                == child_count
            )
            if not projection_current:
                self._job_progress(job_id, "embedding", 0.9, worker_id=worker_id)
                try:
                    embedding_report = self.embedding_indexer.index_version(
                        user_id=user_id,
                        paper_id=paper_id,
                        document_version_id=version_id,
                    )
                except Exception as exc:
                    embedding_error = str(exc)
                    flags.append("embedding_index_failed")
                    logger.warning(
                        "existing_document_embedding_index_failed",
                        extra={"paper_id": paper_id, "version_id": version_id},
                        exc_info=True,
                    )

        if str(version.get("status") or "") != "active":
            if not self.repository.activate_version(
                user_id=user_id,
                paper_id=paper_id,
                document_version_id=version_id,
            ):
                raise RuntimeError("existing document version activation failed")

        degraded = (
            str(version.get("parser_id") or "") == "pymupdf_fallback"
            or bool(flags)
            or embedding_error is not None
            or (
                embedding_report is None
                and str(version.get("embedding_status") or "") in {"failed", "degraded"}
            )
        )
        report_status = "degraded" if degraded else "succeeded"
        report = IngestReport(
            report_status,
            paper_id,
            user_id,
            document_version_id=version_id,
            job_id=job_id,
            parser_id=str(version.get("parser_id") or "") or None,
            parser_version=str(version.get("parser_version") or "") or None,
            page_count=int(version.get("page_count") or 0),
            block_count=int(version.get("block_count") or 0),
            chunk_count=int(version.get("chunk_count") or 0),
            parent_count=parent_count,
            child_count=child_count,
            embedding_indexed_count=(
                embedding_report.indexed_chunks
                if embedding_report
                else int(version.get("embedding_indexed_count") or 0)
            ),
            embedding_model=(
                embedding_report.model
                if embedding_report
                else str(version.get("embedding_model") or "") or None
            ),
            embedding_error=embedding_error,
            quality_score=float(version.get("quality_score") or 0.0),
            flags=sorted(set(flags)),
        )
        self._job_progress(
            job_id,
            "finished",
            1.0,
            status="degraded" if degraded else "succeeded",
            result_version_id=version_id,
            worker_id=worker_id,
            finalize=finalize_job,
        )
        return report

    def _persist_ready(
        self,
        parsed: ParseResult,
        gate: QualityGateResult,
        *,
        file_size: int,
        paper_title: str,
        user_id: int,
        paper_id: int,
        job_id: str | None,
        worker_id: str | None,
        finalize_job: bool,
        flags: list[str],
        version_id: str,
    ) -> IngestReport:
        assert parsed.document is not None
        document: CanonicalDocument = parsed.document
        document.quality.flags = sorted(set(document.quality.flags).union(flags))
        self._job_progress(job_id, "chunking", 0.55, worker_id=worker_id)
        chunks = self.chunker.chunk_document(document, paper_title=paper_title)
        artifact_path = self._write_artifact(document)
        self._job_progress(job_id, "persisting", 0.8, worker_id=worker_id)
        embedding_report: EmbeddingIndexReport | None = None
        embedding_error: str | None = None
        try:
            self.repository.persist_document(
                document,
                chunks,
                quality_score=gate.score,
                canonical_artifact_path=artifact_path,
            )
            if self.embedding_indexer is not None:
                self._job_progress(job_id, "embedding", 0.9, worker_id=worker_id)
                try:
                    embedding_report = self.embedding_indexer.index_version(
                        user_id=user_id,
                        paper_id=paper_id,
                        document_version_id=version_id,
                    )
                except Exception as exc:
                    # Dense retrieval is a projection, not the source of
                    # truth.  Keep the canonical/FTS version readable and
                    # report a visible degraded state for later reindexing.
                    embedding_error = str(exc)
                    flags.append("embedding_index_failed")
                    logger.warning(
                        "document_embedding_index_failed",
                        extra={"paper_id": paper_id, "version_id": version_id},
                        exc_info=True,
                    )
            status = "degraded" if gate.status == "degraded" or parsed.degraded or "pymupdf_fallback" in flags or embedding_error else "ready"
            self.repository.set_version_status(
                user_id=user_id,
                document_version_id=version_id,
                status=status,
            )
            if not self.repository.activate_version(
                user_id=user_id,
                paper_id=paper_id,
                document_version_id=version_id,
            ):
                raise RuntimeError("document version activation failed")
        except Exception as exc:
            logger.exception("document_persist_failed", extra={"paper_id": paper_id, "version_id": version_id})
            self.repository.set_version_status(
                user_id=user_id,
                document_version_id=version_id,
                status="failed",
                error_code="PERSIST_FAILED",
                error_message=str(exc),
            )
            report = IngestReport(
                "failed", paper_id, user_id, version_id, job_id,
                parsed.parser_id, parsed.parser_version,
                embedding_indexed_count=embedding_report.indexed_chunks if embedding_report else 0,
                embedding_model=embedding_report.model if embedding_report else None,
                embedding_error=embedding_error,
                error=str(exc),
                error_code="PERSIST_FAILED",
                flags=sorted(set(flags)),
            )
            self._job_progress(
                job_id,
                "failed",
                1.0,
                status="failed",
                error=str(exc),
                error_code=report.error_code,
                worker_id=worker_id,
                finalize=finalize_job,
            )
            return report

        report_status = "degraded" if status == "degraded" else "succeeded"
        report = IngestReport(
            report_status,
            paper_id,
            user_id,
            document_version_id=version_id,
            job_id=job_id,
            parser_id=parsed.parser_id,
            parser_version=parsed.parser_version,
            page_count=len(document.pages),
            block_count=len(document.blocks),
            chunk_count=len(chunks),
            parent_count=sum(chunk.level == "parent" for chunk in chunks),
            child_count=sum(chunk.level == "child" for chunk in chunks),
            embedding_indexed_count=embedding_report.indexed_chunks if embedding_report else 0,
            embedding_model=embedding_report.model if embedding_report else None,
            embedding_error=embedding_error,
            quality_score=gate.score,
            flags=sorted(set(flags)),
        )
        self._job_progress(
            job_id,
            "finished",
            1.0,
            status="degraded" if report_status == "degraded" else "succeeded",
            result_version_id=version_id,
            worker_id=worker_id,
            finalize=finalize_job,
        )
        return report

    def _write_artifact(self, document: CanonicalDocument) -> str:
        directory = self.artifacts_root / f"paper_{document.paper_id}"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{document.document_version_id}.json"
        fd, tmp_name = tempfile.mkstemp(prefix=target.name, suffix=".tmp", dir=str(directory))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(document.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return str(target)

    def _job_progress(
        self,
        job_id: str | None,
        step: str,
        progress: float,
        *,
        status: str | None = None,
        error: str | None = None,
        error_code: str | None = None,
        result_version_id: str | None = None,
        worker_id: str | None = None,
        finalize: bool = True,
    ) -> None:
        if not job_id:
            return
        try:
            effective_status = status if finalize else None
            self.repository.update_ingest_job(
                job_id=job_id,
                worker_id=worker_id,
                status=effective_status,
                current_step=step,
                progress=progress,
                error_code=(error_code or "INGEST_FAILED") if error else None,
                error_message=error,
                result_document_version_id=result_version_id,
                clear_lease=effective_status in {"succeeded", "degraded", "failed", "cancelled"},
            )
        except Exception:
            logger.warning("ingest_job_progress_failed", extra={"job_id": job_id}, exc_info=True)

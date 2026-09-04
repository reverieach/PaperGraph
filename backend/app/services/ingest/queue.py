"""Deterministic enqueue boundary for canonical PDF ingestion.

Saving a paper and parsing a paper are separate operations.  This module is
the only business boundary that turns an owned, readable local PDF into a
durable queue row; HTTP handlers and save workflows should not duplicate the
hash/ownership checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...repositories.document_repository import DocumentRepository
from .parsers import file_sha256


class IngestEnqueueError(RuntimeError):
    """An owned paper could not be converted to a durable ingest job."""


@dataclass(frozen=True, slots=True)
class IngestEnqueueResult:
    paper_id: int
    job_id: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": int(self.paper_id),
            "job_id": self.job_id,
            "status": self.status,
        }


def enqueue_owned_paper_ingest(
    *,
    db: Any,
    db_path: str,
    user_id: int,
    paper_id: int,
    parser_mode: str = "standard",
) -> IngestEnqueueResult:
    """Idempotently enqueue the local PDF of one paper owned by ``user_id``."""

    normalized_user_id = int(user_id)
    normalized_paper_id = int(paper_id)
    if parser_mode not in {"standard", "fallback", "auto"}:
        raise IngestEnqueueError("unsupported parser mode")
    paper = db.get_paper_by_id(normalized_paper_id, user_id=normalized_user_id)
    if paper is None:
        raise IngestEnqueueError("paper does not belong to user")
    pdf_path = db.get_library_pdf_abspath(
        normalized_paper_id,
        user_id=normalized_user_id,
    )
    if not pdf_path:
        raise IngestEnqueueError("owned paper has no local PDF")
    try:
        file_hash, _ = file_sha256(pdf_path)
    except OSError as exc:
        raise IngestEnqueueError(f"local PDF is unreadable: {exc}") from exc
    job_id = DocumentRepository(str(db_path)).create_ingest_job(
        user_id=normalized_user_id,
        paper_id=normalized_paper_id,
        requested_file_hash=file_hash,
        parser_mode=parser_mode,
    )
    job = DocumentRepository(str(db_path)).get_ingest_job(
        user_id=normalized_user_id,
        job_id=job_id,
    )
    return IngestEnqueueResult(
        paper_id=normalized_paper_id,
        job_id=job_id,
        status=str((job or {}).get("status") or "queued"),
    )


__all__ = [
    "IngestEnqueueError",
    "IngestEnqueueResult",
    "enqueue_owned_paper_ingest",
]

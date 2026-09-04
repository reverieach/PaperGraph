"""Persisted single-process ingest worker.

The worker is intentionally small and synchronous.  A later Redis/Dramatiq
adapter can reuse the same ``run_once`` contract without changing ingestion
or repository semantics.
"""

from __future__ import annotations

import logging
import threading
import time

from ...core.storage import PaperDatabase
from ...repositories.document_repository import DocumentRepository
from .service import IngestReport, IngestService, is_retryable_ingest_error

logger = logging.getLogger(__name__)


class IngestWorker:
    """Execute durable ingest jobs without coupling them to an HTTP request."""

    def __init__(
        self,
        db_path: str,
        *,
        service: IngestService | None = None,
        lease_seconds: int = 900,
        heartbeat_seconds: float | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self.repository = DocumentRepository(self.db_path)
        self.lease_seconds = max(30, int(lease_seconds))
        self.heartbeat_seconds = max(
            5.0,
            float(heartbeat_seconds or min(60.0, self.lease_seconds / 3)),
        )
        if service is None:
            # Import lazily to avoid a factory -> worker import cycle.  Do
            # not fall back to a differently configured service: that used
            # to make a failed production factory silently skip embeddings or
            # write artifacts to an unexpected root.
            from .factory import build_ingest_service

            service = build_ingest_service(self.db_path)
        self.service = service

    def run_once(self, *, worker_id: str, job_id: str | None = None) -> IngestReport | None:
        job = self.repository.claim_ingest_job(
            worker_id=worker_id,
            job_id=job_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None
        user_id = int(job["user_id"])
        paper_id = int(job["paper_id"])
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._renew_lease_until_stopped,
            args=(str(job["id"]), worker_id, heartbeat_stop),
            name=f"papergraph-ingest-lease-{str(job['id'])[-8:]}",
            daemon=True,
        )
        heartbeat.start()
        try:
            db = PaperDatabase(self.db_path)
            paper = db.get_paper_by_id(paper_id, user_id=user_id)
            pdf_path = db.get_library_pdf_abspath(paper_id, user_id=user_id)
            if paper is None or not pdf_path:
                raise FileNotFoundError("owned paper has no local PDF")
            report = self.service.ingest_pdf(
                user_id=user_id,
                paper_id=paper_id,
                pdf_path=pdf_path,
                paper_title=str(getattr(paper, "title", "") or ""),
                parser_mode=str(job.get("parser_mode") or "standard"),
                job_id=str(job["id"]),
                worker_id=worker_id,
                finalize_job=False,
            )
            if report.status in {"succeeded", "degraded"}:
                updated = self.repository.update_ingest_job(
                    job_id=str(job["id"]),
                    worker_id=worker_id,
                    status=report.status,
                    current_step="finished",
                    progress=1.0,
                    result_document_version_id=report.document_version_id,
                    clear_lease=True,
                )
                if not updated:
                    logger.error(
                        "ingest_worker_completion_lost_lease",
                        extra={"job_id": job["id"], "worker_id": worker_id},
                    )
                return report
            self._record_failed_attempt(
                job=job,
                worker_id=worker_id,
                error_code=report.error_code or "INGEST_FAILED",
                error_message=report.error or "ingest returned failed report",
            )
            return report
        except Exception as exc:
            logger.exception("ingest_worker_job_failed", extra={"job_id": job["id"]})
            self._record_failed_attempt(
                job=job,
                worker_id=worker_id,
                error_code="WORKER_FAILED",
                error_message=str(exc),
            )
            return IngestReport(
                "failed",
                paper_id,
                user_id,
                job_id=str(job["id"]),
                error=str(exc),
                error_code="WORKER_FAILED",
            )
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1.0)

    def _renew_lease_until_stopped(
        self,
        job_id: str,
        worker_id: str,
        stop_event: threading.Event,
    ) -> None:
        while not stop_event.wait(self.heartbeat_seconds):
            try:
                renewed = self.repository.renew_ingest_job_lease(
                    job_id=job_id,
                    worker_id=worker_id,
                    lease_seconds=self.lease_seconds,
                )
                if not renewed:
                    logger.warning(
                        "ingest_worker_lease_not_renewed",
                        extra={"job_id": job_id, "worker_id": worker_id},
                    )
                    return
            except Exception:
                # A transient SQLite contention must not kill the active
                # parser thread.  The next heartbeat still has the remaining
                # lease window to recover; every failure is observable.
                logger.warning(
                    "ingest_worker_lease_renew_failed",
                    extra={"job_id": job_id, "worker_id": worker_id},
                    exc_info=True,
                )

    def _record_failed_attempt(
        self,
        *,
        job: dict,
        worker_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        attempts = int(job.get("attempt_count") or 0)
        max_attempts = max(1, int(job.get("max_attempts") or 1))
        job_id = str(job["id"])
        if is_retryable_ingest_error(error_code) and attempts < max_attempts:
            # Keep retries deterministic and bounded.  The queue is local
            # SQLite, so a short durable backoff is preferable to a hot loop.
            retry_delay = min(300, max(2, 2 ** max(0, attempts - 1)))
            requeued = self.repository.requeue_ingest_job(
                job_id=job_id,
                worker_id=worker_id,
                retry_at=int(time.time()) + retry_delay,
                error_code=error_code,
                error_message=error_message,
            )
            if not requeued:
                logger.error(
                    "ingest_worker_retry_lost_lease",
                    extra={"job_id": job_id, "worker_id": worker_id},
                )
            return
        if not is_retryable_ingest_error(error_code):
            logger.info(
                "ingest_worker_terminal_input_failure",
                extra={"job_id": job_id, "worker_id": worker_id, "error_code": error_code},
            )
        updated = self.repository.update_ingest_job(
            job_id=job_id,
            worker_id=worker_id,
            status="failed",
            current_step="failed",
            progress=1.0,
            error_code=error_code,
            error_message=str(error_message)[:4000],
            clear_lease=True,
        )
        if not updated:
            logger.error(
                "ingest_worker_failure_lost_lease",
                extra={"job_id": job_id, "worker_id": worker_id},
            )

    def run_forever(
        self,
        *,
        worker_id: str,
        stop_event: threading.Event | None = None,
        poll_seconds: float = 2.0,
    ) -> None:
        event = stop_event or threading.Event()
        while not event.is_set():
            try:
                report = self.run_once(worker_id=worker_id)
            except Exception:
                logger.exception("ingest_worker_poll_failed", extra={"worker_id": worker_id})
                event.wait(max(0.5, float(poll_seconds)))
                continue
            if report is None:
                event.wait(max(0.1, float(poll_seconds)))

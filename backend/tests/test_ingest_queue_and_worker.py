from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.author import Author
from app.core.paper import Paper
from app.core.storage import PaperDatabase
from app.cli import backfill_ingest
from app.cli.backfill_ingest import run_backfill
from app.infrastructure.db import Database, validate_schema
from app.models.schemas import Paper as ApiPaper
from app.models.schemas import SavePapersRequest
from app.repositories.document_repository import DocumentRepository
from app.services.ingest.queue import IngestEnqueueError, enqueue_owned_paper_ingest
from app.services.ingest.service import IngestReport
from app.services.ingest.worker import IngestWorker
from app.services.papers.papers_converters import api_paper_to_litpaper, litpaper_to_api_paper
from app.services.papers.papers_library_service import save_papers
from app.workers import ingest_worker as ingest_worker_cli


def _owned_pdf_db(tmp_path) -> tuple[PaperDatabase, int, int, int]:
    db_path = str(tmp_path / "papers.db")
    database = PaperDatabase(db_path)
    with Database(db_path).transaction() as conn:
        owner_id = int(
            conn.execute(
                "INSERT INTO auth_users(username,password_hash,status,created_at,updated_at) VALUES('owner','x','active',1,1)"
            ).lastrowid
        )
        other_id = int(
            conn.execute(
                "INSERT INTO auth_users(username,password_hash,status,created_at,updated_at) VALUES('other','x','active',1,1)"
            ).lastrowid
        )
    paper_id, created = database.add_paper(
        Paper(title="Queued PDF", authors=[Author(name="Owner")]),
        user_id=owner_id,
    )
    assert created
    relative_pdf = "文献库/test/queued.pdf"
    pdf_path = Path(tmp_path) / relative_pdf
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    # Queue creation checks file readability/hash only; parser behaviour is
    # intentionally covered by the real PDF ingest service tests.
    pdf_path.write_bytes(b"%PDF-1.4\nqueue-fixture\n")
    assert database.set_local_pdf_path(paper_id, relative_pdf, user_id=owner_id)
    return database, owner_id, other_id, paper_id


def test_owned_pdf_enqueue_is_idempotent_and_status_is_scoped(tmp_path) -> None:
    database, owner_id, other_id, paper_id = _owned_pdf_db(tmp_path)
    first = enqueue_owned_paper_ingest(
        db=database,
        db_path=database.db_path,
        user_id=owner_id,
        paper_id=paper_id,
    )
    second = enqueue_owned_paper_ingest(
        db=database,
        db_path=database.db_path,
        user_id=owner_id,
        paper_id=paper_id,
    )
    assert first.job_id == second.job_id
    assert first.status == "queued"

    repository = DocumentRepository(database.db_path)
    status = repository.get_paper_ingest_status(user_id=owner_id, paper_id=paper_id)
    assert status and status["rag_ready"] is False
    assert status["latest_job"]["id"] == first.job_id
    assert repository.get_paper_ingest_status(user_id=other_id, paper_id=paper_id) is None
    try:
        enqueue_owned_paper_ingest(
            db=database,
            db_path=database.db_path,
            user_id=other_id,
            paper_id=paper_id,
        )
    except IngestEnqueueError as exc:
        assert "does not belong" in str(exc)
    else:
        raise AssertionError("cross-user enqueue must fail")


def test_save_with_download_creates_durable_ingest_job(tmp_path, monkeypatch) -> None:
    database, owner_id, _, _ = _owned_pdf_db(tmp_path)

    def fake_resolve(*_args, **_kwargs) -> str:
        return "https://example.invalid/queued.pdf"

    def fake_download(_paper, destination: str, **_kwargs) -> bool:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-1.4\nsave-fixture\n")
        return True

    monkeypatch.setattr("app.core.pdf_download.resolve_paper_pdf_url", fake_resolve)
    monkeypatch.setattr("app.core.pdf_download.download_paper_pdf_to_path", fake_download)
    monkeypatch.setattr(
        "app.services.graph.kg_relations.build_relations_for_new_paper",
        lambda *_args, **_kwargs: None,
    )
    result = asyncio.run(
        save_papers(
            db=database,
            request=SavePapersRequest(
                papers=[
                    ApiPaper(
                        title="Automatically Queued",
                        pdf_url="https://example.invalid/queued.pdf",
                    )
                ],
                download_pdfs=True,
                llm_classify=False,
            ),
            api_to_lit_fn=api_paper_to_litpaper,
            litpaper_to_api_paper_fn=litpaper_to_api_paper,
            user_id=owner_id,
        )
    )
    assert result.pdf_downloaded == 1
    assert len(result.ingest_jobs) == 1
    job = DocumentRepository(database.db_path).get_ingest_job(
        user_id=owner_id,
        job_id=result.ingest_jobs[0].job_id,
    )
    assert job and job["status"] == "queued"


class _SuccessService:
    def ingest_pdf(self, *, user_id, paper_id, job_id, **_kwargs) -> IngestReport:
        return IngestReport(
            "succeeded",
            paper_id=int(paper_id),
            user_id=int(user_id),
            job_id=str(job_id),
        )


class _FailingService:
    def ingest_pdf(self, **_kwargs) -> IngestReport:
        raise RuntimeError("fixture parser failure")


class _PermanentInputFailureService:
    def ingest_pdf(self, *, user_id, paper_id, job_id, **_kwargs) -> IngestReport:
        return IngestReport(
            "failed",
            paper_id=int(paper_id),
            user_id=int(user_id),
            job_id=str(job_id),
            error="PDF is encrypted and requires a password",
            error_code="PDF_ENCRYPTED",
        )


def test_worker_finishes_owned_job_with_lease_owner(tmp_path) -> None:
    database, owner_id, _, paper_id = _owned_pdf_db(tmp_path)
    queued = enqueue_owned_paper_ingest(
        db=database,
        db_path=database.db_path,
        user_id=owner_id,
        paper_id=paper_id,
    )
    report = IngestWorker(
        database.db_path,
        service=_SuccessService(),  # type: ignore[arg-type]
        heartbeat_seconds=60,
    ).run_once(worker_id="worker-success", job_id=queued.job_id)
    assert report and report.status == "succeeded"
    job = DocumentRepository(database.db_path).get_ingest_job(
        user_id=owner_id,
        job_id=queued.job_id,
    )
    assert job and job["status"] == "succeeded"
    assert job["lease_owner"] is None
    assert job["result_document_version_id"] is None


def test_worker_retries_then_marks_terminal_failure(tmp_path) -> None:
    database, owner_id, _, paper_id = _owned_pdf_db(tmp_path)
    queued = enqueue_owned_paper_ingest(
        db=database,
        db_path=database.db_path,
        user_id=owner_id,
        paper_id=paper_id,
    )
    worker = IngestWorker(
        database.db_path,
        service=_FailingService(),  # type: ignore[arg-type]
        heartbeat_seconds=60,
    )
    repository = DocumentRepository(database.db_path)
    for attempt in range(1, 4):
        report = worker.run_once(worker_id="worker-failure", job_id=queued.job_id)
        assert report and report.status == "failed"
        job = repository.get_ingest_job(user_id=owner_id, job_id=queued.job_id)
        assert job is not None
        if attempt < 3:
            assert job["status"] == "queued"
            assert int(job["next_attempt_at"]) > 0
            with Database(database.db_path).transaction() as conn:
                conn.execute("UPDATE ingest_jobs SET next_attempt_at=0 WHERE id=?", (queued.job_id,))
        else:
            assert job["status"] == "failed"
            assert job["current_step"] == "failed"
            assert job["lease_owner"] is None


def test_worker_does_not_retry_a_terminal_pdf_input_failure(tmp_path) -> None:
    database, owner_id, _, paper_id = _owned_pdf_db(tmp_path)
    queued = enqueue_owned_paper_ingest(
        db=database,
        db_path=database.db_path,
        user_id=owner_id,
        paper_id=paper_id,
    )
    report = IngestWorker(
        database.db_path,
        service=_PermanentInputFailureService(),  # type: ignore[arg-type]
        heartbeat_seconds=60,
    ).run_once(worker_id="worker-terminal-input", job_id=queued.job_id)

    assert report and report.status == "failed" and report.error_code == "PDF_ENCRYPTED"
    job = DocumentRepository(database.db_path).get_ingest_job(
        user_id=owner_id,
        job_id=queued.job_id,
    )
    assert job is not None
    assert job["status"] == "failed"
    assert job["attempt_count"] == 1
    assert job["error_code"] == "PDF_ENCRYPTED"
    assert job["lease_owner"] is None


def test_worker_cli_once_initializes_an_isolated_database(tmp_path, monkeypatch) -> None:
    # Configuration credentials are not the subject of this CLI-contract test;
    # the real environment is checked separately by the strict preflight.
    monkeypatch.setattr(ingest_worker_cli, "validate_config", lambda: True)
    exit_code = ingest_worker_cli.main(
        ["--db-path", str(tmp_path / "worker-cli.db"), "--once", "--worker-id", "test-worker"]
    )
    assert exit_code == 0
    with Database(str(tmp_path / "worker-cli.db")).read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 11


def test_backfill_defaults_to_dry_run_then_enqueues_only_owned_pdf(tmp_path) -> None:
    database, owner_id, _, paper_id = _owned_pdf_db(tmp_path)
    dry_run = run_backfill(
        db_path=database.db_path,
        user_id=owner_id,
        paper_id=paper_id,
        resume_after_paper_id=0,
        limit=10,
        parser_mode="standard",
        execute=False,
    )
    assert dry_run["mode"] == "dry_run"
    assert dry_run["would_enqueue"] == [{"paper_id": paper_id, "user_id": owner_id}]
    repository = DocumentRepository(database.db_path)
    assert repository.get_paper_ingest_status(user_id=owner_id, paper_id=paper_id)["latest_job"] is None

    execute = run_backfill(
        db_path=database.db_path,
        user_id=owner_id,
        paper_id=paper_id,
        resume_after_paper_id=0,
        limit=10,
        parser_mode="standard",
        execute=True,
    )
    assert execute["mode"] == "execute"
    assert len(execute["enqueued"]) == 1
    assert repository.get_paper_ingest_status(user_id=owner_id, paper_id=paper_id)["latest_job"]["status"] == "queued"


def test_backfill_cli_dry_run_uses_read_only_schema_validation(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(backfill_ingest, "validate_config", lambda: True)
    monkeypatch.setattr(
        backfill_ingest,
        "validate_schema",
        lambda path, *, read_only=False: calls.append(("validate", (path, read_only))),
    )
    monkeypatch.setattr(
        backfill_ingest,
        "run_migrations",
        lambda path: calls.append(("migrate", path)),
    )
    monkeypatch.setattr(
        backfill_ingest,
        "run_backfill",
        lambda **_kwargs: {"failed": []},
    )

    exit_code = backfill_ingest.main(
        ["--db-path", str(tmp_path / "not-created.db"), "--user-id", "7"]
    )

    assert exit_code == 0
    assert calls == [("validate", (str((tmp_path / "not-created.db").resolve()), True))]
    assert not (tmp_path / "not-created.db").exists()


def test_schema_validation_supports_a_read_only_database_connection(tmp_path) -> None:
    database, _, _, _ = _owned_pdf_db(tmp_path)
    validate_schema(database.db_path, read_only=True)

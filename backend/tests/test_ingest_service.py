from __future__ import annotations

import fitz

from app.infrastructure.db import Database, run_migrations
from app.repositories.document_repository import DocumentRepository
from app.services.ingest.service import IngestService


def _db_with_paper(tmp_path) -> tuple[str, int, int]:
    path = str(tmp_path / "ingest.db")
    run_migrations(path)
    with Database(path).transaction() as conn:
        user = conn.execute(
            "INSERT INTO auth_users(username,password_hash,status,created_at,updated_at) VALUES('ingest','x','active',1,1)"
        ).lastrowid
        paper = conn.execute(
            "INSERT INTO papers(user_id,title,created_at,updated_at) VALUES(?,?,1,1)",
            (user, "Ingest test paper"),
        ).lastrowid
    assert user is not None and paper is not None
    return path, int(user), int(paper)


def _pdf(tmp_path) -> str:
    path = tmp_path / "paper.pdf"
    doc = fitz.open()
    for page_no in range(1, 3):
        page = doc.new_page(width=612, height=792)
        page.insert_text(
            (72, 72),
            f"{page_no} Introduction\n" +
            "Retrieval augmented generation provides evidence grounded answers. " * 12,
        )
    doc.save(path)
    doc.close()
    return str(path)


def _encrypted_pdf(tmp_path) -> str:
    path = tmp_path / "encrypted.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "This PDF requires a password before ingestion.")
    doc.save(
        path,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner-password",
        user_pw="user-password",
    )
    doc.close()
    return str(path)


def test_ingest_service_persists_and_activates_fallback_version(tmp_path) -> None:
    db_path, user_id, paper_id = _db_with_paper(tmp_path)
    job_id = DocumentRepository(db_path).create_ingest_job(
        user_id=user_id,
        paper_id=paper_id,
        requested_file_hash=None,
        parser_mode="fallback",
    )
    report = IngestService(
        db_path,
        artifacts_root=str(tmp_path / "artifacts"),
    ).ingest_pdf(
        user_id=user_id,
        paper_id=paper_id,
        pdf_path=_pdf(tmp_path),
        paper_title="Ingest test paper",
        parser_mode="fallback",
        job_id=job_id,
    )
    assert report.status == "degraded"
    assert report.document_version_id
    assert report.page_count == 2
    assert report.child_count > 0
    repo = DocumentRepository(db_path)
    active = repo.get_active_version(user_id=user_id, paper_id=paper_id)
    # The active marker is separate from the quality/degradation state; the
    # latter is preserved in quality_json and the report flags.
    assert active and active["status"] == "active"
    job = repo.get_ingest_job(user_id=user_id, job_id=job_id)
    assert job and job["status"] == "degraded" and job["progress"] == 1.0
    assert (tmp_path / "artifacts" / f"paper_{paper_id}" / f"{report.document_version_id}.json").exists()


def test_ingest_service_reuses_active_canonical_version_on_retry(tmp_path) -> None:
    db_path, user_id, paper_id = _db_with_paper(tmp_path)
    pdf_path = _pdf(tmp_path)
    repo = DocumentRepository(db_path)
    first_job = repo.create_ingest_job(
        user_id=user_id,
        paper_id=paper_id,
        requested_file_hash="same-file",
        parser_mode="fallback",
    )
    service = IngestService(
        db_path,
        artifacts_root=str(tmp_path / "artifacts"),
    )
    first = service.ingest_pdf(
        user_id=user_id,
        paper_id=paper_id,
        pdf_path=pdf_path,
        paper_title="Ingest test paper",
        parser_mode="fallback",
        job_id=first_job,
    )
    second_job = repo.create_ingest_job(
        user_id=user_id,
        paper_id=paper_id,
        requested_file_hash="same-file",
        parser_mode="fallback",
    )
    second = service.ingest_pdf(
        user_id=user_id,
        paper_id=paper_id,
        pdf_path=pdf_path,
        paper_title="Ingest test paper",
        parser_mode="fallback",
        job_id=second_job,
    )
    assert second_job != first_job
    assert second.status == "degraded"
    assert second.document_version_id == first.document_version_id
    assert second.page_count == first.page_count
    assert repo.get_ingest_job(user_id=user_id, job_id=second_job)["status"] == "degraded"


def test_ingest_service_marks_corrupt_pdf_and_job_as_failed_without_activation(tmp_path) -> None:
    db_path, user_id, paper_id = _db_with_paper(tmp_path)
    corrupt_path = tmp_path / "corrupt.pdf"
    corrupt_path.write_bytes(b"this is deliberately not a PDF")
    repository = DocumentRepository(db_path)
    job_id = repository.create_ingest_job(
        user_id=user_id,
        paper_id=paper_id,
        requested_file_hash=None,
        parser_mode="fallback",
    )

    report = IngestService(
        db_path,
        artifacts_root=str(tmp_path / "artifacts"),
    ).ingest_pdf(
        user_id=user_id,
        paper_id=paper_id,
        pdf_path=str(corrupt_path),
        paper_title="Ingest test paper",
        parser_mode="fallback",
        job_id=job_id,
    )

    assert report.status == "failed"
    assert report.document_version_id is None
    assert report.error == "PDF cannot be opened or is corrupted"
    assert report.error_code == "PDF_INVALID"
    assert repository.get_active_version(user_id=user_id, paper_id=paper_id) is None
    job = repository.get_ingest_job(user_id=user_id, job_id=job_id)
    assert job and job["status"] == "failed" and job["progress"] == 1.0
    versions = repository.db.query_all(
        "SELECT status, error_code FROM document_versions WHERE user_id=? AND paper_id=?",
        (user_id, paper_id),
    )
    assert [dict(row) for row in versions] == [
        {"status": "failed", "error_code": "PDF_INVALID"}
    ]


def test_ingest_service_reports_encrypted_pdf_without_activating_a_version(tmp_path) -> None:
    db_path, user_id, paper_id = _db_with_paper(tmp_path)
    repository = DocumentRepository(db_path)
    job_id = repository.create_ingest_job(
        user_id=user_id,
        paper_id=paper_id,
        requested_file_hash=None,
        parser_mode="fallback",
    )

    report = IngestService(
        db_path,
        artifacts_root=str(tmp_path / "artifacts"),
    ).ingest_pdf(
        user_id=user_id,
        paper_id=paper_id,
        pdf_path=_encrypted_pdf(tmp_path),
        paper_title="Encrypted PDF",
        parser_mode="fallback",
        job_id=job_id,
    )

    assert report.status == "failed"
    assert report.error_code == "PDF_ENCRYPTED"
    assert repository.get_active_version(user_id=user_id, paper_id=paper_id) is None
    job = repository.get_ingest_job(user_id=user_id, job_id=job_id)
    assert job and job["status"] == "failed" and job["error_code"] == "PDF_ENCRYPTED"
    versions = repository.db.query_all(
        "SELECT status, error_code FROM document_versions WHERE user_id=? AND paper_id=?",
        (user_id, paper_id),
    )
    assert [dict(row) for row in versions] == [
        {"status": "failed", "error_code": "PDF_ENCRYPTED"}
    ]

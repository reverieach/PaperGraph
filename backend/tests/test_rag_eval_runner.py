"""Tests for the isolated RAG evaluation workspace and manifest contract."""

from __future__ import annotations

import hashlib
import json
import zipfile

import fitz
import pytest

from app.infrastructure.db import Database
from app.repositories.document_repository import DocumentRepository
from app.services.evaluation.rag_eval import (
    CorpusManifestError,
    EvaluationWorkspace,
    load_corpus_manifest,
    load_retrieval_cases,
    prepare_workspace,
    run_ingest,
    run_retrieval_evaluation,
    validate_retrieval_cases,
    verify_corpus,
)
from app.services.evaluation.scifact import (
    prepare_scifact_subset,
    run_scifact_sparse_scorecard,
)


def _make_pdf(path) -> str:
    document = fitz.open()
    page = document.new_page()
    text = " ".join(
        [
            "Evaluation fixture documents contain meaningful text for canonical ingestion.",
            "The fallback parser extracts this paragraph into a page, block, and chunk.",
            "The deterministic test checks provenance, retrieval labels, and active versions.",
        ]
        * 4
    )
    inserted = page.insert_textbox(fitz.Rect(72, 72, 523, 700), text, fontsize=10)
    assert inserted >= 0
    document.save(path)
    document.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(path, *, relative_file: str, sha256: str, pages: int = 1) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "test corpus",
                "papers": [
                    {
                        "corpus_id": "fixture_001",
                        "title": "Fixture Paper",
                        "file": relative_file,
                        "source_url": "https://example.invalid/source",
                        "pdf_url": "https://example.invalid/file.pdf",
                        "sha256": sha256,
                        "pages": pages,
                        "languages": ["en"],
                        "features": ["fixture"],
                        "license_or_usage_note": "test fixture",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_prepare_workspace_isolated_from_other_database(tmp_path) -> None:
    corpus_root = tmp_path / "corpus"
    pdf_dir = corpus_root / "pdfs"
    pdf_dir.mkdir(parents=True)
    pdf_path = pdf_dir / "fixture.pdf"
    digest = _make_pdf(pdf_path)
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, relative_file="pdfs/fixture.pdf", sha256=digest)

    business_db = tmp_path / "business.db"
    with Database(str(business_db)).transaction() as conn:
        conn.execute("CREATE TABLE sentinel(value TEXT)")
        conn.execute("INSERT INTO sentinel(value) VALUES('must-not-change')")

    workspace, mapping = prepare_workspace(
        manifest_path=manifest,
        corpus_root=corpus_root,
        workspace_root=tmp_path / "isolated-workspace",
    )
    workspace_again, mapping_again = prepare_workspace(
        manifest_path=manifest,
        corpus_root=corpus_root,
        workspace_root=tmp_path / "isolated-workspace",
    )

    assert workspace.db_path != business_db
    assert EvaluationWorkspace.from_root(workspace.root) == workspace
    assert workspace_again == workspace
    assert mapping["user_id"] > 0
    assert mapping["papers"][0]["corpus_id"] == "fixture_001"
    assert mapping_again["papers"][0]["paper_id"] == mapping["papers"][0]["paper_id"]
    with Database(str(business_db)).read() as conn:
        assert conn.execute("SELECT value FROM sentinel").fetchone()[0] == "must-not-change"
    with Database(str(workspace.db_path)).read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1


def test_verify_corpus_rejects_path_escape_and_hash_mismatch(tmp_path) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    outside = tmp_path / "outside.pdf"
    digest = _make_pdf(outside)
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, relative_file="../outside.pdf", sha256=digest)
    _, papers = load_corpus_manifest(manifest)
    with pytest.raises(CorpusManifestError, match="escapes root"):
        verify_corpus(papers, corpus_root=corpus_root)


def test_retrieval_case_contract_rejects_duplicate_ids(tmp_path) -> None:
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        "\n".join(
            [
                json.dumps({"case_id": "c1", "query": "what", "corpus_ids": ["fixture_001"]}),
                json.dumps({"case_id": "c1", "query": "why", "corpus_ids": ["fixture_001"]}),
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(CorpusManifestError, match="duplicate case_id"):
        load_retrieval_cases(cases)


def test_tracked_case_requires_evidence_provenance(tmp_path) -> None:
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps(
            {
                "case_id": "tracked_missing_metadata",
                "query": "what is retrieval?",
                "corpus_ids": ["fixture_001"],
                "quality_status": "silver",
                "review_status": "auto_verified",
                "answerable": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CorpusManifestError, match="tracked metadata missing"):
        load_retrieval_cases(cases)


def test_external_eval_paths_fail_closed_without_opt_in(tmp_path) -> None:
    corpus_root = tmp_path / "corpus"
    pdf_dir = corpus_root / "pdfs"
    pdf_dir.mkdir(parents=True)
    pdf_path = pdf_dir / "fixture.pdf"
    digest = _make_pdf(pdf_path)
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, relative_file="pdfs/fixture.pdf", sha256=digest)
    workspace, _mapping = prepare_workspace(
        manifest_path=manifest,
        corpus_root=corpus_root,
        workspace_root=tmp_path / "isolated-workspace",
    )
    with pytest.raises(CorpusManifestError, match="run_external=True"):
        run_ingest(
            workspace_root=workspace.root,
            with_embedding=True,
            run_external=False,
        )

    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps(
            {
                "case_id": "ad_hoc",
                "query": "fixture",
                "corpus_ids": ["fixture_001"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CorpusManifestError, match="run_external=True"):
        run_retrieval_evaluation(
            workspace_root=workspace.root,
            cases_path=cases,
            with_dense=True,
            run_external=False,
        )


def test_tracked_qrel_is_checked_against_active_canonical_chunk(tmp_path) -> None:
    corpus_root = tmp_path / "corpus"
    pdf_dir = corpus_root / "pdfs"
    pdf_dir.mkdir(parents=True)
    pdf_path = pdf_dir / "fixture.pdf"
    digest = _make_pdf(pdf_path)
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, relative_file="pdfs/fixture.pdf", sha256=digest)
    workspace, mapping = prepare_workspace(
        manifest_path=manifest,
        corpus_root=corpus_root,
        workspace_root=tmp_path / "isolated-workspace",
    )
    run_ingest(workspace_root=workspace.root, parser_mode="fallback")
    repository = DocumentRepository(str(workspace.db_path))
    active = repository.get_active_version(
        user_id=int(mapping["user_id"]),
        paper_id=int(mapping["papers"][0]["paper_id"]),
    )
    assert active is not None
    chunk = repository.get_chunks_by_uid(
        user_id=int(mapping["user_id"]),
        chunk_uids=[
            row["chunk_uid"]
            for row in repository.db.query_all(
                "SELECT chunk_uid FROM document_chunks WHERE document_version_id=? ORDER BY ordinal LIMIT 1",
                (active["id"],),
            )
        ],
    )[0]
    cases = tmp_path / "tracked.jsonl"
    payload = {
        "case_id": "tracked_fixture",
        "query": "Evaluation fixture",
        "corpus_ids": ["fixture_001"],
        "quality_status": "silver",
        "review_status": "auto_verified",
        "generator_model": "deterministic",
        "generator_prompt_version": "v1",
        "verifier_model": "deterministic",
        "source_sha256": digest,
        "parser_version": active["parser_version"],
        "chunker_version": active["chunker_version"],
        "query_language": "en",
        "document_language": "en",
        "task_type": "factual",
        "answerable": True,
        "evidence": [
            {
                "corpus_id": "fixture_001",
                "group": "fixture_fact",
                "pages": [int(chunk["page_start"])],
                "chunk_uids": [chunk["chunk_uid"]],
                "chunk_text_hashes": [chunk["text_hash"]],
            }
        ],
    }
    cases.write_text(json.dumps(payload), encoding="utf-8")
    validation = validate_retrieval_cases(
        workspace_root=workspace.root,
        cases_path=cases,
    )
    assert validation["verified_chunk_qrel_count"] == 1

    payload["source_sha256"] = "0" * 64
    cases.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusManifestError, match="source_sha256"):
        validate_retrieval_cases(workspace_root=workspace.root, cases_path=cases)


def _write_scifact_archive(path) -> str:
    corpus = [
        {
            "_id": "d1",
            "title": "Quasar retrieval study",
            "text": "The uniquequasar signal supports the primary scientific claim.",
        },
        {
            "_id": "d2",
            "title": "Aurora retrieval study",
            "text": "The uniqueaurora observation supports a separate claim.",
        },
        {
            "_id": "d3",
            "title": "Distractor one",
            "text": "This record intentionally has unrelated vocabulary.",
        },
        {
            "_id": "d4",
            "title": "Distractor two",
            "text": "Another unrelated abstract for the bounded corpus.",
        },
    ]
    queries = [
        {"_id": "q1", "text": "Which evidence mentions uniquequasar?"},
        {"_id": "q2", "text": "Which evidence mentions uniqueaurora?"},
    ]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "scifact/corpus.jsonl",
            "\n".join(json.dumps(row) for row in corpus) + "\n",
        )
        archive.writestr(
            "scifact/queries.jsonl",
            "\n".join(json.dumps(row) for row in queries) + "\n",
        )
        archive.writestr(
            "scifact/qrels/test.tsv",
            "query-id\tcorpus-id\tscore\nq1\td1\t1\nq2\td2\t1\n",
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_scifact_manifest(path, *, archive_sha256: str) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark_id": "fixture_scifact_v1",
                "archive_sha256": archive_sha256,
                "archive_members": {
                    "corpus": "scifact/corpus.jsonl",
                    "queries": "scifact/queries.jsonl",
                    "qrels": "scifact/qrels/test.tsv",
                },
                "source_urls": ["https://example.invalid/scifact"],
                "license_or_usage_note": "test fixture only",
                "selection": {
                    "seed": "fixture-seed",
                    "query_limit": 2,
                    "document_limit": 4,
                },
            }
        ),
        encoding="utf-8",
    )


def test_scifact_scorecard_uses_isolated_text_only_workspace(tmp_path) -> None:
    archive = tmp_path / "scifact.zip"
    digest = _write_scifact_archive(archive)
    manifest = tmp_path / "scifact.json"
    _write_scifact_manifest(manifest, archive_sha256=digest)
    business_db = tmp_path / "business.db"
    with Database(str(business_db)).transaction() as conn:
        conn.execute("CREATE TABLE sentinel(value TEXT)")
        conn.execute("INSERT INTO sentinel(value) VALUES('must-not-change')")

    workspace = tmp_path / "benchmark-workspace"
    prepared = prepare_scifact_subset(
        manifest_path=manifest,
        archive_path=archive,
        workspace_root=workspace,
    )
    prepared_again = prepare_scifact_subset(
        manifest_path=manifest,
        archive_path=archive,
        workspace_root=workspace,
    )
    scorecard = run_scifact_sparse_scorecard(workspace_root=workspace, limit=2)

    assert prepared["evaluation_surface"] == "public_text_retrieval_only_no_pdf_page_citations"
    assert prepared["query_count"] == 2
    assert prepared["document_count"] == 4
    assert prepared_again["created_document_count"] == 0
    assert prepared_again["reused_document_count"] == 4
    assert scorecard["external_provider_calls"] is False
    assert scorecard["recall_at_k"] == 1.0
    assert scorecard["mrr_at_k"] == 1.0
    assert all(
        len(case["hit_document_ids"]) == len(set(case["hit_document_ids"]))
        for case in scorecard["cases"]
    )
    with Database(str(business_db)).read() as conn:
        assert conn.execute("SELECT value FROM sentinel").fetchone()[0] == "must-not-change"

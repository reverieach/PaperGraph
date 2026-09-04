from __future__ import annotations

import json

import pytest

from app.domain.document import (
    CanonicalDocument,
    DocumentBlock,
    DocumentChunk,
    DocumentPage,
    ParseQualityReport,
)
from app.infrastructure.db import Database, run_migrations
from app.repositories.document_repository import DocumentRepository
from app.services.retrieval.hybrid import HybridChunkRetriever


def _fixture_db(tmp_path) -> tuple[str, int, int]:
    path = str(tmp_path / "documents.db")
    run_migrations(path)
    with Database(path).transaction() as conn:
        user = conn.execute(
            """
            INSERT INTO auth_users(username,password_hash,status,created_at,updated_at)
            VALUES('doc-user','hash','active',1,1)
            """
        ).lastrowid
        paper = conn.execute(
            """
            INSERT INTO papers(user_id,title,created_at,updated_at)
            VALUES(?,?,1,1)
            """,
            (user, "A document paper"),
        ).lastrowid
    assert user is not None and paper is not None
    return path, int(user), int(paper)


def _document(version_id: str, user_id: int, paper_id: int) -> CanonicalDocument:
    page = DocumentPage(
        page_index=1,
        printed_page_label="1",
        width=612,
        height=792,
        text="Retrieval augmented generation improves evidence grounding.",
    )
    block = DocumentBlock(
        block_uid=f"{version_id}:block-1",
        page_index=1,
        block_order=0,
        block_type="paragraph",
        section_path=["1 Introduction"],
        text=page.text,
        provenance={"source": "test"},
    )
    quality = ParseQualityReport(
        page_count=1,
        non_empty_page_count=1,
        block_count=1,
        text_char_count=len(page.text),
        pages_with_provenance=1,
        score=0.95,
    )
    return CanonicalDocument(
        document_version_id=version_id,
        user_id=user_id,
        paper_id=paper_id,
        file_hash="file-hash",
        parser_id="test",
        parser_version="1",
        pages=[page],
        blocks=[block],
        quality=quality,
    )


def _chunks(version_id: str, user_id: int, paper_id: int) -> list[DocumentChunk]:
    parent = DocumentChunk(
        chunk_uid=f"{version_id}:parent-1",
        document_version_id=version_id,
        user_id=user_id,
        paper_id=paper_id,
        parent_chunk_uid=None,
        level="parent",
        ordinal=0,
        content_type="paragraph",
        section_path=["1 Introduction"],
        page_start=1,
        page_end=1,
        block_uids=[f"{version_id}:block-1"],
        display_text="Retrieval augmented generation improves evidence grounding.",
        embedding_text="A document paper / 1 Introduction / Retrieval augmented generation improves evidence grounding.",
        sparse_text="retrieval augmented generation evidence grounding",
        text_hash="parent-hash",
        token_count=8,
        chunker_version="test-1",
    )
    child = DocumentChunk(
        chunk_uid=f"{version_id}:child-1",
        document_version_id=version_id,
        user_id=user_id,
        paper_id=paper_id,
        parent_chunk_uid=f"{version_id}:parent-1",
        level="child",
        ordinal=1,
        content_type="paragraph",
        section_path=["1 Introduction"],
        page_start=1,
        page_end=1,
        block_uids=[f"{version_id}:block-1"],
        display_text="Retrieval augmented generation improves evidence grounding.",
        embedding_text="A document paper / 1 Introduction / Retrieval augmented generation improves evidence grounding.",
        sparse_text="retrieval augmented generation evidence grounding",
        text_hash="child-hash",
        token_count=8,
        chunker_version="test-1",
    )
    return [parent, child]


def test_document_version_persistence_activation_and_fts(tmp_path) -> None:
    path, user_id, paper_id = _fixture_db(tmp_path)
    repo = DocumentRepository(path)
    version_id = repo.create_or_get_version(
        user_id=user_id,
        paper_id=paper_id,
        file_hash="file-hash",
        file_size=100,
        parser_id="test",
        parser_version="1",
        parser_config_hash="config",
        chunker_version="test-1",
    )
    assert version_id == repo.create_or_get_version(
        user_id=user_id,
        paper_id=paper_id,
        file_hash="file-hash",
        file_size=100,
        parser_id="test",
        parser_version="1",
        parser_config_hash="config",
        chunker_version="test-1",
    )
    # Changing a rebuildable embedding projection must not conflict with or
    # duplicate the immutable canonical parse.
    assert version_id == repo.create_or_get_version(
        user_id=user_id,
        paper_id=paper_id,
        file_hash="file-hash",
        file_size=100,
        parser_id="test",
        parser_version="1",
        parser_config_hash="config",
        chunker_version="test-1",
        embedding_provider="other",
        embedding_model="embedding-v2",
        embedding_dimension=2048,
    )
    document = _document(version_id, user_id, paper_id)
    persisted = repo.persist_document(document, _chunks(version_id, user_id, paper_id))
    assert persisted["page_count"] == 1
    assert persisted["chunk_count"] == 2
    assert repo.get_active_version(user_id=user_id, paper_id=paper_id) is None
    assert repo.activate_version(
        user_id=user_id,
        paper_id=paper_id,
        document_version_id=version_id,
    )
    active = repo.get_active_version(user_id=user_id, paper_id=paper_id)
    assert active is not None
    assert active["status"] == "active"
    assert repo.list_chunks(user_id=user_id, paper_id=paper_id, level="child")[0]["chunk_uid"].endswith(":child-1")
    assert repo.has_fts()
    hits = repo.search_fts(
        user_id=user_id,
        paper_ids=[paper_id],
        match_query="retrieval augmented",
    )
    assert hits and hits[0]["chunk_uid"].endswith(":child-1")


def test_trigram_projection_recalls_natural_chinese_question_with_scope(tmp_path) -> None:
    path, user_id, paper_id = _fixture_db(tmp_path)
    repo = DocumentRepository(path)
    if not repo.has_trigram_fts():
        pytest.skip("SQLite build does not provide the optional FTS5 trigram tokenizer")
    version_id = repo.create_or_get_version(
        user_id=user_id,
        paper_id=paper_id,
        file_hash="cn-file-hash",
        file_size=100,
        parser_id="test",
        parser_version="1",
        parser_config_hash="config",
        chunker_version="test-1",
    )
    document = _document(version_id, user_id, paper_id)
    chinese_text = "作者指出，长上下文中间的信息更容易丢失，检索可缓解这一问题。"
    document.pages[0].text = chinese_text
    document.blocks[0].text = chinese_text
    chunks = _chunks(version_id, user_id, paper_id)
    for chunk in chunks:
        chunk.display_text = chinese_text
        chunk.embedding_text = f"中文检索论文 / 方法 / {chinese_text}"
        chunk.sparse_text = "作 者 指 出 长 上 下 文 中 间 的 信 息 更 容 易 丢 失 检 索"
    repo.persist_document(document, chunks)
    assert repo.activate_version(
        user_id=user_id, paper_id=paper_id, document_version_id=version_id
    )

    direct = repo.search_trigram_fts(
        user_id=user_id,
        paper_ids=[paper_id],
        match_query='"长上下文"',
    )
    assert direct and direct[0]["chunk_uid"].endswith(":child-1")
    result = HybridChunkRetriever(repo).retrieve(
        user_id=user_id,
        paper_ids=[paper_id],
        query="作者为什么认为长上下文中间的信息更容易丢失？",
        limit=3,
    )
    assert result.hits
    assert "bm25_trigram" in result.hits[0].sources
    assert result.sparse_trigram_count >= 1
    assert not HybridChunkRetriever(repo).retrieve(
        user_id=user_id + 1,
        paper_ids=[paper_id],
        query="长上下文信息丢失",
    ).hits


def test_document_repository_enforces_user_scope_and_active_version_switch(tmp_path) -> None:
    path, user_id, paper_id = _fixture_db(tmp_path)
    repo = DocumentRepository(path)
    with pytest.raises(ValueError):
        repo.create_or_get_version(
            user_id=user_id + 1,
            paper_id=paper_id,
            file_hash="file-hash",
            file_size=1,
            parser_id="test",
            parser_version="1",
            parser_config_hash="config",
            chunker_version="test-1",
        )

    first = repo.create_or_get_version(
        user_id=user_id,
        paper_id=paper_id,
        file_hash="first",
        file_size=1,
        parser_id="test",
        parser_version="1",
        parser_config_hash="config",
        chunker_version="test-1",
    )
    repo.persist_document(_document(first, user_id, paper_id), _chunks(first, user_id, paper_id))
    assert repo.activate_version(user_id=user_id, paper_id=paper_id, document_version_id=first)

    second = repo.create_or_get_version(
        user_id=user_id,
        paper_id=paper_id,
        file_hash="second",
        file_size=2,
        parser_id="test",
        parser_version="1",
        parser_config_hash="config",
        chunker_version="test-1",
    )
    second_doc = _document(second, user_id, paper_id)
    second_doc.file_hash = "second"
    repo.persist_document(second_doc, _chunks(second, user_id, paper_id))
    assert repo.activate_version(user_id=user_id, paper_id=paper_id, document_version_id=second)
    assert repo.get_active_version(user_id=user_id, paper_id=paper_id)["id"] == second
    statuses = repo.db.query_all(
        "SELECT id,status FROM document_versions ORDER BY created_at,id"
    )
    assert {row["status"] for row in statuses} == {"superseded", "active"}


def test_ingest_job_is_idempotent_and_user_scoped(tmp_path) -> None:
    path, user_id, paper_id = _fixture_db(tmp_path)
    repo = DocumentRepository(path)
    job = repo.create_ingest_job(
        user_id=user_id,
        paper_id=paper_id,
        requested_file_hash="hash",
    )
    assert job == repo.create_ingest_job(
        user_id=user_id,
        paper_id=paper_id,
        requested_file_hash="hash",
    )
    assert repo.get_ingest_job(user_id=user_id, job_id=job)["status"] == "queued"
    assert repo.get_ingest_job(user_id=user_id + 1, job_id=job) is None
    claimed = repo.claim_ingest_job(worker_id="worker", job_id=job)
    assert claimed and claimed["id"] == job
    assert repo.update_ingest_job(
        job_id=job,
        worker_id="worker",
        status="succeeded",
        progress=1.0,
        clear_lease=True,
    )
    retry_job = repo.create_ingest_job(
        user_id=user_id,
        paper_id=paper_id,
        requested_file_hash="hash",
    )
    assert retry_job != job
    assert repo.get_ingest_job(user_id=user_id, job_id=retry_job)["status"] == "queued"


def test_ingest_job_claim_uses_lease_and_is_single_owner(tmp_path) -> None:
    path, user_id, paper_id = _fixture_db(tmp_path)
    repo = DocumentRepository(path)
    job = repo.create_ingest_job(
        user_id=user_id,
        paper_id=paper_id,
        requested_file_hash="claim-hash",
    )
    claimed = repo.claim_ingest_job(worker_id="worker-a", lease_seconds=60)
    assert claimed and claimed["id"] == job
    assert claimed["status"] == "running"
    assert claimed["lease_owner"] == "worker-a"
    assert repo.claim_ingest_job(worker_id="worker-b", lease_seconds=60) is None
    assert repo.update_ingest_job(
        job_id=job,
        worker_id="worker-a",
        status="succeeded",
        progress=1.0,
        clear_lease=True,
    )


def test_ingest_job_heartbeat_expired_lease_recovery_and_exhaustion(tmp_path) -> None:
    path, user_id, paper_id = _fixture_db(tmp_path)
    repo = DocumentRepository(path)
    recoverable = repo.create_ingest_job(
        user_id=user_id,
        paper_id=paper_id,
        requested_file_hash="recoverable",
    )
    first = repo.claim_ingest_job(worker_id="worker-a", job_id=recoverable, lease_seconds=60)
    assert first and first["attempt_count"] == 1
    assert repo.renew_ingest_job_lease(
        job_id=recoverable,
        worker_id="worker-a",
        lease_seconds=60,
    )
    renewed = repo.get_ingest_job(user_id=user_id, job_id=recoverable)
    assert renewed and renewed["last_heartbeat_at"] is not None
    with Database(path).transaction() as conn:
        conn.execute("UPDATE ingest_jobs SET lease_expires_at=0 WHERE id=?", (recoverable,))
    reclaimed = repo.claim_ingest_job(worker_id="worker-b", job_id=recoverable, lease_seconds=60)
    assert reclaimed and reclaimed["attempt_count"] == 2
    assert reclaimed["lease_owner"] == "worker-b"

    exhausted = repo.create_ingest_job(
        user_id=user_id,
        paper_id=paper_id,
        requested_file_hash="exhausted",
        max_attempts=1,
    )
    assert repo.claim_ingest_job(worker_id="worker-c", job_id=exhausted, lease_seconds=60)
    with Database(path).transaction() as conn:
        conn.execute("UPDATE ingest_jobs SET lease_expires_at=0 WHERE id=?", (exhausted,))
    assert repo.claim_ingest_job(worker_id="worker-d", job_id=exhausted) is None
    terminal = repo.get_ingest_job(user_id=user_id, job_id=exhausted)
    assert terminal and terminal["status"] == "failed"
    assert terminal["current_step"] == "retry_exhausted"

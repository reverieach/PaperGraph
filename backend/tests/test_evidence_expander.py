from __future__ import annotations

from app.domain.document import CanonicalDocument, DocumentBlock, DocumentChunk, DocumentPage, ParseQualityReport
from app.infrastructure.db import Database, run_migrations
from app.repositories.document_repository import DocumentRepository
from app.services.context import DynamicContextBuilder
from app.services.retrieval.evidence_expander import EvidenceExpander
from app.services.retrieval.hybrid import HybridHit


def _database_with_expansion_fixture(tmp_path) -> tuple[DocumentRepository, int, int, str, str]:
    db_path = str(tmp_path / "evidence-expansion.db")
    run_migrations(db_path)
    with Database(db_path).transaction() as conn:
        user_id = int(
            conn.execute(
                "INSERT INTO auth_users(username,password_hash,status,created_at,updated_at) VALUES('expand-user','x','active',1,1)"
            ).lastrowid
        )
        paper_id = int(
            conn.execute(
                "INSERT INTO papers(user_id,title,created_at,updated_at) VALUES(?,?,1,1)",
                (user_id, "Evidence expansion paper"),
            ).lastrowid
        )
    repository = DocumentRepository(db_path)
    version_id = repository.create_or_get_version(
        user_id=user_id,
        paper_id=paper_id,
        file_hash="fixture-hash",
        file_size=100,
        parser_id="test",
        parser_version="1",
        parser_config_hash="1",
        chunker_version="parent-child-v1",
    )
    page = DocumentPage(page_index=2, text="Parent context. Before. Anchor fact. After.")
    block = DocumentBlock(
        block_uid="blk-1",
        page_index=2,
        block_order=0,
        block_type="paragraph",
        section_path=["2 Method"],
        text=page.text,
    )
    document = CanonicalDocument(
        document_version_id=version_id,
        user_id=user_id,
        paper_id=paper_id,
        file_hash="fixture-hash",
        parser_id="test",
        parser_version="1",
        pages=[page],
        blocks=[block],
        quality=ParseQualityReport(page_count=1, non_empty_page_count=1, block_count=1, score=1.0),
    )
    parent_uid = f"{version_id}:parent"
    chunks = [
        DocumentChunk(
            chunk_uid=parent_uid,
            document_version_id=version_id,
            user_id=user_id,
            paper_id=paper_id,
            parent_chunk_uid=None,
            level="parent",
            ordinal=0,
            content_type="paragraph",
            section_path=["2 Method"],
            page_start=2,
            page_end=2,
            block_uids=["blk-1"],
            display_text="Parent context establishes the method definition and qualification.",
            embedding_text="Parent context establishes the method definition and qualification.",
            sparse_text="parent context method definition qualification",
            text_hash="parent",
            token_count=10,
            chunker_version="parent-child-v1",
        )
    ]
    for ordinal, text in enumerate(("Before qualification.", "Anchor fact for answer.", "After limitation."), 1):
        chunks.append(
            DocumentChunk(
                chunk_uid=f"{version_id}:child-{ordinal}",
                document_version_id=version_id,
                user_id=user_id,
                paper_id=paper_id,
                parent_chunk_uid=parent_uid,
                level="child",
                ordinal=ordinal,
                content_type="paragraph",
                section_path=["2 Method"],
                page_start=2,
                page_end=2,
                block_uids=["blk-1"],
                display_text=text,
                embedding_text=text,
                sparse_text=text.casefold(),
                text_hash=f"child-{ordinal}",
                token_count=5,
                chunker_version="parent-child-v1",
            )
        )
    repository.persist_document(document, chunks)
    assert repository.activate_version(user_id=user_id, paper_id=paper_id, document_version_id=version_id)
    return repository, user_id, paper_id, version_id, f"{version_id}:child-2"


def test_repository_expands_only_active_owned_parent_and_neighbours(tmp_path) -> None:
    repository, user_id, _paper_id, version_id, anchor_uid = _database_with_expansion_fixture(tmp_path)

    rows = repository.expand_active_evidence_chunks(
        user_id=user_id,
        anchor_chunk_uids=[anchor_uid],
        neighbor_radius=1,
    )
    assert {row["chunk_uid"] for row in rows} == {
        f"{version_id}:parent",
        f"{version_id}:child-1",
        f"{version_id}:child-2",
        f"{version_id}:child-3",
    }
    assert repository.expand_active_evidence_chunks(
        user_id=user_id + 1,
        anchor_chunk_uids=[anchor_uid],
    ) == []


def test_expander_preserves_anchor_then_bounded_context_for_citation(tmp_path) -> None:
    repository, user_id, paper_id, version_id, anchor_uid = _database_with_expansion_fixture(tmp_path)
    hit = HybridHit(
        chunk_uid=anchor_uid,
        paper_id=paper_id,
        document_version_id=version_id,
        content_type="paragraph",
        display_text="Anchor fact for answer.",
        section_path=["2 Method"],
        page_start=2,
        page_end=2,
        rrf_score=0.9,
    )
    expanded = EvidenceExpander(repository).expand(user_id=user_id, hits=[hit])

    assert [row["expansion_role"] for row in expanded.chunks] == [
        "anchor",
        "parent_context",
        "neighbor_context",
        "neighbor_context",
    ]
    assert expanded.anchor_count == 1
    assert expanded.parent_count == 1
    assert expanded.neighbor_count == 2
    package = DynamicContextBuilder(max_tokens=900, max_evidence=6).build(
        retrieved_chunks=expanded.chunks,
        query="解释这个方法",
    )
    assert {evidence.chunk_uid for evidence in package.evidence} == {
        row["chunk_uid"] for row in expanded.chunks
    }
    assert any(
        "expansion:parent_context" in item.inclusion_reason
        for item in package.items
        if item.source_type == "retrieved_chunk"
    )

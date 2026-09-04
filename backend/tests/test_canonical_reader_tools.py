"""Canonical Reader tool, context re-entry, and scope regression tests."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.agents.paper_analysis_agent import PaperAnalysisAgent
from app.agents.support.canonical_reader_tools import build_canonical_reader_tools
from app.agents.support.reader_ctx import ReaderCtx
from app.core.paper import Paper
from app.domain.document import (
    CanonicalDocument,
    DocumentBlock,
    DocumentChunk,
    DocumentPage,
    ParseQualityReport,
)
from app.infrastructure.db import Database, run_migrations
from app.repositories.document_repository import DocumentRepository
from app.services.citation import EvidenceRegistry
from app.services.context import DynamicContextBuilder
from app.services.reader.paper_reader_service import PaperReaderService


def _chunk(
    *,
    uid: str,
    version_id: str,
    user_id: int,
    paper_id: int,
    level: str,
    ordinal: int,
    content_type: str,
    section_path: list[str],
    page: int,
    text: str,
    parent_uid: str | None = None,
) -> DocumentChunk:
    return DocumentChunk(
        chunk_uid=uid,
        document_version_id=version_id,
        user_id=user_id,
        paper_id=paper_id,
        parent_chunk_uid=parent_uid,
        level=level,  # type: ignore[arg-type]
        ordinal=ordinal,
        content_type=content_type,
        section_path=section_path,
        page_start=page,
        page_end=page,
        block_uids=[f"{version_id}:block:{ordinal}"],
        display_text=text,
        embedding_text=f"Canonical Tool Paper / {' > '.join(section_path)} / {text}",
        sparse_text=text.lower(),
        text_hash=f"hash:{uid}",
        token_count=max(1, len(text.split())),
        chunker_version="test-canonical-tools-v1",
    )


def _canonical_scope(tmp_path) -> tuple[str, int, int, int, str]:
    path = str(tmp_path / "canonical-tools.db")
    run_migrations(path)
    with Database(path).transaction() as conn:
        user_id = conn.execute(
            """
            INSERT INTO auth_users(username,password_hash,status,created_at,updated_at)
            VALUES('canonical-user','hash','active',1,1)
            """
        ).lastrowid
        other_user_id = conn.execute(
            """
            INSERT INTO auth_users(username,password_hash,status,created_at,updated_at)
            VALUES('other-user','hash','active',1,1)
            """
        ).lastrowid
        paper_id = conn.execute(
            "INSERT INTO papers(user_id,title,created_at,updated_at) VALUES(?,?,1,1)",
            (user_id, "Canonical Tool Paper"),
        ).lastrowid
    assert user_id and other_user_id and paper_id

    repo = DocumentRepository(path)
    version_id = repo.create_or_get_version(
        user_id=int(user_id),
        paper_id=int(paper_id),
        file_hash="canonical-tool-pdf-hash",
        file_size=512,
        parser_id="test",
        parser_version="1",
        parser_config_hash="canonical-tool-config",
        chunker_version="test-canonical-tools-v1",
    )
    method_text = "The method combines hybrid retrieval with reranking for evidence grounded answers."
    table_text = "Table 3 Ablation results: hybrid retrieval improves Recall@10 from 0.71 to 0.92."
    document = CanonicalDocument(
        document_version_id=version_id,
        user_id=int(user_id),
        paper_id=int(paper_id),
        file_hash="canonical-tool-pdf-hash",
        parser_id="test",
        parser_version="1",
        pages=[
            DocumentPage(page_index=1, text="1 Introduction"),
            DocumentPage(page_index=2, text=method_text),
            DocumentPage(page_index=3, text=table_text, table_count=1),
        ],
        blocks=[
            DocumentBlock(f"{version_id}:block:0", 1, 0, "heading", ["1 Introduction"], "1 Introduction"),
            DocumentBlock(f"{version_id}:block:1", 2, 0, "paragraph", ["2 Method"], method_text),
            DocumentBlock(
                f"{version_id}:block:2",
                3,
                0,
                "table",
                ["3 Experiments"],
                table_text,
                table_html="<table><tr><td>Recall@10</td><td>0.92</td></tr></table>",
            ),
        ],
        quality=ParseQualityReport(page_count=3, non_empty_page_count=3, block_count=3, score=0.98),
    )
    method_parent = _chunk(
        uid=f"{version_id}:method-parent",
        version_id=version_id,
        user_id=int(user_id),
        paper_id=int(paper_id),
        level="parent",
        ordinal=1,
        content_type="paragraph",
        section_path=["2 Method"],
        page=2,
        text=method_text,
    )
    method_child = _chunk(
        uid=f"{version_id}:method-child",
        version_id=version_id,
        user_id=int(user_id),
        paper_id=int(paper_id),
        level="child",
        ordinal=2,
        content_type="paragraph",
        section_path=["2 Method"],
        page=2,
        text=method_text,
        parent_uid=method_parent.chunk_uid,
    )
    experiment_parent = _chunk(
        uid=f"{version_id}:experiment-parent",
        version_id=version_id,
        user_id=int(user_id),
        paper_id=int(paper_id),
        level="parent",
        ordinal=3,
        content_type="paragraph",
        section_path=["3 Experiments"],
        page=3,
        text=table_text,
    )
    table_child = _chunk(
        uid=f"{version_id}:table-child",
        version_id=version_id,
        user_id=int(user_id),
        paper_id=int(paper_id),
        level="child",
        ordinal=4,
        content_type="table",
        section_path=["3 Experiments"],
        page=3,
        text=table_text,
        parent_uid=experiment_parent.chunk_uid,
    )
    repo.persist_document(document, [method_parent, method_child, experiment_parent, table_child])
    assert repo.activate_version(
        user_id=int(user_id), paper_id=int(paper_id), document_version_id=version_id
    )
    return path, int(user_id), int(other_user_id), int(paper_id), version_id


def test_canonical_tools_are_json_bounded_scoped_and_hydrate_evidence(tmp_path) -> None:
    path, user_id, other_user_id, paper_id, version_id = _canonical_scope(tmp_path)
    outline, section, table, search = build_canonical_reader_tools(
        db_path=path,
        user_id=user_id,
        paper_id=paper_id,
        document_version_id=version_id,
    )

    outline_payload = json.loads(outline.run({"max_sections": 1}))
    assert outline_payload["status"] == "ok"
    assert outline_payload["truncated"] is True
    assert outline_payload["source_boundary"].endswith("non_citable")

    section_result = section.run_with_timing({"section_ref": "Method"})
    section_payload = json.loads(section_result.text)
    assert section_result.status == "SUCCESS"
    assert section_payload["chunks"][0]["page_start"] == 2
    hydrated = section.context_chunks_from_result(section_result.text)
    assert [row["chunk_uid"] for row in hydrated] == [f"{version_id}:method-parent"]

    table_payload = json.loads(table.run({"table_ref": "3"}))
    assert table_payload["status"] == "ok"
    assert table_payload["chunk"]["chunk_uid"] == f"{version_id}:table-child"

    search_payload = json.loads(search.run({"query": "hybrid retrieval", "max_results": 4}))
    assert search_payload["status"] == "ok"
    assert search_payload["matches"]
    assert any(match["page_start"] == 2 for match in search_payload["matches"])

    oversized = json.loads(outline._render_payload({"tool": "x", "content": "x" * 20_000}))
    assert oversized["truncated"] is True
    assert oversized["status"] == "partial"

    foreign_section = build_canonical_reader_tools(
        db_path=path,
        user_id=other_user_id,
        paper_id=paper_id,
        document_version_id=version_id,
    )[1].run_with_timing({"section_ref": "Method"})
    assert foreign_section.status == "ERROR"
    assert foreign_section.error_info == {"code": "CANONICAL_VERSION_UNAVAILABLE"}


def test_tool_context_reentry_registers_only_active_scoped_evidence(tmp_path) -> None:
    path, user_id, _, paper_id, version_id = _canonical_scope(tmp_path)
    section = build_canonical_reader_tools(
        db_path=path,
        user_id=user_id,
        paper_id=paper_id,
        document_version_id=version_id,
    )[1]
    initial = DynamicContextBuilder(max_tokens=500).build(
        retrieved_chunks=[
            {
                "chunk_uid": f"{version_id}:table-child",
                "paper_id": paper_id,
                "document_version_id": version_id,
                "display_text": "Table 3 canonical initial evidence.",
                "page_start": 3,
                "page_end": 3,
            }
        ],
        query="What improves Recall?",
    )
    registry = EvidenceRegistry.from_context_package(initial, user_id=user_id, paper_id=paper_id)
    result = section.run({"section_ref": "Method"})

    agent = PaperAnalysisAgent.__new__(PaperAnalysisAgent)
    ctx = ReaderCtx(
        snap={
            "paper_id": paper_id,
            "_canonical_document_version_id": version_id,
            "_evidence_registry": registry,
            "_tool_context_token_budget": 700,
        },
        user_message="How does the method ground answers?",
    )
    reentered = agent._reenter_canonical_tool_result(ctx, section, result)

    assert "[E2]" in reentered
    assert registry.evidence_ids == ("E1", "E2")
    assert registry.get("E2").chunk_uid == f"{version_id}:method-parent"
    assert ctx.tool_context_tokens_used > 0

    # Replaying the same tool result cannot duplicate an evidence marker.
    replay = agent._reenter_canonical_tool_result(ctx, section, result)
    assert "[E3]" not in replay
    assert registry.evidence_ids == ("E1", "E2")


def test_agent_selects_canonical_tools_and_never_broadens_malformed_scope(tmp_path) -> None:
    path, user_id, _, paper_id, version_id = _canonical_scope(tmp_path)
    agent = PaperAnalysisAgent.__new__(PaperAnalysisAgent)
    canonical_names = [
        tool.name
        for tool in agent._build_reader_tools(
            ReaderCtx(
                snap={
                    "paper_id": paper_id,
                    "_canonical_db_path": path,
                    "_canonical_user_id": user_id,
                    "_canonical_document_version_id": version_id,
                }
            )
        )
    ]
    assert canonical_names == [
        "reader_paper_lookup",
        "reader_reference_lookup",
        "reader_get_outline",
        "reader_get_section",
        "reader_get_table",
        "reader_search_document",
    ]

    malformed_names = [
        tool.name
        for tool in agent._build_reader_tools(
            ReaderCtx(
                snap={
                    "paper_id": paper_id,
                    "_context_mode": "canonical_degraded",
                }
            )
        )
    ]
    assert malformed_names == ["reader_paper_lookup", "reader_reference_lookup"]


def test_reader_active_canonical_version_never_loads_legacy_pdf_context(tmp_path, monkeypatch) -> None:
    path, user_id, _, paper_id, version_id = _canonical_scope(tmp_path)
    paper = Paper(id=paper_id, title="Canonical Tool Paper", abstract="Short metadata abstract")
    db = SimpleNamespace(
        db_path=path,
        get_paper_by_id=lambda requested_id, *, user_id, expected_user_id=user_id: paper
        if int(requested_id) == paper_id and int(user_id) == int(expected_user_id)
        else None,
    )
    service = PaperReaderService(db=db, agent=object())

    def legacy_path_must_not_run(*args, **kwargs):
        raise AssertionError("canonical Reader must not load the legacy PDF cache")

    import app.services.reader.paper_reader_context as reader_context

    monkeypatch.setattr(reader_context, "build_reader_context_for_paper", legacy_path_must_not_run)
    prepared = asyncio.run(
        service._build_reader_context(
            paper_id,
            user_id=user_id,
            user_message="How does hybrid retrieval work?",
        )
    )
    assert prepared.context_mode == "hybrid_rag_v2"
    assert prepared.document_version_id == version_id
    assert prepared.pdf_ref_text == ""
    assert prepared.pdf_pages == []
    snap = service._build_reader_snap(prepared, user_id=user_id, paper_id=paper_id)
    assert snap["_canonical_document_version_id"] == version_id
    assert "_pdf_merged_for_structure" not in snap
    assert "_pdf_pages" not in snap
    assert "_pdf_abspath" not in snap


def test_reader_active_version_lookup_failure_does_not_fall_back_to_legacy_full_pdf(tmp_path, monkeypatch) -> None:
    path, user_id, _, paper_id, _ = _canonical_scope(tmp_path)
    paper = Paper(id=paper_id, title="Canonical Tool Paper")
    db = SimpleNamespace(
        db_path=path,
        get_paper_by_id=lambda requested_id, *, user_id, expected_user_id=user_id: paper
        if int(requested_id) == paper_id and int(user_id) == int(expected_user_id)
        else None,
    )
    service = PaperReaderService(db=db, agent=object())

    import app.services.reader.paper_reader_context as reader_context

    monkeypatch.setattr(
        reader_context,
        "build_reader_context_for_paper",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy path used")),
    )
    monkeypatch.setattr(
        DocumentRepository,
        "get_active_version",
        lambda self, *, user_id, paper_id: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )
    prepared = asyncio.run(
        service._build_reader_context(
            paper_id,
            user_id=user_id,
            user_message="What is the method?",
        )
    )
    assert prepared.context_mode == "canonical_degraded"
    assert prepared.document_version_id is None
    assert prepared.pdf_ref_text == ""

from __future__ import annotations

from app.domain.document import CanonicalDocument, DocumentBlock, DocumentPage
from app.services.ingest.chunking import ChunkingConfig, HierarchicalChunker, normalize_sparse_text


def _document() -> CanonicalDocument:
    blocks = [
        DocumentBlock("b1", 1, 0, "heading", ["1 Method"], "1 Method"),
        DocumentBlock("b2", 1, 1, "paragraph", ["1 Method"], "We introduce a retrieval method with dense and sparse evidence."),
        DocumentBlock("b3", 2, 0, "table", ["1 Method"], "Results", table_html="<table><tr><td>R@5</td><td>0.8</td></tr></table>"),
        DocumentBlock("b4", 3, 0, "heading", ["2 Analysis"], "2 Analysis"),
        DocumentBlock("b5", 3, 1, "paragraph", ["2 Analysis"], "Long analysis " * 250),
    ]
    return CanonicalDocument(
        document_version_id="dv-test",
        user_id=1,
        paper_id=2,
        file_hash="hash",
        parser_id="test",
        parser_version="1",
        pages=[DocumentPage(page_index=1), DocumentPage(page_index=2), DocumentPage(page_index=3)],
        blocks=blocks,
    )


def test_hierarchical_chunker_is_section_page_aware_and_deterministic() -> None:
    config = ChunkingConfig(parent_max_tokens=120, child_max_tokens=45, overlap_tokens=6, min_tokens=3)
    chunker = HierarchicalChunker(config)
    first = chunker.chunk_document(_document(), paper_title="Paper")
    second = chunker.chunk_document(_document(), paper_title="Paper")
    assert first and [x.chunk_uid for x in first] == [x.chunk_uid for x in second]
    parents = [x for x in first if x.level == "parent"]
    children = [x for x in first if x.level == "child"]
    assert parents and children
    assert all(child.parent_chunk_uid in {p.chunk_uid for p in parents} for child in children)
    assert all(child.token_count <= config.child_max_tokens + 2 for child in children)
    assert any(chunk.content_type == "table" and "R@5" in chunk.display_text for chunk in first)
    assert any(chunk.page_start == 3 for chunk in first)


def test_sparse_normalizer_adds_cjk_boundaries_and_removes_pdf_noise() -> None:
    sparse = normalize_sparse_text("检索增强-\n生成（RAG）")
    assert "检" in sparse and "索" in sparse
    assert "rag" in sparse
    assert "-" not in sparse


def test_large_table_is_split_by_rows_with_caption_and_header_repeated() -> None:
    rows = "".join(
        f"<tr><td>model-{index}</td><td>{index / 10:.1f}</td><td>long capability value {index}</td></tr>"
        for index in range(14)
    )
    table = DocumentBlock(
        "table-large",
        2,
        0,
        "table",
        ["2 Results"],
        "",
        table_html=(
            "<table><tr><td>Model</td><td>Score</td><td>Capability</td></tr>"
            + rows
            + "</table>"
        ),
        provenance={"caption_text": "Table 7 Overview of evaluated model capabilities."},
    )
    document = CanonicalDocument(
        document_version_id="dv-large-table",
        user_id=1,
        paper_id=2,
        file_hash="hash",
        parser_id="test",
        parser_version="1",
        pages=[DocumentPage(page_index=2)],
        blocks=[table],
    )
    config = ChunkingConfig(parent_max_tokens=120, child_max_tokens=52, overlap_tokens=4, min_tokens=3)
    chunks = HierarchicalChunker(config).chunk_document(document, paper_title="Table Paper")
    children = [chunk for chunk in chunks if chunk.level == "child" and chunk.content_type == "table"]

    assert len(children) > 1
    assert all("Caption: Table 7 Overview" in child.display_text for child in children)
    assert all("Columns: Model | Score | Capability" in child.display_text for child in children)
    assert all("<td>" not in child.display_text for child in children)
    assert all(child.token_count <= config.child_max_tokens + 2 for child in children)
    joined = "\n".join(child.display_text for child in children)
    assert "model-0" in joined and "model-13" in joined


def test_table_is_an_independent_chunk_even_when_it_fits_the_budget() -> None:
    table = DocumentBlock(
        "table-small",
        1,
        1,
        "table",
        ["1 Results"],
        "",
        table_html="<table><tr><td>Metric</td><td>Value</td></tr><tr><td>Recall@10</td><td>0.92</td></tr></table>",
        provenance={"caption_text": "Table 2 Main results."},
    )
    document = CanonicalDocument(
        document_version_id="dv-table-boundary",
        user_id=1,
        paper_id=2,
        file_hash="hash",
        parser_id="test",
        parser_version="1",
        pages=[DocumentPage(page_index=1)],
        blocks=[
            DocumentBlock(
                "prose",
                1,
                0,
                "paragraph",
                ["1 Results"],
                "This preceding prose must not be included in the table citation.",
            ),
            table,
        ],
    )
    chunks = HierarchicalChunker(
        ChunkingConfig(parent_max_tokens=160, child_max_tokens=80, min_tokens=3)
    ).chunk_document(document, paper_title="Table Paper")
    table_children = [chunk for chunk in chunks if chunk.level == "child" and chunk.content_type == "table"]

    assert len(table_children) == 1
    assert "Table 2 Main results" in table_children[0].display_text
    assert "Recall@10" in table_children[0].display_text
    assert "preceding prose" not in table_children[0].display_text

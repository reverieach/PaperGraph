from __future__ import annotations

import fitz
from pathlib import Path

from app.domain.document import CanonicalDocument, DocumentPage
from app.services.ingest.canonicalizer import canonicalize_docling
from app.services.ingest.parsers import (
    DoclingParser,
    PyMuPDFParser,
    _result_flag,
    resolve_pdf_ocr_mode,
    staged_docling_input,
)
from app.services.ingest.quality import ParseQualityGate
from app.services.text_normalization import normalize_pdf_layout_text


def _docling_fixture() -> dict:
    return {
        "pages": {"1": {"page_no": 1, "size": {"width": 612, "height": 792}}},
        "texts": [
            {
                "self_ref": "#/texts/0",
                "label": "section_header",
                "text": "1 Introduction",
                "level": 1,
                "prov": [{"page_no": 1, "bbox": {"l": 1, "t": 2, "r": 3, "b": 4}}],
            },
            {
                "self_ref": "#/texts/1",
                "label": "text",
                "text": "Retrieval augmented generation grounds answers in evidence.",
                "prov": [{"page_no": 1, "bbox": {"l": 1, "t": 5, "r": 3, "b": 20}}],
            },
        ],
        "tables": [
            {
                "self_ref": "#/tables/0",
                "label": "table",
                "prov": [{"page_no": 1, "bbox": {"l": 1, "t": 21, "r": 3, "b": 30}}],
                "data": {
                    "table_cells": [
                        {"start_row_offset_idx": 0, "start_col_offset_idx": 0, "text": "Metric"},
                        {"start_row_offset_idx": 0, "start_col_offset_idx": 1, "text": "Value"},
                    ]
                },
            }
        ],
        "body": {
            "children": [
                {"$ref": "#/texts/0"},
                {"$ref": "#/texts/1"},
                {"$ref": "#/tables/0"},
            ]
        },
    }


def test_docling_canonicalizer_preserves_sections_pages_tables_and_provenance() -> None:
    document = canonicalize_docling(
        _docling_fixture(),
        document_version_id="dv-test",
        user_id=1,
        paper_id=2,
        file_hash="hash",
        parser_version="2.0",
    )
    assert isinstance(document, CanonicalDocument)
    assert document.page_count == 1
    assert [block.block_type for block in document.blocks] == ["heading", "paragraph", "table"]
    assert document.blocks[1].section_path == ["1 Introduction"]
    assert document.blocks[2].table_html and "Metric" in document.blocks[2].table_html
    assert document.blocks[1].provenance["prov"][0]["page_no"] == 1
    assert document.pages[0].text.startswith("1 Introduction")


def test_docling_canonicalizer_attaches_a_nearby_visible_table_caption() -> None:
    raw = _docling_fixture()
    raw["texts"].append(
        {
            "self_ref": "#/texts/2",
            # Some Docling exports classify an otherwise clear caption as a
            # list item; canonicalization must not depend on the label alone.
            "label": "list_item",
            "text": "Table 1 Overview of the retrieval model variants.",
            "prov": [{"page_no": 1, "bbox": {"l": 1, "t": 31, "r": 3, "b": 38}}],
        }
    )
    raw["body"]["children"].append({"$ref": "#/texts/2"})

    document = canonicalize_docling(
        raw,
        document_version_id="dv-caption",
        user_id=1,
        paper_id=2,
        file_hash="hash",
        parser_version="2.0",
    )

    table = next(block for block in document.blocks if block.block_type == "table")
    assert table.provenance["caption_text"] == "Table 1 Overview of the retrieval model variants."
    assert table.provenance["caption_block_uid"]


def test_docling_canonicalizer_attaches_a_preceding_chinese_table_caption() -> None:
    raw = _docling_fixture()
    raw["texts"].append(
        {
            "self_ref": "#/texts/2",
            "label": "text",
            "text": "表1 汉语评测结果",
            "prov": [{"page_no": 1, "bbox": {"l": 1, "t": 20, "r": 3, "b": 21}}],
        }
    )
    # A common PDF order puts the caption directly before the table.
    raw["body"]["children"] = [
        {"$ref": "#/texts/0"},
        {"$ref": "#/texts/1"},
        {"$ref": "#/texts/2"},
        {"$ref": "#/tables/0"},
    ]

    document = canonicalize_docling(
        raw,
        document_version_id="dv-caption-before",
        user_id=1,
        paper_id=2,
        file_hash="hash",
        parser_version="2.0",
    )

    table = next(block for block in document.blocks if block.block_type == "table")
    assert table.provenance["caption_text"] == "表1 汉语评测结果"


def test_pymupdf_parser_emits_page_and_block_provenance(tmp_path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "1 Introduction\nRetrieval augmented generation grounds answers in evidence.")
    doc.save(pdf_path)
    doc.close()

    result = PyMuPDFParser().parse(
        str(pdf_path),
        document_version_id="dv-test",
        user_id=1,
        paper_id=2,
    )
    assert result.document is not None
    assert result.document.pages[0].page_index == 1
    assert result.document.blocks
    assert result.document.blocks[0].provenance["parser"] == "pymupdf_fallback"


def test_parse_quality_gate_distinguishes_failed_and_degraded_documents() -> None:
    empty = CanonicalDocument(
        document_version_id="empty",
        user_id=1,
        paper_id=1,
        file_hash="x",
        parser_id="test",
        parser_version="1",
    )
    gate = ParseQualityGate(min_text_chars=20)
    failed = gate.evaluate(empty)
    assert not failed.accepted and failed.status == "failed"

    partial = CanonicalDocument(
        document_version_id="partial",
        user_id=1,
        paper_id=1,
        file_hash="x",
        parser_id="test",
        parser_version="1",
    )
    partial.pages = [
        DocumentPage(page_index=1, text="enough text for a gate"),
        DocumentPage(page_index=2, text=""),
    ]
    partial.blocks = []
    partial.quality.page_count = 2
    partial.quality.non_empty_page_count = 1
    partial.quality.block_count = 1
    partial.quality.text_char_count = 22
    partial.quality.pages_with_provenance = 1
    partial.quality.score = 0.5
    degraded = ParseQualityGate(min_text_chars=20).evaluate(partial)
    assert degraded.accepted and degraded.status == "degraded"


def test_docling_result_predicates_are_called_instead_of_treating_methods_as_true() -> None:
    class Result:
        def has_parse_errors(self) -> bool:
            return False

        def has_inference_errors(self) -> bool:
            return True

    result = Result()
    assert not _result_flag(result, "has_parse_errors")
    assert _result_flag(result, "has_inference_errors")


def test_docling_ascii_path_is_used_without_a_copy(tmp_path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4\\nminimal fixture\\n")

    with staged_docling_input(source, file_hash="a" * 64) as (prepared, staged):
        assert prepared == source.resolve()
        assert staged is False
        assert prepared.read_bytes() == source.read_bytes()


def test_docling_non_ascii_path_is_staged_and_cleaned_up(tmp_path) -> None:
    source_dir = tmp_path / "中文输入"
    source_dir.mkdir()
    source = source_dir / "论文.pdf"
    source.write_bytes(b"%PDF-1.4\\nminimal fixture\\n")
    staging_root = tmp_path / "ascii_docling_stage"

    with staged_docling_input(
        source,
        file_hash="b" * 64,
        staging_root=str(staging_root),
    ) as (prepared, staged):
        assert staged is True
        assert str(prepared).isascii()
        assert prepared.name == f"{'b' * 64}.pdf"
        assert prepared.read_bytes() == source.read_bytes()
        prepared_path = Path(prepared)
        assert prepared_path.is_file()

    assert not prepared_path.exists()
    assert source.is_file()


def test_pdf_layout_normalization_joins_only_artificial_cjk_spacing() -> None:
    assert normalize_pdf_layout_text("检 索 增 强 生 成") == "检索增强生成"
    assert normalize_pdf_layout_text("中文 ， 英文 words remain separate") == "中文, 英文 words remain separate"
    assert normalize_pdf_layout_text("qwen3-rerank stays intact") == "qwen3-rerank stays intact"


def test_docling_canonicalizer_normalizes_character_spaced_cjk_text() -> None:
    raw = _docling_fixture()
    raw["texts"][0]["text"] = "检 索 增 强 生 成"
    raw["texts"][1]["text"] = "中 文 证 据 与 English evidence"
    document = canonicalize_docling(
        raw,
        document_version_id="dv-cjk",
        user_id=1,
        paper_id=2,
        file_hash="hash",
        parser_version="2.0",
    )
    assert document.blocks[0].text == "检索增强生成"
    assert document.blocks[1].text == "中文证据与 English evidence"


def test_auto_ocr_skips_clearly_native_text_and_keeps_scanned_like_pdf_safe(tmp_path) -> None:
    native_path = tmp_path / "native.pdf"
    native = fitz.open()
    for _ in range(3):
        page = native.new_page()
        page.insert_textbox(
            fitz.Rect(72, 72, 540, 700),
            "Native PDF text " * 120,
            fontsize=10,
        )
    native.save(native_path)
    native.close()

    use_ocr, native_probe = resolve_pdf_ocr_mode(native_path, mode="auto")
    assert use_ocr is False
    assert native_probe["decision"] == "skip_ocr_native_text"
    assert native_probe["native_text_page_count"] == 3

    scanned_like_path = tmp_path / "scanned_like.pdf"
    scanned_like = fitz.open()
    scanned_like.new_page()
    scanned_like.save(scanned_like_path)
    scanned_like.close()

    use_ocr, scanned_probe = resolve_pdf_ocr_mode(scanned_like_path, mode="auto")
    assert use_ocr is True
    assert scanned_probe["decision"] == "use_ocr_low_native_text"
    assert scanned_probe["native_text_page_count"] == 0

    assert DoclingParser().ocr_mode == "always"
    assert DoclingParser(ocr_mode="auto").ocr_mode == "auto"


def test_auto_ocr_detects_a_real_image_only_pdf(tmp_path) -> None:
    """Use rasterized page content rather than an empty-page proxy for OCR mode."""

    source = fitz.open()
    source_page = source.new_page(width=612, height=792)
    remaining = source_page.insert_textbox(
        fitz.Rect(72, 72, 540, 700),
        "Hybrid retrieval combines lexical and dense evidence.\n" * 12,
        fontsize=12,
    )
    assert remaining >= 0
    image = source_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
    scanned_path = tmp_path / "image-only-scan.pdf"
    scanned = fitz.open()
    scanned_page = scanned.new_page(width=612, height=792)
    scanned_page.insert_image(scanned_page.rect, pixmap=image)
    scanned.save(scanned_path)
    scanned.close()
    source.close()

    with fitz.open(scanned_path) as document:
        page = document.load_page(0)
        assert not (page.get_text("text") or "").strip()
        assert len(page.get_images(full=True) or []) == 1

    use_ocr, probe = resolve_pdf_ocr_mode(scanned_path, mode="auto")
    assert use_ocr is True
    assert probe["decision"] == "use_ocr_low_native_text"
    assert probe["native_text_page_count"] == 0


def test_encrypted_pdf_is_rejected_before_docling_conversion(tmp_path) -> None:
    encrypted_path = tmp_path / "encrypted.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Encrypted parser fixture")
    document.save(
        encrypted_path,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner-password",
        user_pw="user-password",
    )
    document.close()

    do_ocr, probe = resolve_pdf_ocr_mode(encrypted_path, mode="auto")
    assert do_ocr is False
    assert probe["decision"] == "requires_password"
    assert probe["requires_password"] is True

    parsed = DoclingParser(ocr_mode="auto").parse(
        str(encrypted_path),
        document_version_id="dv-encrypted",
        user_id=1,
        paper_id=2,
    )
    assert parsed.document is None
    assert parsed.error_code == "PDF_ENCRYPTED"


def test_invalid_pdf_is_rejected_before_docling_conversion(tmp_path) -> None:
    invalid_path = tmp_path / "not-a-pdf.pdf"
    invalid_path.write_bytes(b"not a PDF fixture")

    do_ocr, probe = resolve_pdf_ocr_mode(invalid_path, mode="auto")
    assert do_ocr is False
    assert probe["decision"] == "invalid_pdf"
    assert probe["invalid_pdf"] is True

    parsed = DoclingParser(ocr_mode="auto").parse(
        str(invalid_path),
        document_version_id="dv-invalid",
        user_id=1,
        paper_id=2,
    )
    assert parsed.document is None
    assert parsed.error_code == "PDF_INVALID"

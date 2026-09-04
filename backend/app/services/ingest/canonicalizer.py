"""Convert Docling's exported JSON into project-owned domain objects."""

from __future__ import annotations

import html
import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any, cast

from ...domain.document import (
    CanonicalDocument,
    DocumentBlock,
    DocumentPage,
    ParseQualityReport,
    stable_uid,
)
from ..text_normalization import normalize_pdf_layout_text


_SKIP_LABELS = {"page_header", "page_footer"}
_REFERENCE_LABELS = {"reference", "bib_entry"}
_HEADING_LABELS = {"section_header", "title", "document_index"}
_FORMULA_LABELS = {"formula", "equation"}
_CAPTION_LABELS = {"caption", "table_caption", "figure_caption"}
_LIST_LABELS = {"list_item", "list"}
_TABLE_CAPTION_RE = re.compile(
    r"^\s*(?:table|表)\s*(?:\d+|[一二三四五六七八九十]+)", re.IGNORECASE
)


def _ref_key(ref: Any) -> str:
    if isinstance(ref, dict):
        return str(ref.get("$ref") or "")
    return str(ref or "")


def _provenance(item: dict[str, Any]) -> tuple[int | None, list[float] | None]:
    prov = item.get("prov") or []
    if not isinstance(prov, list) or not prov:
        return None, None
    first = prov[0] if isinstance(prov[0], dict) else {}
    page_no: int | None
    raw_page_no = first.get("page_no")
    try:
        page_no = int(raw_page_no) if raw_page_no is not None else None
    except (TypeError, ValueError):
        page_no = None
    bbox = first.get("bbox") if isinstance(first, dict) else None
    if isinstance(bbox, dict):
        values = [bbox.get(key) for key in ("l", "t", "r", "b")]
        if all(isinstance(value, (int, float)) for value in values):
            return page_no, [float(cast(int | float, value)) for value in values]
    return page_no, None


def _item_text(item: dict[str, Any]) -> str:
    text = str(item.get("text") or item.get("orig") or "").strip()
    if text:
        return normalize_pdf_layout_text(text)
    data = item.get("data") or {}
    cells = data.get("table_cells") if isinstance(data, dict) else None
    if isinstance(cells, list):
        values = [
            normalize_pdf_layout_text(str(cell.get("text") or "").strip())
            for cell in cells
            if isinstance(cell, dict)
        ]
        return " | ".join(value for value in values if value)
    return ""


def _table_html(item: dict[str, Any]) -> str | None:
    data = item.get("data") or {}
    cells = data.get("table_cells") if isinstance(data, dict) else None
    if not isinstance(cells, list) or not cells:
        return None
    rows: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        try:
            row = int(cell.get("start_row_offset_idx") or 0)
            col = int(cell.get("start_col_offset_idx") or 0)
        except (TypeError, ValueError):
            row, col = 0, 0
        text = html.escape(normalize_pdf_layout_text(str(cell.get("text") or "").strip()))
        rows[row].append((col, text))
    if not rows:
        return None
    parts = ["<table>"]
    for row_index in sorted(rows):
        parts.append("<tr>")
        for _, text in sorted(rows[row_index]):
            parts.append(f"<td>{text}</td>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def _resolve_item(raw: dict[str, Any], ref: str) -> dict[str, Any] | None:
    match = re.fullmatch(r"#/(texts|tables|pictures|groups)/(\d+)", ref)
    if not match:
        return None
    collection, index = match.group(1), int(match.group(2))
    values = raw.get(collection) or []
    if not isinstance(values, list) or index >= len(values):
        return None
    item = values[index]
    return item if isinstance(item, dict) else None


def _label_to_type(label: str) -> str:
    label = label.lower().strip()
    if label in _REFERENCE_LABELS:
        return "reference"
    if label in _HEADING_LABELS:
        return "heading"
    if label in _FORMULA_LABELS:
        return "formula"
    if label in _CAPTION_LABELS:
        return "caption"
    if label in _LIST_LABELS:
        return "list"
    if label in {"table"}:
        return "table"
    if label in {"picture", "figure", "chart"}:
        return "figure"
    return "paragraph"


def _attach_nearby_table_captions(blocks: list[DocumentBlock]) -> None:
    """Attach a nearby visible table caption to its canonical table block.

    Docling usually exposes a table caption as a separate item and, for some
    PDFs, labels it as a list/paragraph rather than ``table_caption``.  The
    table's own rows then lose the natural-language identifier a user asks
    about (for example, ``Table 1 Overview of ...``).  Keep the original
    caption block intact for reading order, and store a bounded copy only as
    table provenance so the chunker can repeat it in table retrieval chunks.
    """

    for index, table in enumerate(blocks):
        if table.block_type != "table" or table.provenance.get("caption_text"):
            continue
        # The reading order emitted by layout models is not universally
        # caption-before-table or caption-after-table.  Prefer following
        # candidates (the common Docling order we observed), then inspect a
        # bounded preceding window on the same page.  The explicit caption
        # prefix prevents ordinary nearby prose from being copied into table
        # retrieval chunks.
        candidate_indexes = [
            *range(index + 1, min(len(blocks), index + 5)),
            *range(index - 1, max(-1, index - 4), -1),
        ]
        for candidate_index in candidate_indexes:
            candidate = blocks[candidate_index]
            if candidate.page_index != table.page_index:
                continue
            text = normalize_pdf_layout_text(candidate.text)
            if not text or not _TABLE_CAPTION_RE.match(text):
                continue
            table.provenance["caption_text"] = text[:1200]
            table.provenance["caption_block_uid"] = candidate.block_uid
            break


def canonicalize_docling(
    raw: dict[str, Any],
    *,
    document_version_id: str,
    user_id: int,
    paper_id: int,
    file_hash: str,
    parser_version: str,
    metadata: dict[str, Any] | None = None,
) -> CanonicalDocument:
    """Canonicalize exported Docling JSON while preserving provenance."""

    pages_raw = raw.get("pages") or {}
    pages: dict[int, DocumentPage] = {}
    values: Iterable[Any]
    if isinstance(pages_raw, dict):
        values = pages_raw.values()
    elif isinstance(pages_raw, list):
        values = pages_raw
    else:
        values = []
    for item in values:
        if not isinstance(item, dict):
            continue
        raw_page_index = item.get("page_no") or item.get("page_index")
        try:
            page_index = int(raw_page_index) if raw_page_index is not None else 0
        except (TypeError, ValueError):
            continue
        if page_index < 1:
            continue
        size = item.get("size") or {}
        width = size.get("width") if isinstance(size, dict) else None
        height = size.get("height") if isinstance(size, dict) else None
        pages[page_index] = DocumentPage(
            page_index=page_index,
            width=float(width) if isinstance(width, (int, float)) else None,
            height=float(height) if isinstance(height, (int, float)) else None,
        )

    blocks: list[DocumentBlock] = []
    page_text: dict[int, list[str]] = defaultdict(list)
    section_stack: list[str] = []
    visited: set[str] = set()
    order = 0

    root = raw.get("body") or {}
    root_children = root.get("children") if isinstance(root, dict) else []

    def walk(ref: Any) -> None:
        nonlocal order, section_stack
        key = _ref_key(ref)
        if not key or key in visited:
            return
        item = _resolve_item(raw, key)
        if item is None:
            return
        visited.add(key)
        label = str(item.get("label") or "unknown").strip().lower()
        if label == "group" or label == "list" or key.startswith("#/groups/"):
            for child in item.get("children") or []:
                walk(child)
            return
        text = _item_text(item)
        page_index, bbox = _provenance(item)
        if label in _SKIP_LABELS:
            return
        if page_index is None:
            page_index = 1 if pages else None
        if page_index is None or not text and label not in {"table", "picture", "figure"}:
            return
        block_type = _label_to_type(label)
        if block_type == "heading":
            raw_level = item.get("level")
            try:
                level = max(1, int(raw_level)) if raw_level is not None else 1
            except (TypeError, ValueError):
                level = 1
            section_stack = section_stack[: level - 1]
            if text:
                section_stack.append(text)
        section_path = list(section_stack)
        if block_type == "heading" and not section_path and text:
            section_path = [text]
        if text:
            page_text[page_index].append(text)
        block = DocumentBlock(
            block_uid=stable_uid("blk", document_version_id, key, page_index, order),
            page_index=page_index,
            block_order=order,
            block_type=block_type,  # type: ignore[arg-type]
            section_path=section_path,
            text=text,
            markdown=text if block_type != "table" else None,
            table_html=_table_html(item) if block_type == "table" else None,
            formula_latex=text if block_type == "formula" else None,
            bbox=bbox,
            provenance={
                "parser": "docling",
                "self_ref": key,
                "label": label,
                "content_layer": item.get("content_layer"),
                "prov": item.get("prov") or [],
            },
        )
        blocks.append(block)
        order += 1
        # A picture/table may own caption or OCR child text; include those
        # after the parent so reading order stays deterministic.
        for child in item.get("children") or []:
            walk(child)
        for caption in item.get("captions") or []:
            walk(caption)

    if isinstance(root_children, list):
        for ref in root_children:
            walk(ref)
    if not blocks:
        # Some exported documents omit a body tree.  Preserve the text list
        # instead of silently producing an empty document.
        for collection in (raw.get("texts") or [], raw.get("tables") or []):
            if isinstance(collection, list):
                for index, item in enumerate(collection):
                    if isinstance(item, dict):
                        walk(item.get("self_ref") or f"#/texts/{index}")

    _attach_nearby_table_captions(blocks)

    for page_index in sorted(set(pages) | set(page_text)):
        if page_index not in pages:
            pages[page_index] = DocumentPage(page_index=page_index)
        pages[page_index].text = "\n\n".join(page_text.get(page_index, []))

    page_list = [pages[key] for key in sorted(pages)]
    non_empty = sum(bool(page.text.strip()) for page in page_list)
    quality = ParseQualityReport(
        page_count=len(page_list),
        non_empty_page_count=non_empty,
        block_count=len(blocks),
        text_char_count=sum(len(page.text) for page in page_list),
        heading_count=sum(block.block_type == "heading" for block in blocks),
        table_count=sum(block.block_type == "table" for block in blocks),
        formula_count=sum(block.block_type == "formula" for block in blocks),
        figure_count=sum(block.block_type == "figure" for block in blocks),
        reference_count=sum(block.block_type == "reference" for block in blocks),
        pages_with_provenance=sum(bool(blocks_for_page) for blocks_for_page in _blocks_by_page(blocks).values()),
    )
    coverage = non_empty / len(page_list) if page_list else 0.0
    quality.score = min(1.0, 0.35 * coverage + 0.25 * min(1.0, len(blocks) / max(1, len(page_list) * 8)) + 0.4 * min(1.0, quality.text_char_count / 3000))
    return CanonicalDocument(
        document_version_id=document_version_id,
        user_id=int(user_id),
        paper_id=int(paper_id),
        file_hash=file_hash,
        parser_id="docling_standard",
        parser_version=parser_version,
        pages=page_list,
        blocks=blocks,
        metadata=dict(metadata or {}),
        quality=quality,
    )


def _blocks_by_page(blocks: list[DocumentBlock]) -> dict[int, list[DocumentBlock]]:
    grouped: dict[int, list[DocumentBlock]] = defaultdict(list)
    for block in blocks:
        grouped[block.page_index].append(block)
    return grouped

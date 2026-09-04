"""Deterministic section-aware, page-aware parent/child chunking."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

from ...domain.document import CanonicalDocument, DocumentBlock, DocumentChunk, stable_hash, stable_uid
from ..context.token_counter import TokenCounter


# v3 preserves table captions/headers for row-aware splits and keeps every
# table as an independently citeable chunk.  The prior token-slice strategy
# could emit broken HTML fragments and the first v2 pass could still merge a
# small table with preceding prose.
CHUNKER_VERSION = "parent-child-v3"


@dataclass(slots=True)
class ChunkingConfig:
    parent_max_tokens: int = 1200
    child_max_tokens: int = 450
    overlap_tokens: int = 60
    min_tokens: int = 8
    version: str = CHUNKER_VERSION


def normalize_sparse_text(text: str) -> str:
    """Make FTS5 input less hostile to Chinese and PDF punctuation noise."""

    value = str(text or "").replace("\u00ad", "")
    value = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", value)
    value = re.sub(r"[^\w\u4e00-\u9fff]+", " ", value, flags=re.UNICODE)
    # Unicode61 does not segment Chinese words. Spacing CJK characters keeps
    # exact-term lookup useful while the vector branch handles semantics.
    value = re.sub(r"([\u4e00-\u9fff])", r" \1 ", value)
    return " ".join(value.split()).lower()


def _clean_table_cell(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _table_rows(block: DocumentBlock) -> list[list[str]]:
    """Read project-owned table HTML into deterministic text rows.

    ``canonicalizer._table_html`` writes a small escaped ``table/tr/td``
    representation.  Parsing that representation here avoids slicing the
    raw HTML token stream in the middle of tags or cells.  A parser fallback
    keeps hand-built test/legacy blocks readable when no HTML is available.
    """

    rows: list[list[str]] = []
    if str(block.provenance.get("split_strategy") or "") == "table_rows_v1":
        header: list[str] = []
        for line in str(block.text or "").splitlines():
            value = line.strip()
            if value.startswith("Columns:"):
                header = [
                    _clean_table_cell(cell)
                    for cell in value.removeprefix("Columns:").split("|")
                ]
            elif value.startswith("Row:"):
                rows.append(
                    [
                        _clean_table_cell(cell)
                        for cell in value.removeprefix("Row:").split("|")
                    ]
                )
        if header:
            return [header, *rows]
        if rows:
            return rows

    payload = str(block.table_html or "").strip()
    if payload:
        for raw_row in re.findall(r"<tr>(.*?)</tr>", payload, flags=re.IGNORECASE | re.DOTALL):
            cells = [
                _clean_table_cell(cell)
                for cell in re.findall(r"<td>(.*?)</td>", raw_row, flags=re.IGNORECASE | re.DOTALL)
            ]
            if any(cells):
                rows.append(cells)
    if rows:
        return rows
    text = " ".join(str(block.text or "").split())
    if not text:
        return []
    cells = [_clean_table_cell(value) for value in text.split("|")]
    return [cells] if any(cells) else []


def _table_caption(block: DocumentBlock) -> str:
    return " ".join(str(block.provenance.get("caption_text") or "").split())[:1200]


def _table_prefix(block: DocumentBlock, header: list[str]) -> list[str]:
    lines = ["Table"]
    caption = _table_caption(block)
    if caption:
        lines.append(f"Caption: {caption}")
    if header:
        lines.append("Columns: " + " | ".join(cell for cell in header if cell))
    return lines


def _render_table_text(block: DocumentBlock) -> str:
    rows = _table_rows(block)
    if not rows:
        return " ".join(str(block.text or "").split())
    header = rows[0] if len(rows) > 1 else []
    data_rows = rows[1:] if header else rows
    lines = _table_prefix(block, header)
    lines.extend(
        "Row: " + " | ".join(cell for cell in row if cell)
        for row in data_rows
        if any(row)
    )
    return "\n".join(line for line in lines if line.strip()).strip()


def _block_text(block: DocumentBlock) -> str:
    text = (block.text or "").strip()
    if block.block_type == "table":
        return _render_table_text(block)
    return text


def _content_type(blocks: list[DocumentBlock]) -> str:
    kinds = {block.block_type for block in blocks}
    for preferred in ("table", "formula", "figure", "reference", "list", "paragraph"):
        if preferred in kinds:
            return preferred
    return "paragraph"


def _page_range(blocks: list[DocumentBlock]) -> tuple[int, int]:
    pages = sorted({int(block.page_index) for block in blocks if int(block.page_index) > 0})
    if not pages:
        return 1, 1
    return pages[0], pages[-1]


class HierarchicalChunker:
    """Create stable parent and child chunks from canonical blocks.

    The primary boundary is a section and then a block.  Token slicing is only
    used for a block that is larger than the configured budget, which prevents
    tables, captions and formulas from being mixed with unrelated sections.
    """

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self.config = config or ChunkingConfig()
        if self.config.child_max_tokens <= 0 or self.config.parent_max_tokens < self.config.child_max_tokens:
            raise ValueError("parent_max_tokens must be >= child_max_tokens > 0")
        self.tokens = TokenCounter()

    def chunk_document(
        self,
        document: CanonicalDocument,
        *,
        paper_title: str = "",
    ) -> list[DocumentChunk]:
        blocks = [block for block in document.blocks if _block_text(block)]
        if not blocks:
            return []
        groups = self._section_groups(blocks)
        parents: list[tuple[list[DocumentBlock], list[str]]] = []
        for section_path, section_blocks in groups:
            parents.extend(self._split_block_group(section_blocks, self.config.parent_max_tokens, section_path))

        chunks: list[DocumentChunk] = []
        parent_ord = 0
        child_ord = 0
        for parent_blocks, section_path in parents:
            parent_text = self._render(parent_blocks, section_path)
            if self.tokens.count(parent_text) < self.config.min_tokens:
                continue
            parent_uid = stable_uid(
                "chn",
                document.document_version_id,
                "parent",
                parent_ord,
                [block.block_uid for block in parent_blocks],
            )
            page_start, page_end = _page_range(parent_blocks)
            parent_embedding = self._embedding_text(paper_title, section_path, parent_text)
            chunks.append(
                DocumentChunk(
                    chunk_uid=parent_uid,
                    document_version_id=document.document_version_id,
                    user_id=document.user_id,
                    paper_id=document.paper_id,
                    parent_chunk_uid=None,
                    level="parent",
                    ordinal=parent_ord,
                    content_type=_content_type(parent_blocks),
                    section_path=list(section_path),
                    page_start=page_start,
                    page_end=page_end,
                    block_uids=[block.block_uid for block in parent_blocks],
                    display_text=parent_text,
                    embedding_text=parent_embedding,
                    sparse_text=normalize_sparse_text(parent_embedding),
                    text_hash=stable_hash(parent_text),
                    token_count=self.tokens.count(parent_text),
                    chunker_version=self.config.version,
                )
            )
            parent_ord += 1

            child_parts = self._split_block_group(
                parent_blocks,
                self.config.child_max_tokens,
                section_path,
            )
            for child_blocks, child_section_path in child_parts:
                child_text = self._render(child_blocks, child_section_path)
                if self.tokens.count(child_text) < self.config.min_tokens:
                    continue
                child_uid = stable_uid(
                    "chn",
                    document.document_version_id,
                    "child",
                    child_ord,
                    [block.block_uid for block in child_blocks],
                    child_text,
                )
                child_start, child_end = _page_range(child_blocks)
                child_embedding = self._embedding_text(paper_title, child_section_path, child_text)
                chunks.append(
                    DocumentChunk(
                        chunk_uid=child_uid,
                        document_version_id=document.document_version_id,
                        user_id=document.user_id,
                        paper_id=document.paper_id,
                        parent_chunk_uid=parent_uid,
                        level="child",
                        ordinal=child_ord,
                        content_type=_content_type(child_blocks),
                        section_path=list(child_section_path),
                        page_start=child_start,
                        page_end=child_end,
                        block_uids=[block.block_uid for block in child_blocks],
                        display_text=child_text,
                        embedding_text=child_embedding,
                        sparse_text=normalize_sparse_text(child_embedding),
                        text_hash=stable_hash(child_text),
                        token_count=self.tokens.count(child_text),
                        chunker_version=self.config.version,
                    )
                )
                child_ord += 1
        return chunks

    def _section_groups(self, blocks: list[DocumentBlock]) -> list[tuple[list[str], list[DocumentBlock]]]:
        groups: list[tuple[list[str], list[DocumentBlock]]] = []
        current_path: list[str] = []
        current: list[DocumentBlock] = []
        for block in blocks:
            path = list(block.section_path or current_path)
            if current and path != current_path:
                groups.append((current_path, current))
                current = []
            current_path = path
            current.append(block)
        if current:
            groups.append((current_path, current))
        return groups

    def _split_block_group(
        self,
        blocks: list[DocumentBlock],
        max_tokens: int,
        section_path: list[str],
    ) -> list[tuple[list[DocumentBlock], list[str]]]:
        section_prefix_tokens = self.tokens.count(" / ".join(section_path)) if section_path else 0
        effective_max = max(1, int(max_tokens) - section_prefix_tokens - 2)
        parts: list[tuple[list[DocumentBlock], list[str]]] = []
        current: list[DocumentBlock] = []
        current_tokens = 0
        for block in blocks:
            text = _block_text(block)
            block_tokens = self.tokens.count(text)
            # A table is an independently addressable evidence object.  Even
            # when it fits the token budget, do not merge it with preceding
            # prose: doing so dilutes table-only queries and makes table
            # citations include unrelated paragraph text.
            if block.block_type == "table":
                if current:
                    parts.append((current, list(section_path)))
                    current = []
                    current_tokens = 0
                if block_tokens <= effective_max:
                    parts.append(([block], list(section_path)))
                    continue
                table_parts = self._split_table_block(block, effective_max)
                if table_parts:
                    parts.extend(([piece], list(section_path)) for piece in table_parts)
                    continue
            if block_tokens <= effective_max:
                candidate = current + [block]
                candidate_tokens = self.tokens.count(self._render(candidate, section_path))
                if current and candidate_tokens > max_tokens:
                    parts.append((current, list(section_path)))
                    current = []
                    current_tokens = 0
                current.append(block)
                current_tokens = self.tokens.count(self._render(current, section_path))
                continue
            if current:
                parts.append((current, list(section_path)))
                current = []
                current_tokens = 0
            for index, piece in enumerate(self._split_long_text(text, effective_max)):
                synthetic = DocumentBlock(
                    block_uid=f"{block.block_uid}:part:{index}",
                    page_index=block.page_index,
                    block_order=block.block_order,
                    block_type=block.block_type,
                    section_path=list(block.section_path),
                    text=piece,
                    printed_page_label=block.printed_page_label,
                    markdown=piece,
                    # ``piece`` already contains the rendered table/formula
                    # text.  Re-attaching the full original payload here
                    # would duplicate it and violate the child token budget.
                    table_html=None,
                    formula_latex=None,
                    bbox=block.bbox,
                    provenance={**block.provenance, "split_part": index},
                )
                parts.append(([synthetic], list(section_path)))
        if current:
            parts.append((current, list(section_path)))
        return parts

    def _split_table_block(
        self,
        block: DocumentBlock,
        max_tokens: int,
    ) -> list[DocumentBlock]:
        """Split a large table by complete rows while repeating its header.

        Each emitted piece stays independently interpretable for sparse,
        dense and rerank retrieval: it contains the table caption (when
        available), the column header, and only whole data rows.  This is a
        deliberate semantic boundary, unlike generic token slicing which can
        produce fragments such as ``td><td>`` and drop the table identity.
        """

        rows = _table_rows(block)
        if not rows:
            return []
        header = rows[0] if len(rows) > 1 else []
        data_rows = rows[1:] if header else rows
        prefix_lines = _table_prefix(block, header)
        # A pathological caption/header cannot consume the whole table chunk.
        # Keep a deterministic prefix budget so at least one data row remains
        # searchable whenever one exists.
        prefix_budget = max(12, max_tokens - 8)
        prefix = self._clip_to_tokens("\n".join(prefix_lines), prefix_budget)
        parts: list[str] = []
        current_lines = [prefix] if prefix else []
        for row in data_rows or [[]]:
            row_text = "Row: " + " | ".join(cell for cell in row if cell)
            row_text = row_text.strip(" :")
            candidate_lines = [*current_lines, row_text] if row_text else list(current_lines)
            candidate = "\n".join(line for line in candidate_lines if line).strip()
            if len(current_lines) > 1 and row_text and self.tokens.count(candidate) > max_tokens:
                parts.append("\n".join(line for line in current_lines if line).strip())
                current_lines = [prefix] if prefix else []
                candidate = "\n".join(
                    line for line in [*current_lines, row_text] if line
                ).strip()
            if row_text and self.tokens.count(candidate) > max_tokens:
                # A single unusually wide row is still retained, clipped only
                # after the repeated table identity/header prefix.
                available = max(1, max_tokens - self.tokens.count("\n".join(current_lines)))
                row_text = self._clip_to_tokens(row_text, available)
            if row_text:
                current_lines.append(row_text)
        rendered = "\n".join(line for line in current_lines if line).strip()
        if rendered:
            parts.append(rendered)

        synthetic: list[DocumentBlock] = []
        for index, text in enumerate(parts):
            synthetic.append(
                DocumentBlock(
                    block_uid=f"{block.block_uid}:table_rows:{index}",
                    page_index=block.page_index,
                    block_order=block.block_order,
                    block_type="table",
                    section_path=list(block.section_path),
                    text=text,
                    printed_page_label=block.printed_page_label,
                    markdown=text,
                    table_html=None,
                    formula_latex=None,
                    bbox=block.bbox,
                    provenance={
                        **block.provenance,
                        "split_strategy": "table_rows_v1",
                        "split_part": index,
                    },
                )
            )
        return synthetic

    def _clip_to_tokens(self, text: str, max_tokens: int) -> str:
        value = str(text or "").strip()
        if not value:
            return ""
        encoded = self.tokens.encode(value)
        if len(encoded) <= max(1, int(max_tokens)):
            return value
        return str(self.tokens.decode(encoded[: max(1, int(max_tokens))])).strip()

    def _split_long_text(self, text: str, max_tokens: int) -> list[str]:
        tokens = self.tokens.encode(text)
        overlap = min(max(0, self.config.overlap_tokens), max_tokens // 4)
        pieces: list[str] = []
        start = 0
        while start < len(tokens):
            end = min(len(tokens), start + max_tokens)
            piece = self.tokens.decode(tokens[start:end]).strip()
            if piece:
                pieces.append(piece)
            if end >= len(tokens):
                break
            next_start = end - overlap
            if next_start <= start:
                next_start = end
            start = next_start
        return pieces

    def _render(self, blocks: list[DocumentBlock], section_path: list[str]) -> str:
        lines: list[str] = []
        if section_path:
            lines.append(" / ".join(section_path))
        for block in blocks:
            text = _block_text(block)
            if not text:
                continue
            if block.block_type == "heading" and text not in lines:
                lines.append(text)
            else:
                lines.append(text)
        return "\n\n".join(lines).strip()

    @staticmethod
    def _embedding_text(title: str, section_path: list[str], text: str) -> str:
        prefix = " / ".join(x for x in [title.strip(), *section_path] if x.strip())
        return f"{prefix}\n{text}".strip() if prefix else text.strip()

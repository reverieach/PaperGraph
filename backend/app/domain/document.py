"""Canonical document domain objects used by the Phase 2 RAG pipeline.

The parser-specific objects (Docling, PyMuPDF, or a future cloud parser) must
be converted to these small, JSON-friendly dataclasses before they reach the
database, chunker, or retrieval layer.  Keeping this boundary explicit makes
parser replacement and deterministic re-ingestion possible.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


BlockType = Literal[
    "title",
    "heading",
    "paragraph",
    "list",
    "table",
    "formula",
    "caption",
    "figure",
    "reference",
    "footnote",
    "unknown",
]
ChunkLevel = Literal["parent", "child"]


def stable_hash(value: Any) -> str:
    """Return a stable SHA-256 for JSON-compatible values or plain text."""

    if isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def stable_uid(prefix: str, *parts: object) -> str:
    """Build a deterministic identifier with a readable prefix."""

    digest = stable_hash([str(part) for part in parts])[:32]
    return f"{prefix}_{digest}"


@dataclass(slots=True)
class ParseQualityReport:
    page_count: int = 0
    non_empty_page_count: int = 0
    block_count: int = 0
    text_char_count: int = 0
    heading_count: int = 0
    table_count: int = 0
    formula_count: int = 0
    figure_count: int = 0
    reference_count: int = 0
    pages_with_provenance: int = 0
    score: float = 0.0
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DocumentPage:
    # Canonical PDF page numbers are 1-based.  This matches Docling,
    # PyMuPDF's user-facing page anchors, and the reader's ``[pN]`` syntax.
    # ``page_index`` is kept for schema compatibility even though it is a
    # page number rather than a zero-based array offset.
    page_index: int
    printed_page_label: str | None = None
    width: float | None = None
    height: float | None = None
    text: str = ""
    markdown: str = ""
    image_count: int = 0
    table_count: int = 0
    formula_count: int = 0
    ocr_used: bool = False
    quality: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DocumentBlock:
    block_uid: str
    page_index: int  # 1-based PDF page number
    block_order: int
    block_type: BlockType
    section_path: list[str]
    text: str
    printed_page_label: str | None = None
    markdown: str | None = None
    table_html: str | None = None
    formula_latex: str | None = None
    bbox: list[float] | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def text_hash(self) -> str:
        return stable_hash(self.text.strip())


@dataclass(slots=True)
class DocumentChunk:
    chunk_uid: str
    document_version_id: str
    user_id: int
    paper_id: int
    parent_chunk_uid: str | None
    level: ChunkLevel
    ordinal: int
    content_type: str
    section_path: list[str]
    page_start: int  # inclusive, 1-based PDF page number
    page_end: int  # inclusive, 1-based PDF page number
    block_uids: list[str]
    display_text: str
    embedding_text: str
    sparse_text: str
    text_hash: str
    token_count: int
    chunker_version: str


@dataclass(slots=True)
class CanonicalDocument:
    document_version_id: str
    user_id: int
    paper_id: int
    file_hash: str
    parser_id: str
    parser_version: str
    pages: list[DocumentPage] = field(default_factory=list)
    blocks: list[DocumentBlock] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    quality: ParseQualityReport = field(default_factory=ParseQualityReport)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_version_id": self.document_version_id,
            "user_id": self.user_id,
            "paper_id": self.paper_id,
            "file_hash": self.file_hash,
            "parser_id": self.parser_id,
            "parser_version": self.parser_version,
            "pages": [asdict(page) for page in self.pages],
            "blocks": [asdict(block) for block in self.blocks],
            "metadata": self.metadata,
            "quality": self.quality.to_dict(),
        }

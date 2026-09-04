"""Parser adapters for Docling Standard and PyMuPDF fallback."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.metadata
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ...domain.document import (
    BlockType,
    CanonicalDocument,
    DocumentBlock,
    DocumentPage,
    ParseQualityReport,
    stable_uid,
)
from .canonicalizer import canonicalize_docling

logger = logging.getLogger(__name__)
# Bump whenever the adapter can produce materially different canonical output
# for the same source file.  v6 additionally classifies structurally invalid
# PDFs before a heavyweight converter starts; v5 added encrypted-PDF rejection.
PARSER_ADAPTER_VERSION = "pdf-parser-adapter-v6"
_OCR_MODES = {"auto", "always", "never"}
_NATIVE_TEXT_PAGE_CHARS = 160
_NATIVE_TEXT_MEDIAN_CHARS = 280
_NATIVE_TEXT_MIN_COVERAGE = 0.85
_INVALID_PDF_ERROR_TYPES = frozenset({"EmptyFileError", "FileDataError"})


def _is_ascii_path(path: Path) -> bool:
    """Return whether a filesystem path is safe for Docling's Windows backend."""

    return str(path).isascii()


def _is_invalid_pdf_error(exc: BaseException) -> bool:
    """Return whether PyMuPDF classified an input as structurally unreadable.

    The name-based check deliberately avoids importing PyMuPDF exception types
    at module import time.  It is stable across the supported PyMuPDF releases
    and does not convert ordinary extraction/OCR errors into a terminal input
    error.
    """

    return type(exc).__name__ in _INVALID_PDF_ERROR_TYPES


def _docling_staging_candidates(configured_root: str | None) -> list[Path]:
    """Return ordered, ASCII-only candidates for a short-lived input copy.

    ``docling-parse`` currently fails with a generic ``Invalid argument`` on
    Windows when its input path contains CJK characters.  The user-controlled
    location is tried first.  The ordinary system temporary directory is the
    portable default; Windows Public is only a last-resort fallback for a
    non-ASCII user profile path.
    """

    candidates: list[Path] = []
    if configured_root and configured_root.strip():
        candidates.append(Path(configured_root).expanduser())
    candidates.append(Path(tempfile.gettempdir()) / "papergraph_docling")
    if os.name == "nt":
        public_root = Path(os.environ.get("PUBLIC", r"C:\\Users\\Public"))
        candidates.append(public_root / "PaperGraph" / "docling-input")
    return candidates


def _resolve_ascii_staging_root(configured_root: str | None) -> Path:
    """Create and return a writable ASCII staging directory, or raise clearly."""

    errors: list[str] = []
    for candidate in _docling_staging_candidates(configured_root):
        try:
            resolved = candidate.resolve()
            if not _is_ascii_path(resolved):
                errors.append(f"non_ascii:{resolved}")
                continue
            resolved.mkdir(parents=True, exist_ok=True)
            if not resolved.is_dir():
                errors.append(f"not_directory:{resolved}")
                continue
            return resolved
        except OSError as exc:
            errors.append(f"{type(exc).__name__}:{candidate}")
    detail = "; ".join(errors[:4]) or "no candidates"
    raise RuntimeError(f"Docling needs an ASCII-safe staging directory ({detail})")


@contextmanager
def staged_docling_input(
    pdf_path: str | os.PathLike[str],
    *,
    file_hash: str,
    staging_root: str | None = None,
) -> Iterator[tuple[Path, bool]]:
    """Yield a Docling-readable input path and whether a temporary copy was used.

    The original PDF is never modified.  An ASCII path is passed through with
    zero copy.  For a non-ASCII path, a hash-named copy is placed in a private
    temporary subdirectory and removed immediately after conversion finishes.
    """

    source = Path(pdf_path).expanduser().resolve()
    if _is_ascii_path(source):
        yield source, False
        return

    root = _resolve_ascii_staging_root(staging_root)
    with tempfile.TemporaryDirectory(prefix="pg_docling_", dir=str(root)) as directory:
        staged = Path(directory) / f"{str(file_hash).lower()[:64]}.pdf"
        shutil.copy2(source, staged)
        yield staged, True


def _result_flag(result: Any, name: str) -> bool:
    """Read a Docling result predicate across method/property API versions."""

    value = getattr(result, name, False)
    return bool(value() if callable(value) else value)


def _result_status(result: Any) -> str:
    value = getattr(result, "status", "")
    return str(getattr(value, "value", value) or "").strip().lower()


def _docling_error_summary(result: Any) -> dict[str, Any]:
    errors = list(getattr(result, "errors", None) or [])
    categories: dict[str, int] = {}
    pages: set[int] = set()
    messages: list[str] = []
    for error in errors:
        category_obj = getattr(error, "category", "unknown")
        category = str(getattr(category_obj, "value", category_obj) or "unknown")
        categories[category] = categories.get(category, 0) + 1
        page_no = getattr(error, "page_no", None)
        try:
            if page_no is not None:
                pages.add(int(page_no))
        except (TypeError, ValueError):
            pass
        message = str(getattr(error, "error_message", "") or "").strip()
        if message and message not in messages:
            messages.append(message[:500])
    return {
        "count": len(errors),
        "categories": categories,
        "pages": sorted(pages),
        "messages": messages[:8],
    }


def file_sha256(path: str | os.PathLike[str]) -> tuple[str, int]:
    """Hash a PDF in bounded reads so large papers do not load in memory."""

    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def probe_pdf_native_text(
    pdf_path: str | os.PathLike[str],
    *,
    max_sample_pages: int = 12,
) -> dict[str, Any]:
    """Inspect a bounded PDF sample before deciding whether OCR is needed.

    Native text extraction is cheap and avoids loading Docling's OCR models
    for a conventional born-digital paper.  The result is deliberately only
    aggregate counts: it is persisted for operational diagnosis without
    copying a user's document text into metadata/logs.
    """

    try:
        import fitz

        with fitz.open(str(pdf_path)) as document:
            if bool(getattr(document, "needs_pass", False)):
                return {
                    "available": False,
                    "requires_password": True,
                    "invalid_pdf": False,
                    "page_count": int(len(document)),
                    "sampled_page_count": 0,
                    "native_text_page_count": 0,
                    "median_non_whitespace_chars": 0,
                }
            page_count = len(document)
            if page_count <= 0:
                return {
                    "available": True,
                    "requires_password": False,
                    "invalid_pdf": False,
                    "page_count": 0,
                    "sampled_page_count": 0,
                    "native_text_page_count": 0,
                    "median_non_whitespace_chars": 0,
                }
            sample_count = max(1, min(int(max_sample_pages), page_count))
            if sample_count == page_count:
                sample_indices = list(range(page_count))
            else:
                # Deterministically spread samples across the document so a
                # cover page cannot decide OCR for every later page.
                sample_indices = sorted(
                    {
                        round(index * (page_count - 1) / max(1, sample_count - 1))
                        for index in range(sample_count)
                    }
                )
            char_counts: list[int] = []
            for page_index in sample_indices:
                text = document.load_page(int(page_index)).get_text("text") or ""
                char_counts.append(len(re.sub(r"\s+", "", text)))
        ordered = sorted(char_counts)
        median = ordered[len(ordered) // 2] if ordered else 0
        native_pages = sum(count >= _NATIVE_TEXT_PAGE_CHARS for count in char_counts)
        return {
            "available": True,
            "requires_password": False,
            "invalid_pdf": False,
            "page_count": int(page_count),
            "sampled_page_count": len(char_counts),
            "native_text_page_count": int(native_pages),
            "median_non_whitespace_chars": int(median),
        }
    except Exception as exc:
        # Failing open the text layer is a reason to retain OCR, not to fail a
        # document that Docling may still be able to read.
        return {
            "available": False,
            "requires_password": False,
            "invalid_pdf": _is_invalid_pdf_error(exc),
            "error_type": type(exc).__name__,
            "page_count": 0,
            "sampled_page_count": 0,
            "native_text_page_count": 0,
            "median_non_whitespace_chars": 0,
        }


def resolve_pdf_ocr_mode(
    pdf_path: str | os.PathLike[str],
    *,
    mode: str,
) -> tuple[bool, dict[str, Any]]:
    """Return a conservative OCR decision plus safe aggregate evidence."""

    normalized_mode = str(mode or "auto").strip().lower()
    if normalized_mode not in _OCR_MODES:
        raise ValueError("ocr mode must be auto, always, or never")
    probe = probe_pdf_native_text(pdf_path)
    if probe.get("requires_password"):
        return False, {**probe, "decision": "requires_password"}
    if probe.get("invalid_pdf"):
        return False, {**probe, "decision": "invalid_pdf"}
    if normalized_mode == "always":
        return True, {**probe, "decision": "always"}
    if normalized_mode == "never":
        return False, {**probe, "decision": "never"}
    sampled = max(1, int(probe.get("sampled_page_count") or 0))
    coverage = int(probe.get("native_text_page_count") or 0) / sampled
    median = int(probe.get("median_non_whitespace_chars") or 0)
    native_text_sufficient = bool(
        probe.get("available")
        and coverage >= _NATIVE_TEXT_MIN_COVERAGE
        and median >= _NATIVE_TEXT_MEDIAN_CHARS
    )
    return (
        not native_text_sufficient,
        {
            **probe,
            "decision": "skip_ocr_native_text" if native_text_sufficient else "use_ocr_low_native_text",
            "native_text_coverage": round(coverage, 4),
        },
    )


@dataclass(slots=True)
class ParseResult:
    document: CanonicalDocument | None
    parser_id: str
    parser_version: str
    degraded: bool = False
    flags: list[str] = field(default_factory=list)
    error: str | None = None
    error_code: str | None = None


class PyMuPDFParser:
    parser_id = "pymupdf_fallback"

    def __init__(self, *, version: str | None = None) -> None:
        self.parser_version = version or _package_version("PyMuPDF")

    def parse(
        self,
        pdf_path: str,
        *,
        document_version_id: str,
        user_id: int,
        paper_id: int,
        file_hash: str | None = None,
    ) -> ParseResult:
        file_hash = file_hash or file_sha256(pdf_path)[0]
        try:
            import fitz

            pages: list[DocumentPage] = []
            blocks: list[DocumentBlock] = []
            with fitz.open(pdf_path) as pdf:
                if bool(getattr(pdf, "needs_pass", False)):
                    return ParseResult(
                        None,
                        self.parser_id,
                        self.parser_version,
                        degraded=True,
                        error="PDF is encrypted and requires a password",
                        error_code="PDF_ENCRYPTED",
                    )
                for page_index, page in enumerate(pdf, start=1):
                    raw_text = (page.get_text("text") or "").strip()
                    try:
                        markdown = (page.get_text("markdown") or raw_text).strip()
                    except Exception:
                        markdown = raw_text
                    page_blocks = page.get_text("blocks") or []
                    page_obj = DocumentPage(
                        page_index=page_index,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        text=raw_text,
                        markdown=markdown,
                        image_count=len(page.get_images(full=True) or []),
                    )
                    pages.append(page_obj)
                    for block_order, raw in enumerate(page_blocks):
                        if len(raw) < 5:
                            continue
                        text = str(raw[4] or "").strip()
                        if not text:
                            continue
                        block_type = _guess_block_type(text)
                        section_path = [text] if block_type == "heading" else []
                        bbox = [float(x) for x in raw[:4]] if len(raw) >= 4 else None
                        blocks.append(
                            DocumentBlock(
                                block_uid=stable_uid("blk", document_version_id, page_index, block_order, text),
                                page_index=page_index,
                                block_order=block_order,
                                block_type=block_type,
                                section_path=section_path,
                                text=text,
                                markdown=text,
                                bbox=bbox,
                                provenance={
                                    "parser": self.parser_id,
                                    "block_type": raw[6] if len(raw) > 6 else None,
                                },
                            )
                        )
            non_empty = sum(bool(page.text) for page in pages)
            quality = ParseQualityReport(
                page_count=len(pages),
                non_empty_page_count=non_empty,
                block_count=len(blocks),
                text_char_count=sum(len(page.text) for page in pages),
                heading_count=sum(block.block_type == "heading" for block in blocks),
                figure_count=sum(page.image_count for page in pages),
                pages_with_provenance=non_empty,
            )
            coverage = non_empty / len(pages) if pages else 0.0
            quality.score = min(1.0, 0.6 * coverage + 0.4 * min(1.0, quality.text_char_count / 3000))
            document = CanonicalDocument(
                document_version_id=document_version_id,
                user_id=int(user_id),
                paper_id=int(paper_id),
                file_hash=file_hash,
                parser_id=self.parser_id,
                parser_version=self.parser_version,
                pages=pages,
                blocks=blocks,
                metadata={"fallback": True},
                quality=quality,
            )
            return ParseResult(document, self.parser_id, self.parser_version, degraded=True)
        except Exception as exc:
            # Parser errors are operationally useful, but a full local path
            # can reveal a user's directory/name in shared logs.  The content
            # hash is sufficient to correlate this failure with the ingest job.
            logger.exception("pymupdf_parse_failed", extra={"file_hash": file_hash})
            invalid_pdf = _is_invalid_pdf_error(exc)
            return ParseResult(
                None,
                self.parser_id,
                self.parser_version,
                degraded=True,
                error="PDF cannot be opened or is corrupted" if invalid_pdf else str(exc),
                error_code="PDF_INVALID" if invalid_pdf else None,
            )


class DoclingParser:
    parser_id = "docling_standard"

    def __init__(
        self,
        *,
        artifacts_path: str | None = None,
        staging_root: str | None = None,
        device: str = "auto",
        do_ocr: bool | None = True,
        ocr_mode: str | None = None,
        do_table_structure: bool = True,
        max_num_pages: int | None = None,
    ) -> None:
        self.artifacts_path = artifacts_path
        self.staging_root = staging_root
        self.device = device
        if ocr_mode is None:
            # Preserve the previous direct-parser default (always OCR).  The
            # ingestion service passes an explicit ``ocr_mode`` and owns the
            # production automatic policy.
            ocr_mode = "auto" if do_ocr is None else ("always" if do_ocr else "never")
        self.ocr_mode = str(ocr_mode).strip().lower()
        if self.ocr_mode not in _OCR_MODES:
            raise ValueError("ocr_mode must be auto, always, or never")
        self.do_table_structure = bool(do_table_structure)
        self.max_num_pages = max_num_pages
        self.parser_version = _package_version("docling")

    def parse(
        self,
        pdf_path: str,
        *,
        document_version_id: str,
        user_id: int,
        paper_id: int,
        file_hash: str | None = None,
    ) -> ParseResult:
        file_hash = file_hash or file_sha256(pdf_path)[0]
        try:
            do_ocr, ocr_preflight = resolve_pdf_ocr_mode(
                pdf_path,
                mode=self.ocr_mode,
            )
            if bool(ocr_preflight.get("requires_password")):
                return ParseResult(
                    None,
                    self.parser_id,
                    self.parser_version,
                    error="PDF is encrypted and requires a password",
                    error_code="PDF_ENCRYPTED",
                )
            if bool(ocr_preflight.get("invalid_pdf")):
                return ParseResult(
                    None,
                    self.parser_id,
                    self.parser_version,
                    error="PDF cannot be opened or is corrupted",
                    error_code="PDF_INVALID",
                )

            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import AcceleratorOptions, PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            options = PdfPipelineOptions(
                artifacts_path=self.artifacts_path,
                do_ocr=do_ocr,
                do_table_structure=self.do_table_structure,
                generate_page_images=False,
                generate_picture_images=False,
            )
            options.accelerator_options = AcceleratorOptions(device=self.device, num_threads=4)
            converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
            kwargs: dict[str, Any] = {"raises_on_error": False}
            if self.max_num_pages is not None:
                kwargs["max_num_pages"] = max(1, int(self.max_num_pages))
            with staged_docling_input(
                pdf_path,
                file_hash=file_hash,
                staging_root=self.staging_root,
            ) as (converter_input, input_staged):
                result = converter.convert(str(converter_input), **kwargs)
            status = _result_status(result)
            error_summary = _docling_error_summary(result)
            if result.document is None or status in {"failure", "skipped"}:
                messages = error_summary["messages"]
                error = "; ".join(messages) if messages else f"docling conversion status: {status or 'unknown'}"
                return ParseResult(None, self.parser_id, self.parser_version, error=error)
            raw = result.document.export_to_dict(mode="json", by_alias=True, exclude_none=True)
            has_parse_errors = _result_flag(result, "has_parse_errors")
            has_inference_errors = _result_flag(result, "has_inference_errors")
            document = canonicalize_docling(
                raw,
                document_version_id=document_version_id,
                user_id=int(user_id),
                paper_id=int(paper_id),
                file_hash=file_hash,
                parser_version=self.parser_version,
                metadata={
                    "source": "docling",
                    "conversion_status": status,
                    "has_parse_errors": has_parse_errors,
                    "has_inference_errors": has_inference_errors,
                    "error_summary": error_summary,
                    "device": self.device,
                    "ocr_mode": self.ocr_mode,
                    "do_ocr": do_ocr,
                    "ocr_preflight": ocr_preflight,
                    # Keep the staging fact observable without exposing the
                    # temporary filesystem path in a persisted artifact.
                    "input_staged_for_docling": input_staged,
                },
            )
            flags: list[str] = []
            if has_parse_errors:
                flags.append("docling_parse_errors")
            if has_inference_errors:
                flags.append("docling_inference_errors")
            document.quality.flags.extend(flags)
            return ParseResult(
                document,
                self.parser_id,
                self.parser_version,
                degraded=status == "partial_success" or bool(flags),
                flags=flags,
            )
        except Exception as exc:
            logger.exception("docling_parse_failed", extra={"file_hash": file_hash})
            return ParseResult(None, self.parser_id, self.parser_version, error=str(exc))


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "uninstalled"


def _guess_block_type(text: str) -> BlockType:
    normalized = " ".join(text.split())
    if len(normalized) <= 120 and re.match(r"^(?:\d+(?:\.\d+)*|[IVX]+)[.)]?\s+\S+", normalized):
        return "heading"
    if re.match(r"^(?:figure|fig\.?|table|tab\.?|图|表)\s*\d+", normalized, re.IGNORECASE):
        return "caption"
    if re.match(r"^(?:references|bibliography|参考文献)\s*$", normalized, re.IGNORECASE):
        return "heading"
    return "paragraph"

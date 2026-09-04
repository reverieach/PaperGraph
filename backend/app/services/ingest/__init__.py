"""Paper ingestion primitives for the Phase 2 document RAG pipeline."""

from .parsers import DoclingParser, ParseResult, PyMuPDFParser, file_sha256
from .quality import ParseQualityGate, QualityGateResult

__all__ = [
    "DoclingParser",
    "ParseResult",
    "PyMuPDFParser",
    "ParseQualityGate",
    "QualityGateResult",
    "file_sha256",
]


"""Small, deterministic normalizers shared by ingestion and retrieval.

These functions deliberately fix only presentation artefacts that are common
in PDF extraction.  They do not translate, segment, summarize, or otherwise
reinterpret research content.
"""

from __future__ import annotations

import re
import unicodedata


_CJK = r"\u3400-\u9fff\uf900-\ufaff"
_CJK_INTERNAL_SPACE_RE = re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])")
_CJK_TO_PUNCTUATION_SPACE_RE = re.compile(
    rf"(?<=[{_CJK}])\s+(?=[,，.。!?！？;；:：、）】》])"
)
_PUNCTUATION_TO_CJK_SPACE_RE = re.compile(
    rf"(?<=[(（【《])\s+(?=[{_CJK}])"
)


def normalize_pdf_layout_text(value: str) -> str:
    """Normalize harmless CJK character spacing introduced by PDF extraction.

    Some PDF/OCR backends emit ``检 索 增 强 生 成`` for one logical phrase.
    Chinese prose normally has no whitespace between adjacent Han characters,
    so joining only those characters (and their adjacent CJK punctuation)
    restores lexical retrieval without altering English word boundaries,
    numbers, formulas, or normal paragraph breaks.
    """

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\u00ad", "").replace("\u200b", "")
    text = _CJK_INTERNAL_SPACE_RE.sub("", text)
    text = _CJK_TO_PUNCTUATION_SPACE_RE.sub("", text)
    return _PUNCTUATION_TO_CJK_SPACE_RE.sub("", text)


__all__ = ["normalize_pdf_layout_text"]

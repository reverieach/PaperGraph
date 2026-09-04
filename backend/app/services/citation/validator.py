"""Validate model citation markers against the request-scoped EvidenceRegistry."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .evidence_registry import EvidenceRegistry


_BRACKET_RE = re.compile(r"\[([^\]]+)\]")
_EVIDENCE_TOKEN_RE = re.compile(r"^E(\d+)$", re.IGNORECASE)
# A canonical RAG request must never expose a freely model-invented page
# anchor.  Legacy reader mode has a separate compatibility parser; this rule
# applies only when an EvidenceRegistry has been attached to the request.
_LEGACY_PAGE_MARKER_RE = re.compile(r"\[p\d+(?:\s*(?:,|-)\s*p?\d+)*\]", re.IGNORECASE)


@dataclass(slots=True)
class CitationValidationResult:
    citations: list[dict]
    invalid_markers: list[str] = field(default_factory=list)
    cleaned_reply: str = ""


class CitationValidator:
    """Accept only `[E#]` markers tied to current canonical evidence."""

    @staticmethod
    def _marker_ids(group: str) -> list[str]:
        output: list[str] = []
        for part in str(group or "").split(","):
            match = _EVIDENCE_TOKEN_RE.fullmatch(part.strip())
            if match:
                output.append(f"E{int(match.group(1))}")
        return output

    def validate_reply(
        self,
        reply: str,
        *,
        registry: EvidenceRegistry,
    ) -> CitationValidationResult:
        original = str(reply or "")
        citations: list[dict] = []
        invalid: list[str] = []
        seen: set[str] = set()

        def replace(match: re.Match[str]) -> str:
            raw_group = match.group(1)
            ids = self._marker_ids(raw_group)
            if not ids:
                return match.group(0)
            valid_markers: list[str] = []
            for evidence_id in ids:
                entry = registry.get(evidence_id)
                if entry is None or not entry.citation_allowed:
                    invalid.append(f"[{evidence_id}]")
                    continue
                if evidence_id not in seen:
                    seen.add(evidence_id)
                    public = entry.to_public_dict()
                    public["marker"] = f"[{evidence_id}]"
                    citations.append(public)
                valid_markers.append(f"[{evidence_id}]")
            # A fabricated-only marker is removed rather than being displayed
            # as a believable but unsupported page citation.
            return " ".join(valid_markers)

        cleaned = _BRACKET_RE.sub(replace, original)
        cleaned = _LEGACY_PAGE_MARKER_RE.sub("", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\s+([，。；：！？,.!?])", r"\1", cleaned)
        return CitationValidationResult(
            citations=citations,
            invalid_markers=invalid,
            cleaned_reply=cleaned.strip(),
        )


__all__ = ["CitationValidationResult", "CitationValidator"]

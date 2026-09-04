"""Deterministic parse quality gates and explicit degradation states."""

from __future__ import annotations

from dataclasses import dataclass, field

from ...domain.document import CanonicalDocument


@dataclass(slots=True)
class QualityGateResult:
    accepted: bool
    status: str
    score: float
    flags: list[str] = field(default_factory=list)


class ParseQualityGate:
    """Reject unusable parses and label partial parses instead of hiding them."""

    def __init__(
        self,
        *,
        min_page_coverage: float = 0.75,
        min_text_chars: int = 120,
        min_blocks: int = 1,
        degraded_page_coverage: float = 0.95,
    ) -> None:
        self.min_page_coverage = max(0.0, min(1.0, float(min_page_coverage)))
        self.min_text_chars = max(1, int(min_text_chars))
        self.min_blocks = max(1, int(min_blocks))
        self.degraded_page_coverage = max(
            self.min_page_coverage,
            min(1.0, float(degraded_page_coverage)),
        )

    def evaluate(self, document: CanonicalDocument) -> QualityGateResult:
        quality = document.quality
        flags = list(quality.flags)
        if quality.page_count <= 0:
            flags.append("no_pages")
        if quality.block_count < self.min_blocks:
            flags.append("too_few_blocks")
        if quality.text_char_count < self.min_text_chars:
            flags.append("too_little_text")
        coverage = (
            quality.non_empty_page_count / quality.page_count
            if quality.page_count
            else 0.0
        )
        if coverage < self.min_page_coverage:
            flags.append("low_page_text_coverage")
        if quality.pages_with_provenance < quality.non_empty_page_count:
            flags.append("incomplete_provenance")
        quality.flags = sorted(set(flags))

        hard_fail = (
            quality.page_count <= 0
            or quality.block_count < self.min_blocks
            or quality.text_char_count < self.min_text_chars
        )
        if hard_fail:
            return QualityGateResult(False, "failed", quality.score, quality.flags)
        if coverage < self.degraded_page_coverage or "incomplete_provenance" in quality.flags:
            return QualityGateResult(True, "degraded", quality.score, quality.flags)
        return QualityGateResult(True, "ready", quality.score, quality.flags)


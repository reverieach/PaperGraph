"""Bounded canonical evidence expansion after child-chunk retrieval.

The retriever optimizes recall against concise child chunks.  The Reader needs
just enough surrounding material to interpret a hit safely, not an entire
section or PDF.  This adapter deterministically adds the owning parent and
same-parent neighbours under hard repository scope checks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from ...repositories.document_repository import DocumentRepository


def _section_path(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = []
    return [str(item) for item in value] if isinstance(value, list) else []


def _hit_value(hit: Any, name: str, default: Any = None) -> Any:
    return hit.get(name, default) if isinstance(hit, dict) else getattr(hit, name, default)


@dataclass(slots=True)
class EvidenceExpansionResult:
    chunks: list[dict[str, Any]] = field(default_factory=list)
    anchor_count: int = 0
    parent_count: int = 0
    neighbor_count: int = 0
    degraded: bool = False
    degradation_reasons: list[str] = field(default_factory=list)


class EvidenceExpander:
    """Expand at most a few direct retrieval hits into bounded PDF evidence."""

    def __init__(self, repository: DocumentRepository) -> None:
        self.repository = repository

    @staticmethod
    def _direct_chunk(hit: Any) -> dict[str, Any] | None:
        uid = str(_hit_value(hit, "chunk_uid", "") or "").strip()
        if not uid:
            return None
        try:
            paper_id = int(_hit_value(hit, "paper_id", 0) or 0)
        except (TypeError, ValueError):
            paper_id = 0
        return {
            "chunk_uid": uid,
            "paper_id": paper_id,
            "document_version_id": str(_hit_value(hit, "document_version_id", "") or ""),
            "content_type": str(_hit_value(hit, "content_type", "paragraph") or "paragraph"),
            "display_text": str(_hit_value(hit, "display_text", "") or ""),
            "section_path": _section_path(_hit_value(hit, "section_path", [])),
            "page_start": _hit_value(hit, "page_start"),
            "page_end": _hit_value(hit, "page_end"),
            "rrf_score": _hit_value(hit, "rrf_score"),
            "rerank_score": _hit_value(hit, "rerank_score"),
            "expansion_role": "anchor",
        }

    @staticmethod
    def _row_chunk(
        row: dict[str, Any],
        *,
        role: str,
        anchor: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "chunk_uid": str(row.get("chunk_uid") or ""),
            "paper_id": int(row.get("paper_id") or 0),
            "document_version_id": str(row.get("document_version_id") or ""),
            "content_type": str(row.get("content_type") or "paragraph"),
            "display_text": str(row.get("display_text") or ""),
            "section_path": _section_path(row.get("section_path_json") or []),
            "page_start": row.get("page_start"),
            "page_end": row.get("page_end"),
            # Expanded context is not independently ranked.  It inherits the
            # anchor's score solely for ContextBuilder ordering and traceability.
            "rrf_score": anchor.get("rrf_score"),
            "rerank_score": anchor.get("rerank_score"),
            "expansion_role": role,
            "expansion_anchor_uid": anchor.get("chunk_uid"),
        }

    def expand(
        self,
        *,
        user_id: int,
        hits: Iterable[Any],
        max_anchor_hits: int = 4,
        neighbor_radius: int = 1,
        max_chunks: int = 12,
    ) -> EvidenceExpansionResult:
        result = EvidenceExpansionResult()
        direct: list[dict[str, Any]] = []
        seen_direct: set[str] = set()
        for hit in list(hits or []):
            item = self._direct_chunk(hit)
            if item is None or item["chunk_uid"] in seen_direct:
                continue
            seen_direct.add(item["chunk_uid"])
            direct.append(item)
            if len(direct) >= max(1, min(int(max_anchor_hits), 12)):
                break
        if not direct:
            return result

        anchor_uids = [str(item["chunk_uid"]) for item in direct]
        try:
            expanded_rows = self.repository.expand_active_evidence_chunks(
                user_id=int(user_id),
                anchor_chunk_uids=anchor_uids,
                neighbor_radius=neighbor_radius,
                limit=max(1, min(int(max_chunks) * 4, 200)),
            )
        except Exception as exc:
            result.degraded = True
            result.degradation_reasons.append(
                f"evidence_expansion_unavailable:{type(exc).__name__}"
            )
            result.chunks = direct[: max(1, int(max_chunks))]
            result.anchor_count = len(result.chunks)
            return result

        by_uid = {str(row.get("chunk_uid") or ""): row for row in expanded_rows}
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        max_output = max(1, min(int(max_chunks), 40))

        def append(chunk: dict[str, Any], *, count: str) -> None:
            uid = str(chunk.get("chunk_uid") or "")
            if not uid or uid in seen or len(output) >= max_output:
                return
            seen.add(uid)
            output.append(chunk)
            if count == "anchor":
                result.anchor_count += 1
            elif count == "parent":
                result.parent_count += 1
            elif count == "neighbor":
                result.neighbor_count += 1

        for anchor in direct:
            anchor_uid = str(anchor["chunk_uid"])
            anchor_row = by_uid.get(anchor_uid)
            if anchor_row is None:
                # The retriever's final hydration normally makes this
                # impossible.  Preserve the already-authorized direct hit
                # rather than dropping useful evidence because expansion had
                # an inconsistent projection snapshot.
                append(anchor, count="anchor")
                result.degraded = True
                result.degradation_reasons.append("evidence_expansion_anchor_missing")
                continue

            append(self._row_chunk(anchor_row, role="anchor", anchor=anchor), count="anchor")
            parent_uid = str(anchor_row.get("parent_chunk_uid") or "")
            if parent_uid and parent_uid in by_uid:
                append(
                    self._row_chunk(by_uid[parent_uid], role="parent_context", anchor=anchor),
                    count="parent",
                )

            anchor_ordinal = int(anchor_row.get("ordinal") or 0)
            parent_key = anchor_row.get("parent_chunk_uid")
            neighbours = [
                row
                for row in expanded_rows
                if str(row.get("level") or "") == "child"
                and str(row.get("document_version_id") or "")
                == str(anchor_row.get("document_version_id") or "")
                and row.get("parent_chunk_uid") == parent_key
                and str(row.get("chunk_uid") or "") != anchor_uid
            ]
            neighbours.sort(
                key=lambda row: (
                    abs(int(row.get("ordinal") or 0) - anchor_ordinal),
                    int(row.get("ordinal") or 0),
                )
            )
            for neighbor in neighbours:
                append(
                    self._row_chunk(neighbor, role="neighbor_context", anchor=anchor),
                    count="neighbor",
                )

        result.chunks = output
        return result


__all__ = ["EvidenceExpander", "EvidenceExpansionResult"]

"""Request-scoped registry of canonical PDF evidence allowed for citation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..context.builder import ContextEvidence, ContextPackage


@dataclass(frozen=True, slots=True)
class EvidenceRegistryEntry:
    evidence_id: str
    paper_id: int
    document_version_id: str
    chunk_uid: str
    content: str
    content_type: str
    page_start: int | None
    page_end: int | None
    section_path: tuple[str, ...]
    source_type: str = "retrieved_chunk"
    citation_allowed: bool = True

    def to_public_dict(self, *, snippet_chars: int = 320) -> dict:
        snippet = self.content.strip()
        if len(snippet) > snippet_chars:
            snippet = snippet[: max(0, int(snippet_chars) - 1)].rstrip() + "…"
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "paper_id": self.paper_id,
            "document_version_id": self.document_version_id,
            "chunk_uid": self.chunk_uid,
            "content_type": self.content_type,
            "page": self.page_start,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "section_path": list(self.section_path),
            "snippet": snippet,
        }


class EvidenceRegistry:
    """Only evidence that actually survived ContextPackage budgeting is valid."""

    def __init__(
        self,
        *,
        user_id: int,
        paper_id: int,
        entries: list[EvidenceRegistryEntry],
    ) -> None:
        self.user_id = int(user_id)
        self.paper_id = int(paper_id)
        self._entries = {entry.evidence_id: entry for entry in entries}

    @classmethod
    def from_context_package(
        cls,
        package: ContextPackage,
        *,
        user_id: int,
        paper_id: int,
    ) -> "EvidenceRegistry":
        entries: list[EvidenceRegistryEntry] = []
        for evidence in package.evidence:
            if not isinstance(evidence, ContextEvidence):
                continue
            if (
                not evidence.citation_allowed
                or evidence.source_type != "retrieved_chunk"
                or evidence.paper_id != int(paper_id)
                or not evidence.document_version_id
                or not evidence.chunk_uid
            ):
                continue
            entries.append(
                EvidenceRegistryEntry(
                    evidence_id=evidence.evidence_id,
                    paper_id=int(evidence.paper_id),
                    document_version_id=str(evidence.document_version_id),
                    chunk_uid=str(evidence.chunk_uid),
                    content=str(evidence.content),
                    content_type=str(evidence.content_type),
                    page_start=evidence.page_start,
                    page_end=evidence.page_end,
                    section_path=tuple(str(value) for value in evidence.section_path),
                )
        )
        return cls(user_id=user_id, paper_id=paper_id, entries=entries)

    @classmethod
    def from_context_package_for_papers(
        cls,
        package: ContextPackage,
        *,
        user_id: int,
        paper_ids: list[int] | tuple[int, ...] | set[int],
    ) -> "EvidenceRegistry":
        """Build a request registry for a fixed, user-owned paper set.

        A research session may retrieve evidence from several selected papers.
        Its citation boundary is still just as strict as Reader's single-paper
        boundary: a marker is valid only when the chunk both survived the
        ContextPackage token budget and belongs to one of the explicit session
        papers.  ``paper_id=0`` is an internal sentinel only; consumers must
        inspect each entry's own ``paper_id``.
        """

        allowed_paper_ids = {int(value) for value in paper_ids}
        entries: list[EvidenceRegistryEntry] = []
        for evidence in package.evidence:
            if (
                not isinstance(evidence, ContextEvidence)
                or not evidence.citation_allowed
                or evidence.source_type != "retrieved_chunk"
                or evidence.paper_id is None
                or int(evidence.paper_id) not in allowed_paper_ids
                or not evidence.document_version_id
                or not evidence.chunk_uid
            ):
                continue
            entries.append(
                EvidenceRegistryEntry(
                    evidence_id=evidence.evidence_id,
                    paper_id=int(evidence.paper_id),
                    document_version_id=str(evidence.document_version_id),
                    chunk_uid=str(evidence.chunk_uid),
                    content=str(evidence.content),
                    content_type=str(evidence.content_type),
                    page_start=evidence.page_start,
                    page_end=evidence.page_end,
                    section_path=tuple(str(value) for value in evidence.section_path),
                )
            )
        return cls(user_id=user_id, paper_id=0, entries=entries)

    def get(self, evidence_id: str) -> EvidenceRegistryEntry | None:
        return self._entries.get(str(evidence_id or "").strip())

    def has_chunk(self, *, document_version_id: str, chunk_uid: str) -> bool:
        """Return whether this request has already exposed one chunk as evidence."""

        version = str(document_version_id or "").strip()
        uid = str(chunk_uid or "").strip()
        return bool(version and uid) and any(
            entry.document_version_id == version and entry.chunk_uid == uid
            for entry in self._entries.values()
        )

    def register_tool_context_package(
        self,
        package: ContextPackage,
        *,
        document_version_id: str,
    ) -> list[str]:
        """Register a bounded tool re-entry package as request evidence.

        ``DynamicContextBuilder`` always starts temporary packages at ``E1``.
        A tool result arrives after the original Reader package, so its marker
        IDs must be re-numbered under this request's existing registry before
        the model sees them.  The method rejects every non-canonical or
        foreign item rather than weakening the current request scope.
        """

        expected_version = str(document_version_id or "").strip()
        candidates: list[ContextEvidence] = []
        for evidence in package.evidence:
            if (
                not isinstance(evidence, ContextEvidence)
                or not evidence.citation_allowed
                or evidence.source_type != "retrieved_chunk"
                or evidence.paper_id != self.paper_id
                or not evidence.document_version_id
                or evidence.document_version_id != expected_version
                or not evidence.chunk_uid
                or self.has_chunk(
                    document_version_id=evidence.document_version_id,
                    chunk_uid=evidence.chunk_uid,
                )
            ):
                return []
            candidates.append(evidence)
        if not candidates:
            return []

        marker_map: dict[str, str] = {}
        next_index = len(self._entries) + 1
        for offset, evidence in enumerate(candidates):
            old_id = evidence.evidence_id
            new_id = f"E{next_index + offset}"
            marker_map[old_id] = new_id
            evidence.evidence_id = new_id
            self._entries[new_id] = EvidenceRegistryEntry(
                evidence_id=new_id,
                paper_id=self.paper_id,
                document_version_id=str(evidence.document_version_id),
                chunk_uid=str(evidence.chunk_uid),
                content=str(evidence.content),
                content_type=str(evidence.content_type),
                page_start=evidence.page_start,
                page_end=evidence.page_end,
                section_path=tuple(str(value) for value in evidence.section_path),
            )

        marker_pattern = re.compile(r"\[(E\d+)\]")

        def rewrite(value: str) -> str:
            return marker_pattern.sub(
                lambda match: f"[{marker_map.get(match.group(1), match.group(1))}]",
                str(value or ""),
            )

        package.text = rewrite(package.text)
        for evidence in candidates:
            evidence.prompt_content = rewrite(evidence.prompt_content)
        return [marker_map[key] for key in marker_map]

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(self._entries)


__all__ = ["EvidenceRegistry", "EvidenceRegistryEntry"]

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    evidence_turn_ids: list[int] = Field(default_factory=list, max_length=20)


class UserMemoryCandidate(EvidenceItem):
    kind: Literal["preference", "research_goal"] = "preference"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class MemoryDraftPayload(BaseModel):
    paper_summary: str = Field(default="", max_length=4000)
    key_findings: list[EvidenceItem] = Field(default_factory=list, max_length=12)
    open_questions: list[EvidenceItem] = Field(default_factory=list, max_length=12)
    research_decisions: list[EvidenceItem] = Field(default_factory=list, max_length=12)
    user_memory_candidates: list[UserMemoryCandidate] = Field(
        default_factory=list,
        max_length=8,
    )


PAPER_MEMORY_KINDS = {
    "reading_summary",
    "key_finding",
    "open_question",
    "research_decision",
}
USER_MEMORY_KINDS = {"preference", "research_goal"}

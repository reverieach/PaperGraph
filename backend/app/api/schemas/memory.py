from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CreateMemoryDraftRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=128)
    from_turn_id: int | None = Field(default=None, ge=1)
    to_turn_id: int | None = Field(default=None, ge=1)


class CommitMemoryItem(BaseModel):
    kind: Literal[
        "reading_summary",
        "key_finding",
        "open_question",
        "research_decision",
        "preference",
        "research_goal",
    ]
    content: str = Field(min_length=1, max_length=4000)


class CommitMemoryDraftRequest(BaseModel):
    paper_items: list[CommitMemoryItem] = Field(default_factory=list, max_length=40)
    accepted_user_items: list[CommitMemoryItem] = Field(
        default_factory=list,
        max_length=20,
    )


class CreateUserMemoryRequest(BaseModel):
    kind: Literal["preference", "research_goal"]
    content: str = Field(min_length=1, max_length=4000)

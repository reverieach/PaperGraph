from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class CreateResearchSessionRequest(BaseModel):
    paper_ids: list[int] = Field(min_length=1, max_length=8)
    title: str | None = Field(default=None, max_length=200)

    @field_validator("paper_ids")
    @classmethod
    def validate_paper_ids(cls, value: list[int]) -> list[int]:
        if any(int(item) < 1 for item in value):
            raise ValueError("paper_ids 必须是正整数")
        return value


class ResearchChatRequest(BaseModel):
    user_message: str = Field(min_length=1, max_length=4000)

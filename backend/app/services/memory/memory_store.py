"""Compatibility read adapter for the canonical Memory repository."""
from __future__ import annotations

from typing import Any

from ...repositories.memory_repository import MemoryRepository


class MemoryStoreConfig:
    max_context_items: int = 10


class MemoryStore:
    """Deprecated facade.

    Direct writes are deliberately rejected. New Memory must follow
    ``MemoryDraftService -> user review -> MemoryRepository.commit_draft``.
    """

    def __init__(
        self,
        db_path: str,
        config: MemoryStoreConfig | None = None,
        *,
        user_id: int | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self.config = config or MemoryStoreConfig()
        self.user_id = int(user_id) if user_id is not None else None
        self.repository = MemoryRepository(self.db_path)

    def add(self, **_: Any) -> str:
        raise RuntimeError("自动 Memory 写入已禁用；请生成并提交 Memory 草稿")

    def upsert_single(self, **_: Any) -> None:
        raise RuntimeError("自动 Memory 写入已禁用；请生成并提交 Memory 草稿")

    def build_context_block(self, *, paper_id: int, query: str = "") -> str:
        if self.user_id is None:
            return ""
        return self.repository.build_paper_context(
            user_id=self.user_id,
            paper_id=int(paper_id),
            query=query,
            limit=self.config.max_context_items,
        )

    def get_context_for_query(
        self,
        *,
        paper_id: int,
        query: str,
        limit: int = 6,
    ) -> str:
        if self.user_id is None:
            return ""
        return self.repository.build_paper_context(
            user_id=self.user_id,
            paper_id=int(paper_id),
            query=query,
            limit=int(limit),
        )

    def list_recent_contents(
        self,
        *,
        scope: str,
        paper_id: int | None,
        kinds: list[str],
        limit: int,
    ) -> list[str]:
        if self.user_id is None:
            return []
        scope_type = "user" if scope == "global" else "paper"
        scope_id = str(self.user_id if scope_type == "user" else paper_id)
        return [
            str(item["content"])
            for item in self.repository.list_memories(
                user_id=self.user_id,
                scope_type=scope_type,
                scope_id=scope_id,
                kinds=kinds,
                limit=int(limit),
            )
        ]

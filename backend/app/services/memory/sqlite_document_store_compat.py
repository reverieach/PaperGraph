"""Removed legacy document-store API.

The old store used an incompatible ``memories`` schema. Keeping a live SQL
implementation here would recreate the split-brain Memory system, so callers
must migrate to :class:`app.repositories.memory_repository.MemoryRepository`.
"""
from __future__ import annotations


class SQLiteDocumentStore:
    def __init__(self, db_path: str) -> None:
        raise RuntimeError(
            "SQLiteDocumentStore 已废弃；请使用 MemoryRepository 和 MemoryDraftService"
        )

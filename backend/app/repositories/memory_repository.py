from __future__ import annotations

import hashlib
import json
import time
import unicodedata
import uuid
from typing import Any

from ..domain.memory import PAPER_MEMORY_KINDS, USER_MEMORY_KINDS
from ..infrastructure.db import Database


class MemoryRepositoryError(RuntimeError):
    pass


class MemoryOwnershipError(MemoryRepositoryError):
    pass


class MemoryConflictError(MemoryRepositoryError):
    pass


def normalize_memory_content(content: str) -> str:
    text = unicodedata.normalize("NFKC", str(content or "")).strip()
    return " ".join(text.split())


def memory_content_hash(content: str) -> str:
    return hashlib.sha256(
        normalize_memory_content(content).encode("utf-8")
    ).hexdigest()


class MemoryRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self.database = Database(self.db_path)

    @staticmethod
    def _row_to_draft(row: Any) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "user_id": int(row["user_id"]),
            "paper_id": int(row["paper_id"]),
            "conversation_id": str(row["conversation_id"]),
            "from_turn_id": int(row["from_turn_id"]),
            "to_turn_id": int(row["to_turn_id"]),
            "status": str(row["status"]),
            "payload": json.loads(str(row["payload_json"])),
            "source_snapshot_hash": str(row["source_snapshot_hash"]),
            "llm_model": str(row["llm_model"] or ""),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
            "committed_at": (
                int(row["committed_at"])
                if row["committed_at"] is not None
                else None
            ),
        }

    @staticmethod
    def _row_to_memory(row: Any) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "user_id": int(row["user_id"]),
            "scope_type": str(row["scope_type"]),
            "scope_id": str(row["scope_id"]),
            "kind": str(row["kind"]),
            "content": str(row["content"]),
            "source_type": str(row["source_type"]),
            "source_id": str(row["source_id"] or ""),
            "source_turn_from": row["source_turn_from"],
            "source_turn_to": row["source_turn_to"],
            "confirmed_by_user": bool(row["confirmed_by_user"]),
            "status": str(row["status"]),
            "metadata": json.loads(str(row["metadata_json"] or "{}")),
            "importance": float(row["importance"] if row["importance"] is not None else 0.5),
            "expires_at": (
                int(row["expires_at"]) if row["expires_at"] is not None else None
            ),
            "superseded_by": str(row["superseded_by"] or ""),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }

    def create_draft(
        self,
        *,
        user_id: int,
        paper_id: int,
        conversation_id: str,
        from_turn_id: int,
        to_turn_id: int,
        payload: dict[str, Any],
        source_snapshot_hash: str,
        llm_model: str | None,
    ) -> dict[str, Any]:
        now = int(time.time())
        draft_id = uuid.uuid4().hex
        with self.database.transaction() as conn:
            scope = conn.execute(
                """
                SELECT 1 FROM reader_conversations
                WHERE id=? AND user_id=? AND paper_id=?
                """,
                (conversation_id, int(user_id), int(paper_id)),
            ).fetchone()
            if not scope:
                raise MemoryOwnershipError("conversation not found")
            conn.execute(
                """
                INSERT INTO memory_drafts(
                    id,user_id,paper_id,conversation_id,from_turn_id,to_turn_id,
                    status,payload_json,source_snapshot_hash,llm_model,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,'draft',?,?,?,?,?)
                """,
                (
                    draft_id,
                    int(user_id),
                    int(paper_id),
                    conversation_id,
                    int(from_turn_id),
                    int(to_turn_id),
                    json.dumps(payload, ensure_ascii=False),
                    source_snapshot_hash,
                    llm_model,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM memory_drafts WHERE id=?",
                (draft_id,),
            ).fetchone()
        assert row is not None
        return self._row_to_draft(row)

    def get_draft(self, *, user_id: int, draft_id: str) -> dict[str, Any] | None:
        with self.database.read() as conn:
            row = conn.execute(
                "SELECT * FROM memory_drafts WHERE id=? AND user_id=?",
                (str(draft_id), int(user_id)),
            ).fetchone()
        return self._row_to_draft(row) if row else None

    def cancel_draft(
        self,
        *,
        user_id: int,
        draft_id: str,
    ) -> dict[str, Any]:
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM memory_drafts WHERE id=? AND user_id=?",
                (str(draft_id), int(user_id)),
            ).fetchone()
            if not row:
                raise MemoryOwnershipError("memory draft not found")
            status = str(row["status"])
            if status == "cancelled":
                return self._row_to_draft(row)
            if status != "draft":
                raise MemoryConflictError(
                    f"draft cannot be cancelled from status={status}"
                )
            now = int(time.time())
            updated = conn.execute(
                """
                UPDATE memory_drafts
                SET status='cancelled',updated_at=?
                WHERE id=? AND user_id=? AND status='draft'
                """,
                (now, str(draft_id), int(user_id)),
            )
            if updated.rowcount != 1:
                raise MemoryConflictError("draft cancellation lost a concurrent race")
            cancelled = conn.execute(
                "SELECT * FROM memory_drafts WHERE id=? AND user_id=?",
                (str(draft_id), int(user_id)),
            ).fetchone()
        assert cancelled is not None
        return self._row_to_draft(cancelled)

    def commit_draft(
        self,
        *,
        user_id: int,
        draft_id: str,
        paper_items: list[dict[str, str]],
        accepted_user_items: list[dict[str, str]],
        idempotency_key: str,
    ) -> dict[str, Any]:
        key = str(idempotency_key or "").strip()
        if len(key) < 8 or len(key) > 128:
            raise ValueError("Idempotency-Key 长度必须在 8 到 128 之间")

        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM memory_drafts WHERE id=? AND user_id=?",
                (str(draft_id), int(user_id)),
            ).fetchone()
            if not row:
                raise MemoryOwnershipError("memory draft not found")
            if str(row["status"]) == "committed":
                if str(row["commit_idempotency_key"] or "") != key:
                    raise MemoryConflictError(
                        "draft already committed with another idempotency key"
                    )
                cached_result = json.loads(
                    str(row["commit_result_json"] or "{}")
                )
                if not isinstance(cached_result, dict):
                    raise MemoryConflictError("stored commit result is invalid")
                return cached_result
            if str(row["status"]) != "draft":
                raise MemoryConflictError(
                    f"draft cannot be committed from status={row['status']}"
                )

            specs: list[tuple[str, str, str, dict[str, str]]] = []
            for item in paper_items:
                kind = str(item.get("kind") or "").strip()
                if kind not in PAPER_MEMORY_KINDS:
                    raise ValueError(f"unsupported paper memory kind: {kind}")
                specs.append(("paper", str(row["paper_id"]), kind, item))
            for item in accepted_user_items:
                kind = str(item.get("kind") or "").strip()
                if kind not in USER_MEMORY_KINDS:
                    raise ValueError(f"unsupported user memory kind: {kind}")
                specs.append(("user", str(user_id), kind, item))
            if not specs:
                raise ValueError("至少确认一条 Memory")

            created: list[dict[str, Any]] = []
            seen_specs: set[tuple[str, str, str, str]] = set()
            now = int(time.time())
            for scope_type, scope_id, kind, item in specs:
                content = normalize_memory_content(str(item.get("content") or ""))
                if not content:
                    raise ValueError("Memory 内容不能为空")
                if len(content) > 4000:
                    raise ValueError("Memory 内容不能超过 4000 字符")
                content_hash = memory_content_hash(content)
                spec_key = (scope_type, scope_id, kind, content_hash)
                if spec_key in seen_specs:
                    continue
                seen_specs.add(spec_key)
                memory_id = uuid.uuid4().hex
                conn.execute(
                    """
                    INSERT OR IGNORE INTO memories(
                        id,user_id,scope_type,scope_id,kind,content,content_hash,
                        source_type,source_id,source_turn_from,source_turn_to,
                        confirmed_by_user,status,metadata_json,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,'active','{}',?,?)
                    """,
                    (
                        memory_id,
                        int(user_id),
                        scope_type,
                        scope_id,
                        kind,
                        content,
                        content_hash,
                        "memory_draft",
                        str(draft_id),
                        int(row["from_turn_id"]),
                        int(row["to_turn_id"]),
                        now,
                        now,
                    ),
                )
                stored = conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE user_id=? AND scope_type=? AND scope_id=?
                      AND kind=? AND content_hash=? AND status='active'
                    """,
                    (
                        int(user_id),
                        scope_type,
                        scope_id,
                        kind,
                        content_hash,
                    ),
                ).fetchone()
                assert stored is not None
                created.append(self._row_to_memory(stored))

            result = {
                "draft_id": str(draft_id),
                "status": "committed",
                "memories": created,
            }
            updated = conn.execute(
                """
                UPDATE memory_drafts
                SET status='committed',commit_idempotency_key=?,
                    commit_result_json=?,updated_at=?,committed_at=?
                WHERE id=? AND user_id=? AND status='draft'
                """,
                (
                    key,
                    json.dumps(result, ensure_ascii=False),
                    now,
                    now,
                    str(draft_id),
                    int(user_id),
                ),
            )
            if updated.rowcount != 1:
                raise MemoryConflictError("draft commit lost a concurrent race")
        return result

    def add_user_memory(
        self,
        *,
        user_id: int,
        kind: str,
        content: str,
    ) -> tuple[dict[str, Any], bool]:
        normalized_kind = str(kind or "").strip()
        if normalized_kind not in USER_MEMORY_KINDS:
            raise ValueError(f"unsupported user memory kind: {normalized_kind}")
        normalized_content = normalize_memory_content(content)
        if not normalized_content:
            raise ValueError("Memory 内容不能为空")
        if len(normalized_content) > 4000:
            raise ValueError("Memory 内容不能超过 4000 字符")

        content_hash = memory_content_hash(normalized_content)
        memory_id = uuid.uuid4().hex
        now = int(time.time())
        with self.database.transaction() as conn:
            inserted = conn.execute(
                """
                INSERT OR IGNORE INTO memories(
                    id,user_id,scope_type,scope_id,kind,content,content_hash,
                    source_type,source_id,confirmed_by_user,status,metadata_json,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,'manual',NULL,1,'active',?, ?, ?)
                """,
                (
                    memory_id,
                    int(user_id),
                    "user",
                    str(user_id),
                    normalized_kind,
                    normalized_content,
                    content_hash,
                    json.dumps(
                        {"entry_point": "long_term_memory_page"},
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM memories
                WHERE user_id=? AND scope_type='user' AND scope_id=?
                  AND kind=? AND content_hash=? AND status='active'
                """,
                (
                    int(user_id),
                    str(user_id),
                    normalized_kind,
                    content_hash,
                ),
            ).fetchone()
        assert row is not None
        return self._row_to_memory(row), inserted.rowcount == 1

    def list_memories(
        self,
        *,
        user_id: int,
        scope_type: str | None = None,
        scope_id: str | None = None,
        kinds: list[str] | None = None,
        include_deleted: bool = False,
        confirmed_only: bool = True,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["user_id=?"]
        params: list[Any] = [int(user_id)]
        if scope_type:
            clauses.append("scope_type=?")
            params.append(str(scope_type))
        if scope_id is not None:
            clauses.append("scope_id=?")
            params.append(str(scope_id))
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            clauses.append(f"kind IN ({placeholders})")
            params.extend(str(kind) for kind in kinds)
        if not include_deleted:
            clauses.append("status='active'")
        if confirmed_only:
            clauses.append("confirmed_by_user=1")
        params.append(max(1, min(int(limit), 1000)))
        with self.database.read() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM memories
                WHERE {" AND ".join(clauses)}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def delete_memory(self, *, user_id: int, memory_id: str) -> bool:
        with self.database.transaction() as conn:
            result = conn.execute(
                """
                UPDATE memories
                SET status='deleted',updated_at=?
                WHERE id=? AND user_id=? AND status='active'
                """,
                (int(time.time()), str(memory_id), int(user_id)),
            )
            return result.rowcount == 1

    def update_memory_retrieval_policy(
        self,
        *,
        user_id: int,
        memory_id: str,
        importance: float | None = None,
        expires_at: int | None = None,
    ) -> dict[str, Any] | None:
        """Update user-controlled retrieval metadata without changing content.

        This deliberately does not create or rewrite Memory text.  Content
        changes must go through the existing confirmation workflow so the
        provenance shown in the UI remains truthful.
        """

        assignments = ["updated_at=?"]
        params: list[Any] = [int(time.time())]
        if importance is not None:
            value = float(importance)
            if value < 0.0 or value > 1.0:
                raise ValueError("importance must be between 0 and 1")
            assignments.append("importance=?")
            params.append(value)
        if expires_at is not None:
            assignments.append("expires_at=?")
            params.append(max(0, int(expires_at)))
        if len(assignments) == 1:
            return self.get_memory(user_id=user_id, memory_id=memory_id)
        with self.database.transaction() as conn:
            updated = conn.execute(
                f"""
                UPDATE memories SET {", ".join(assignments)}
                WHERE id=? AND user_id=? AND status='active'
                """,
                (*params, str(memory_id), int(user_id)),
            )
            if int(updated.rowcount or 0) != 1:
                return None
            row = conn.execute(
                "SELECT * FROM memories WHERE id=? AND user_id=?",
                (str(memory_id), int(user_id)),
            ).fetchone()
        return self._row_to_memory(row) if row is not None else None

    def supersede_memory(
        self,
        *,
        user_id: int,
        memory_id: str,
        replacement_memory_id: str,
    ) -> bool:
        """Mark one active Memory superseded by another owned active Memory."""

        if str(memory_id) == str(replacement_memory_id):
            raise ValueError("replacement memory must differ from source memory")
        now = int(time.time())
        with self.database.transaction() as conn:
            source = conn.execute(
                """
                SELECT scope_type,scope_id,confirmed_by_user,expires_at
                FROM memories
                WHERE id=? AND user_id=? AND status='active'
                """,
                (str(memory_id), int(user_id)),
            ).fetchone()
            replacement = conn.execute(
                """
                SELECT scope_type,scope_id,confirmed_by_user,expires_at
                FROM memories
                WHERE id=? AND user_id=? AND status='active'
                """,
                (str(replacement_memory_id), int(user_id)),
            ).fetchone()
            if source is None or replacement is None:
                return False
            if (
                int(source["confirmed_by_user"]) != 1
                or int(replacement["confirmed_by_user"]) != 1
                or (
                    source["expires_at"] is not None
                    and int(source["expires_at"]) <= now
                )
                or (
                    replacement["expires_at"] is not None
                    and int(replacement["expires_at"]) <= now
                )
            ):
                return False
            if (
                str(source["scope_type"]),
                str(source["scope_id"]),
            ) != (
                str(replacement["scope_type"]),
                str(replacement["scope_id"]),
            ):
                raise ValueError("replacement memory must have the same scope")
            changed = conn.execute(
                """
                UPDATE memories
                SET status='superseded', superseded_by=?, updated_at=?
                WHERE id=? AND user_id=? AND status='active'
                """,
                (
                    str(replacement_memory_id),
                    now,
                    str(memory_id),
                    int(user_id),
                ),
            )
        return int(changed.rowcount or 0) == 1

    def get_memory(self, *, user_id: int, memory_id: str) -> dict[str, Any] | None:
        with self.database.read() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id=? AND user_id=?",
                (str(memory_id), int(user_id)),
            ).fetchone()
        return self._row_to_memory(row) if row is not None else None

    def list_retrievable_memories(
        self,
        *,
        user_id: int,
        paper_id: int,
        now: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return only active, confirmed Memory in the caller's valid scopes."""

        timestamp = int(time.time()) if now is None else int(now)
        with self.database.read() as conn:
            rows = conn.execute(
                """
                SELECT m.* FROM memories m
                WHERE m.user_id=?
                  AND m.status='active'
                  AND m.confirmed_by_user=1
                  AND (m.expires_at IS NULL OR m.expires_at > ?)
                  AND EXISTS (
                      SELECT 1 FROM papers p
                      WHERE p.id=? AND p.user_id=m.user_id
                  )
                  AND (
                      (m.scope_type='paper' AND m.scope_id=?)
                      OR (m.scope_type='user' AND m.scope_id=?)
                  )
                ORDER BY m.importance DESC, m.updated_at DESC
                LIMIT ?
                """,
                (
                    int(user_id),
                    timestamp,
                    int(paper_id),
                    str(paper_id),
                    str(user_id),
                    max(1, min(int(limit), 1000)),
                ),
            ).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def has_memory_fts(self) -> bool:
        return self._has_memory_fts_projection("memories_fts")

    def has_memory_trigram_fts(self) -> bool:
        return self._has_memory_fts_projection("memories_trigram_fts")

    def _has_memory_fts_projection(self, table_name: str) -> bool:
        if table_name not in {"memories_fts", "memories_trigram_fts"}:
            raise ValueError("unsupported Memory FTS projection")
        row = self.database.query_one(
            "SELECT 1 FROM sqlite_master WHERE name=?", (table_name,)
        )
        return row is not None

    def search_retrievable_memories_fts(
        self,
        *,
        user_id: int,
        paper_id: int,
        match_query: str,
        trigram: bool = False,
        now: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Search one Memory FTS projection, then reapply all scope filters."""

        table_name = "memories_trigram_fts" if trigram else "memories_fts"
        if not match_query.strip() or not self._has_memory_fts_projection(table_name):
            return []
        timestamp = int(time.time()) if now is None else int(now)
        with self.database.read() as conn:
            rows = conn.execute(
                f"""
                SELECT m.*, bm25({table_name}) AS bm25_score
                FROM {table_name}
                JOIN memories m ON m.rowid={table_name}.rowid
                WHERE {table_name} MATCH ?
                  AND m.user_id=?
                  AND m.status='active'
                  AND m.confirmed_by_user=1
                  AND (m.expires_at IS NULL OR m.expires_at > ?)
                  AND EXISTS (
                      SELECT 1 FROM papers p
                      WHERE p.id=? AND p.user_id=m.user_id
                  )
                  AND (
                      (m.scope_type='paper' AND m.scope_id=?)
                      OR (m.scope_type='user' AND m.scope_id=?)
                  )
                ORDER BY bm25_score ASC
                LIMIT ?
                """,
                (
                    str(match_query),
                    int(user_id),
                    timestamp,
                    int(paper_id),
                    str(paper_id),
                    str(user_id),
                    max(1, min(int(limit), 200)),
                ),
            ).fetchall()
        return [self._row_to_memory(row) | {"bm25_score": row["bm25_score"]} for row in rows]

    def stats(self, *, user_id: int) -> dict[str, Any]:
        with self.database.read() as conn:
            rows = conn.execute(
                """
                SELECT scope_type,kind,status,COUNT(*) AS count
                FROM memories
                WHERE user_id=?
                GROUP BY scope_type,kind,status
                ORDER BY scope_type,kind,status
                """,
                (int(user_id),),
            ).fetchall()
        return {
            "total": sum(int(row["count"]) for row in rows),
            "groups": [
                {
                    "scope_type": str(row["scope_type"]),
                    "kind": str(row["kind"]),
                    "status": str(row["status"]),
                    "count": int(row["count"]),
                }
                for row in rows
            ],
        }

    def build_paper_context(
        self,
        *,
        user_id: int,
        paper_id: int,
        query: str = "",
        limit: int = 6,
    ) -> str:
        # Compatibility facade for legacy callers.  The canonical path is
        # ``MemoryRetriever.retrieve`` which returns structured scope/reason
        # metadata for the ContextBuilder instead of a preassembled string.
        from ..services.memory.retriever import MemoryRetriever, format_memory_context

        requested_limit = max(1, min(int(limit), 10))
        paper_limit = min(3, requested_limit)
        user_limit = min(2, max(0, requested_limit - paper_limit))
        retrieved = MemoryRetriever(
            self,
            paper_limit=paper_limit,
            user_limit=user_limit,
        ).retrieve(
            user_id=int(user_id),
            paper_id=int(paper_id),
            query=query,
        )
        return format_memory_context(retrieved.hits)

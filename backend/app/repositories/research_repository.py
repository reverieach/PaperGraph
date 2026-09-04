from __future__ import annotations

import json
import time
import uuid
from typing import Any

from ..infrastructure.db import Database


class ResearchSessionError(RuntimeError):
    pass


class ResearchSessionNotFound(ResearchSessionError):
    pass


class ResearchPaperOwnershipError(ResearchSessionError):
    pass


class ResearchRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self.database = Database(self.db_path)

    @staticmethod
    def _paper_from_row(row: Any) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "title": str(row["title"]),
            "abstract": str(row["abstract"] or ""),
            "year": int(row["year"]) if row["year"] is not None else None,
            "journal": str(row["journal"] or ""),
            "category": str(row["category"] or ""),
            "authors": [
                item.strip()
                for item in str(row["author_names"] or "").split("|||")
                if item.strip()
            ],
        }

    @staticmethod
    def _turn_from_row(row: Any) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "role": str(row["role"]),
            "content": str(row["content"]),
            "metadata": json.loads(str(row["metadata_json"] or "{}")),
            "created_at": int(row["created_at"]),
        }

    def _load_papers(
        self,
        conn: Any,
        *,
        user_id: int,
        paper_ids: list[int],
    ) -> list[dict[str, Any]]:
        if not paper_ids:
            return []
        placeholders = ",".join("?" for _ in paper_ids)
        rows = conn.execute(
            f"""
            SELECT p.id,p.title,p.abstract,p.year,p.journal,p.category,
                   GROUP_CONCAT(a.name, '|||') AS author_names
            FROM papers p
            LEFT JOIN paper_authors pa ON pa.paper_id=p.id
            LEFT JOIN authors a ON a.id=pa.author_id
            WHERE p.user_id=? AND p.id IN ({placeholders})
            GROUP BY p.id
            """,
            (int(user_id), *paper_ids),
        ).fetchall()
        by_id = {
            int(row["id"]): self._paper_from_row(row)
            for row in rows
        }
        return [by_id[paper_id] for paper_id in paper_ids if paper_id in by_id]

    def create_session(
        self,
        *,
        user_id: int,
        paper_ids: list[int],
        title: str | None = None,
    ) -> dict[str, Any]:
        unique_paper_ids = list(dict.fromkeys(int(item) for item in paper_ids))
        if not unique_paper_ids:
            raise ValueError("请至少选择一篇论文")
        if len(unique_paper_ids) > 8:
            raise ValueError("当前阶段一次最多选择 8 篇论文")

        session_id = uuid.uuid4().hex
        now = int(time.time())
        with self.database.transaction() as conn:
            papers = self._load_papers(
                conn,
                user_id=int(user_id),
                paper_ids=unique_paper_ids,
            )
            if len(papers) != len(unique_paper_ids):
                raise ResearchPaperOwnershipError(
                    "所选论文不存在或不属于当前用户"
                )
            normalized_title = str(title or "").strip()
            if not normalized_title:
                normalized_title = (
                    f"{papers[0]['title']} 等 {len(papers)} 篇论文"
                    if len(papers) > 1
                    else papers[0]["title"]
                )
            normalized_title = normalized_title[:200]
            conn.execute(
                """
                INSERT INTO research_sessions(
                    id,user_id,title,created_at,updated_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    session_id,
                    int(user_id),
                    normalized_title,
                    now,
                    now,
                ),
            )
            conn.executemany(
                """
                INSERT INTO research_session_papers(
                    session_id,paper_id,position,created_at
                ) VALUES(?,?,?,?)
                """,
                [
                    (session_id, paper_id, position, now)
                    for position, paper_id in enumerate(unique_paper_ids)
                ],
            )
        return {
            "id": session_id,
            "title": normalized_title,
            "papers": papers,
            "turns": [],
            "created_at": now,
            "updated_at": now,
        }

    def get_session(
        self,
        *,
        user_id: int,
        session_id: str,
        turn_limit: int = 100,
    ) -> dict[str, Any] | None:
        with self.database.read() as conn:
            session = conn.execute(
                """
                SELECT * FROM research_sessions
                WHERE id=? AND user_id=?
                """,
                (str(session_id), int(user_id)),
            ).fetchone()
            if not session:
                return None
            paper_rows = conn.execute(
                """
                SELECT p.id,p.title,p.abstract,p.year,p.journal,p.category,
                       GROUP_CONCAT(a.name, '|||') AS author_names
                FROM research_session_papers rsp
                JOIN papers p ON p.id=rsp.paper_id AND p.user_id=?
                LEFT JOIN paper_authors pa ON pa.paper_id=p.id
                LEFT JOIN authors a ON a.id=pa.author_id
                WHERE rsp.session_id=?
                GROUP BY p.id,rsp.position
                ORDER BY rsp.position
                """,
                (int(user_id), str(session_id)),
            ).fetchall()
            turn_rows = conn.execute(
                """
                SELECT * FROM research_turns
                WHERE session_id=? AND user_id=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (
                    str(session_id),
                    int(user_id),
                    max(1, min(int(turn_limit), 500)),
                ),
            ).fetchall()
        return {
            "id": str(session["id"]),
            "title": str(session["title"]),
            "papers": [self._paper_from_row(row) for row in paper_rows],
            "turns": [
                self._turn_from_row(row)
                for row in reversed(turn_rows)
            ],
            "created_at": int(session["created_at"]),
            "updated_at": int(session["updated_at"]),
        }

    def list_sessions(
        self,
        *,
        user_id: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self.database.read() as conn:
            rows = conn.execute(
                """
                SELECT rs.id,rs.title,rs.created_at,rs.updated_at,
                       COUNT(rsp.paper_id) AS paper_count
                FROM research_sessions rs
                LEFT JOIN research_session_papers rsp ON rsp.session_id=rs.id
                WHERE rs.user_id=?
                GROUP BY rs.id
                ORDER BY rs.updated_at DESC
                LIMIT ?
                """,
                (int(user_id), max(1, min(int(limit), 200))),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "title": str(row["title"]),
                "paper_count": int(row["paper_count"]),
                "created_at": int(row["created_at"]),
                "updated_at": int(row["updated_at"]),
            }
            for row in rows
        ]

    def append_exchange(
        self,
        *,
        user_id: int,
        session_id: str,
        user_message: str,
        assistant_reply: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        now = int(time.time())
        with self.database.transaction() as conn:
            session = conn.execute(
                """
                SELECT 1 FROM research_sessions
                WHERE id=? AND user_id=?
                """,
                (str(session_id), int(user_id)),
            ).fetchone()
            if not session:
                raise ResearchSessionNotFound("协同研究会话不存在")
            cursor = conn.executemany(
                """
                INSERT INTO research_turns(
                    session_id,user_id,role,content,metadata_json,created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                [
                    (
                        str(session_id),
                        int(user_id),
                        "user",
                        str(user_message),
                        "{}",
                        now,
                    ),
                    (
                        str(session_id),
                        int(user_id),
                        "assistant",
                        str(assistant_reply),
                        json.dumps(metadata or {}, ensure_ascii=False),
                        now,
                    ),
                ],
            )
            del cursor
            conn.execute(
                """
                UPDATE research_sessions SET updated_at=?
                WHERE id=? AND user_id=?
                """,
                (now, str(session_id), int(user_id)),
            )
            rows = conn.execute(
                """
                SELECT * FROM research_turns
                WHERE session_id=? AND user_id=?
                ORDER BY id DESC LIMIT 2
                """,
                (str(session_id), int(user_id)),
            ).fetchall()
        return [self._turn_from_row(row) for row in reversed(rows)]

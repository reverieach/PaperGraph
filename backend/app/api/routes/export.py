"""User-scoped portable JSON export."""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from ..dependencies import get_db_path
from ..deps import require_user
from ...infrastructure.db import Database


router = APIRouter(prefix="/export", tags=["导出"])
VALID_SCOPES = {"all", "papers", "reader", "memory", "graph", "feedback"}


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _export_papers(conn, user_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id,title,doi,pmid,arxiv_id,pmc_id,abstract,journal,
               venue_type,year,volume,issue,pages,publisher,pdf_url,
               source_url,source,keywords,mesh_terms,"references",category,
               tags,rating,read_status,importance,notes,citations,
               created_at,updated_at
        FROM papers
        WHERE user_id=?
        ORDER BY created_at ASC,id ASC
        """,
        (int(user_id),),
    ).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        authors = conn.execute(
            """
            SELECT a.name,a.affiliation,a.email,a.orcid,pa.author_order
            FROM authors a
            JOIN paper_authors pa ON pa.author_id=a.id
            WHERE pa.paper_id=?
            ORDER BY pa.author_order ASC,a.id ASC
            """,
            (int(row["id"]),),
        ).fetchall()
        item["authors"] = [dict(author) for author in authors]
        output.append(item)
    return output


def _export_reader(conn, user_id: int) -> dict[str, list[dict[str, Any]]]:
    conversations = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id,paper_id,title,created_at,updated_at
            FROM reader_conversations
            WHERE user_id=?
            ORDER BY created_at ASC,id ASC
            """,
            (int(user_id),),
        ).fetchall()
    ]
    turns = [
        {
            **dict(row),
            "metadata": json.loads(str(row["metadata_json"] or "{}")),
        }
        for row in conn.execute(
            """
            SELECT id,paper_id,conversation_id,role,content,metadata_json,created_at
            FROM paper_reader_turns
            WHERE user_id=?
            ORDER BY id ASC
            """,
            (int(user_id),),
        ).fetchall()
    ]
    for turn in turns:
        turn.pop("metadata_json", None)
    return {"conversations": conversations, "turns": turns}


def _export_memories(conn, user_id: int) -> list[dict[str, Any]]:
    return [
        {
            **dict(row),
            "metadata": json.loads(str(row["metadata_json"] or "{}")),
        }
        for row in conn.execute(
            """
            SELECT id,scope_type,scope_id,kind,content,source_type,source_id,
                   source_turn_from,source_turn_to,confirmed_by_user,status,
                   metadata_json,created_at,updated_at
            FROM memories
            WHERE user_id=? AND confirmed_by_user=1 AND status='active'
            ORDER BY created_at ASC,id ASC
            """,
            (int(user_id),),
        ).fetchall()
    ]


def _export_relations(conn, user_id: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "paper_relations"):
        return []
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT pr.source_paper_id,pr.target_paper_id,pr.relation,
                   pr.score,pr.evidence,pr.created_at,pr.updated_at
            FROM paper_relations pr
            JOIN papers ps ON ps.id=pr.source_paper_id AND ps.user_id=?
            JOIN papers pt ON pt.id=pr.target_paper_id AND pt.user_id=?
            WHERE pr.user_id=?
            ORDER BY pr.created_at ASC
            """,
            (int(user_id), int(user_id), int(user_id)),
        ).fetchall()
    ]


def _export_feedback(conn, user_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT date_key,paper_identity_key,identity_type,title,action,
                   source_list,score_at_recommend,created_at
            FROM daily_recommend_feedback
            WHERE user_id=?
            ORDER BY created_at ASC,id ASC
            """,
            (int(user_id),),
        ).fetchall()
    ]


@router.get("/json")
async def export_json(
    scope: str = Query(default="all"),
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> Response:
    scope = str(scope or "").strip().lower()
    if scope not in VALID_SCOPES:
        raise HTTPException(status_code=422, detail="无效导出 scope")
    selected = (
        {"papers", "reader", "memory", "graph", "feedback"}
        if scope == "all"
        else {scope}
    )
    user_id = int(user["user_id"])
    data: dict[str, Any] = {
        "version": "2.0",
        "schema_version": 4,
        "generated_at": int(time.time()),
        "source": "PaperGraph",
    }
    with Database(db_path).read() as conn:
        if "papers" in selected:
            data["papers"] = _export_papers(conn, user_id)
        if "reader" in selected:
            data["reader"] = _export_reader(conn, user_id)
        if "memory" in selected:
            memories = _export_memories(conn, user_id)
            for item in memories:
                item.pop("metadata_json", None)
            data["memories"] = memories
        if "graph" in selected:
            data["relations"] = _export_relations(conn, user_id)
        if "feedback" in selected:
            data["feedback"] = _export_feedback(conn, user_id)

    data["summary"] = {
        "papers": len(data.get("papers", [])),
        "conversations": len(data.get("reader", {}).get("conversations", [])),
        "turns": len(data.get("reader", {}).get("turns", [])),
        "memories": len(data.get("memories", [])),
        "relations": len(data.get("relations", [])),
        "feedback": len(data.get("feedback", [])),
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    filename = f"papergraph_export_{time.strftime('%Y%m%d_%H%M%S')}.json"
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(payload)),
        },
    )

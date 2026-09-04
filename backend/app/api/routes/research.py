from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from ...repositories.research_repository import (
    ResearchPaperOwnershipError,
    ResearchRepository,
    ResearchSessionNotFound,
)
from ...services.research.multi_paper_service import MultiPaperResearchService
from ..dependencies import get_db_path
from ..deps import require_user
from ..schemas.research import (
    CreateResearchSessionRequest,
    ResearchChatRequest,
)


router = APIRouter(prefix="/research", tags=["协同研究"])


@router.post("/sessions")
async def create_research_session(
    body: CreateResearchSessionRequest,
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    try:
        session = await run_in_threadpool(
            ResearchRepository(db_path).create_session,
            user_id=int(user["user_id"]),
            paper_ids=body.paper_ids,
            title=body.title,
        )
        return {"success": True, "session": session}
    except ResearchPaperOwnershipError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/sessions")
async def list_research_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    items = await run_in_threadpool(
        ResearchRepository(db_path).list_sessions,
        user_id=int(user["user_id"]),
        limit=int(limit),
    )
    return {"success": True, "count": len(items), "items": items}


@router.get("/sessions/{session_id}")
async def get_research_session(
    session_id: str,
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    session = await run_in_threadpool(
        ResearchRepository(db_path).get_session,
        user_id=int(user["user_id"]),
        session_id=session_id,
    )
    if not session:
        raise HTTPException(status_code=404, detail="协同研究会话不存在")
    return {"success": True, "session": session}


@router.post("/sessions/{session_id}/chat")
async def research_chat(
    session_id: str,
    body: ResearchChatRequest,
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    try:
        result = await run_in_threadpool(
            MultiPaperResearchService(db_path).chat,
            user_id=int(user["user_id"]),
            session_id=session_id,
            user_message=body.user_message,
        )
        return {"success": True, **result}
    except ResearchSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

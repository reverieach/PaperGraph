
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from ...models.schemas import (
    PaperReaderChatRequest,
    PaperReaderChatResponse,
    PaperReaderHistoryItem,
    PaperReaderHistoryResponse,
    PaperReaderOpeningRequest,
    PaperReaderOpeningResponse,
)
from ..dependencies import get_database
from ..deps import require_user, check_rate_limit
from ...utils.common import safe_http_500
from ...services.reader.paper_reader_service import PaperReaderService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI 分析"])


def get_paper_reader_service() -> PaperReaderService:
    db = get_database()
    return PaperReaderService(db=db)


@router.post("/paper-reader/opening", response_model=PaperReaderOpeningResponse)
async def paper_reader_opening(
    request: Request,
    body: PaperReaderOpeningRequest,
    background_tasks: BackgroundTasks,
    service: PaperReaderService = Depends(get_paper_reader_service),
    user: dict = Depends(require_user),
):
    check_rate_limit(
        f"user:{int(user['user_id'])}:"
        f"{request.client.host if request.client else 'unknown'}",
        max_requests=20,
    )
    try:
        result = await service.get_opening(
            user_id=int(user["user_id"]),
            paper_id=int(body.paper_id),
            conversation_id=body.conversation_id,
            background_tasks=background_tasks,
            request_id=str(getattr(request.state, "request_id", "") or ""),
        )
        return PaperReaderOpeningResponse(success=True, **result)
    except HTTPException:
        raise
    except Exception as e:
        raise safe_http_500("paper_reader_opening", e)

@router.post("/paper-reader/chat", response_model=PaperReaderChatResponse)
async def paper_reader_chat(
    request: Request,
    body: PaperReaderChatRequest,
    background_tasks: BackgroundTasks,
    service: PaperReaderService = Depends(get_paper_reader_service),
    user: dict = Depends(require_user),
):
    check_rate_limit(
        f"user:{int(user['user_id'])}:"
        f"{request.client.host if request.client else 'unknown'}",
        max_requests=15,
    )
    try:
        out = await service.process_chat(
            user_id=int(user["user_id"]),
            paper_id=int(body.paper_id),
            conversation_id=body.conversation_id,
            messages=list(body.messages or []),
            user_message=body.user_message,
            background_tasks=background_tasks,
            request_id=str(getattr(request.state, "request_id", "") or ""),
        )
        return PaperReaderChatResponse(
            success=True,
            reply=str(out.get("reply") or "").strip(),
            conversation_id=str(out["conversation_id"]),
            pdf_parsing=bool(out.get("pdf_parsing", False)),
            context_mode=str(out.get("context_mode") or "legacy_fallback"),
            degradation_flags=[
                str(flag)
                for flag in (out.get("degradation_flags") or [])
                if str(flag).strip()
            ],
            related_papers=list(out.get("related_papers") or []),
            related_hints=list(out.get("related_hints") or []),
            kg_edges=list(out.get("kg_edges") or []),
            citations=list(out.get("citations") or []),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise safe_http_500("paper_reader_chat", e)

@router.get("/paper-reader/history", response_model=PaperReaderHistoryResponse)
async def paper_reader_history(
    paper_id: int = Query(..., ge=1),
    limit: int = Query(default=200, ge=1, le=1000),
    conversation_id: str | None = Query(default=None, max_length=128),
    service: PaperReaderService = Depends(get_paper_reader_service),
    user: dict = Depends(require_user),
):
    try:
        from ...services.reader.paper_reader_history import default_conversation_id

        resolved_conversation_id = (
            str(conversation_id or "").strip()
            or default_conversation_id(int(user["user_id"]), int(paper_id))
        )
        turns = await service.get_history(
            paper_id=int(paper_id),
            limit=int(limit),
            user_id=int(user["user_id"]),
            conversation_id=resolved_conversation_id,
        )
        return PaperReaderHistoryResponse(
            success=True,
            paper_id=int(paper_id),
            conversation_id=resolved_conversation_id,
            turns=[PaperReaderHistoryItem(**t) for t in turns],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise safe_http_500("paper_reader_history", e)

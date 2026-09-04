
import logging


import anyio
from fastapi import APIRouter, Query, Request, Depends, HTTPException
from ...utils.common import safe_http_500
from ...models.schemas import (
    DeletePaperResponse,
    LibraryCategoriesResponse,
    Paper,
    PapersResponse,
    ReadStatus,
    SavePapersRequest,
    SavePapersResponse,
    LibraryGraphResponse,
    UpdatePaperRequest,
    UpdatePaperResponse,
    DailyPapersRequest,
    DailyPapersResponse,
    DailyRecommendFeedbackRequest,
    DailyRecommendFeedbackResponse,
    ReadingCalendarItem,
    ReadingLogRequest,
    ReadingCalendarResponse,
)

from ...services.papers.papers_converters import api_paper_to_litpaper, litpaper_to_api_paper

from ...services.papers.papers_helpers import (
    daily_paper_identity_sig,
)
from ...services.graph.graph_service import build_library_graph
from ...services.papers.papers_library_service import (
    build_library_pdf_response_service,
    delete_paper_by_id,
    get_library as get_library_service,
    get_paper_by_id,
    list_library_categories as list_library_categories_service,
    save_papers as save_papers_service,
    update_paper_by_id,
)
from ...services.daily.daily_auto_refresh import get_daily_compute_lock
from ...services.daily.daily_service import (
    compute_daily_papers as compute_daily_service,
    read_daily_cached_or_204 as get_daily_cached_or_204_service,
    record_user_daily_feedback as record_daily_feedback_service,
)
from ...settings import get_settings
from ..dependencies import get_database, get_db_path, get_searcher
from ..deps import require_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/papers", tags=["文献管理"])

class DailyServices:
    def __init__(self, db_path=Depends(get_db_path), searcher=Depends(get_searcher)):
        self.db_path = db_path
        self.searcher = searcher

@router.get("/graph/library", response_model=LibraryGraphResponse)
def library_graph(
    limit: int = Query(default=200, ge=1, le=1000),
    category: str | None = Query(default=None),
    include_authors: bool = Query(default=False),
    include_keywords: bool = Query(default=False),
    relation_edge_limit: int = Query(default=400, ge=0, le=5000),
    focus_paper_id: int | None = Query(default=None, ge=1),
    db=Depends(get_database),
    user: dict = Depends(require_user),
):
    try:
        return build_library_graph(
            db=db,
            limit=int(limit),
            category=category,
            include_authors=bool(include_authors),
            include_keywords=bool(include_keywords),
            relation_edge_limit=int(relation_edge_limit),
            focus_paper_id=focus_paper_id,
            user_id=int(user["user_id"]),
        )
    except Exception as e:
        logger.exception("GET /api/papers/graph/library 失败")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/library/categories", response_model=LibraryCategoriesResponse)
def list_library_categories(
    db=Depends(get_database),
    user: dict = Depends(require_user),
):
    return list_library_categories_service(
        db=db,
        user_id=int(user["user_id"]),
    )

@router.get("/library", response_model=PapersResponse)
def get_library(
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    q: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    read_status: ReadStatus | None = None,
    tags: str | None = Query(default=None, description="逗号分隔标签"),
    category: str | None = Query(default=None, description="领域筛选"),
    db=Depends(get_database),
    user: dict = Depends(require_user),
):
    return get_library_service(
        db=db,
        litpaper_to_api_paper_fn=litpaper_to_api_paper,
        limit=limit,
        offset=offset,
        q=q,
        year_from=year_from,
        year_to=year_to,
        read_status=read_status,
        tags=tags,
        category=category,
        user_id=user["user_id"],
    )

@router.post("/save", response_model=SavePapersResponse)
async def save_papers(
    request: SavePapersRequest,
    db=Depends(get_database),
    user: dict = Depends(require_user),
):
    try:
        return await save_papers_service(
            db=db,
            request=request,
            api_to_lit_fn=api_paper_to_litpaper,
            litpaper_to_api_paper_fn=litpaper_to_api_paper,
            user_id=int(user["user_id"]),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise safe_http_500("save_papers", e)


@router.post("/{paper_id}/ingest")
async def enqueue_paper_ingest(
    paper_id: int,
    db=Depends(get_database),
    db_path=Depends(get_db_path),
    user: dict = Depends(require_user),
):
    """Queue canonical PDF parsing/chunking/indexing for one owned paper."""

    try:
        from ...services.ingest.queue import IngestEnqueueError, enqueue_owned_paper_ingest

        result = enqueue_owned_paper_ingest(
            db=db,
            db_path=db_path,
            user_id=int(user["user_id"]),
            paper_id=int(paper_id),
        )
    except IngestEnqueueError as exc:
        detail = str(exc)
        if detail == "paper does not belong to user":
            raise HTTPException(status_code=404, detail="文献不存在") from exc
        raise HTTPException(status_code=409, detail=f"无法创建 PDF 入库任务：{detail}") from exc
    return {"success": True, **result.to_dict()}


@router.get("/{paper_id}/ingest")
def get_paper_ingest_status(
    paper_id: int,
    db_path=Depends(get_db_path),
    user: dict = Depends(require_user),
):
    from ...repositories.document_repository import DocumentRepository

    status = DocumentRepository(db_path).get_paper_ingest_status(
        user_id=int(user["user_id"]),
        paper_id=int(paper_id),
    )
    if status is None:
        raise HTTPException(status_code=404, detail="文献不存在")
    return {"success": True, **status}


@router.get("/{paper_id}/ingest/{job_id}")
def get_paper_ingest_job(
    paper_id: int,
    job_id: str,
    db_path=Depends(get_db_path),
    user: dict = Depends(require_user),
):
    from ...repositories.document_repository import DocumentRepository

    job = DocumentRepository(db_path).get_ingest_job(
        user_id=int(user["user_id"]), job_id=str(job_id)
    )
    if not job or int(job.get("paper_id") or 0) != int(paper_id):
        raise HTTPException(status_code=404, detail="解析任务不存在")
    return {"success": True, "job": job}

@router.get("/daily")
async def daily_papers_get(
    db_path=Depends(get_db_path),
    user: dict = Depends(require_user),
):
    logger.info("HTTP GET /api/papers/daily")
    return await get_daily_cached_or_204_service(
        db_path=db_path,
        user_id=int(user["user_id"]),
    )

@router.post("/daily", response_model=DailyPapersResponse)
async def daily_papers(
    body: DailyPapersRequest,
    services: DailyServices = Depends(),
    settings=Depends(get_settings),
    user: dict = Depends(require_user),
):
    logger.info(
        "HTTP POST /api/papers/daily force_refresh=%s",
        getattr(body, "force_refresh", False),
    )
    lock = get_daily_compute_lock()
    async with lock:
        try:
            with anyio.fail_after(180.0):
                resp = await compute_daily_service(
                body=body, db_path=services.db_path, searcher=services.searcher,
                daily_paper_identity_sig_fn=daily_paper_identity_sig,
                    daily_arxiv_cs_categories=settings.get_daily_arxiv_cs_categories(),
                    papergraph_to_api_fn=litpaper_to_api_paper,
                    logger=logger,
                    user_id=int(user["user_id"]),
                )
        except TimeoutError:
            err_msg = "每日论文计算超时（>180s），请稍后重试或缩小范围"
            raise HTTPException(status_code=504, detail=err_msg)
        except HTTPException:
            raise
        except Exception as e:
            raise safe_http_500("daily_papers", e)
        else:
            return resp

@router.post("/reading/log")
def log_reading_session(
    body: ReadingLogRequest,
    db_path=Depends(get_db_path),
    user: dict = Depends(require_user),
):
    from ...services.reading_log.log import append_session
    append_session(
        db_path,
        paper_id=int(body.paper_id),
        duration_sec=int(body.duration_sec),
        client_ts=int(body.client_ts) if body.client_ts is not None else None,
        user_id=int(user["user_id"]),
    )
    return {"success": True}

@router.get("/reading/calendar", response_model=ReadingCalendarResponse)
def reading_calendar(
    days: int = Query(default=180, ge=7, le=366),
    db_path=Depends(get_db_path),
    user: dict = Depends(require_user),
):
    from ...services.reading_log.log import list_daily_aggregate
    items = list_daily_aggregate(
        db_path,
        days=int(days),
        user_id=int(user["user_id"]),
    )
    return ReadingCalendarResponse(success=True, days=int(days),
                                   items=[ReadingCalendarItem(**x) for x in items])

@router.get("/{paper_id}/library-pdf")
async def get_paper_library_pdf(
    paper_id: int,
    request: Request,
    db_path=Depends(get_db_path),
    user: dict = Depends(require_user),
):
    return build_library_pdf_response_service(
        paper_id=paper_id,
        request=request,
        db_path=db_path,
        logger_obj=logger,
        user_id=int(user["user_id"]),
    )

@router.get("/{paper_id}", response_model=Paper)
def get_paper(
    paper_id: int,
    db=Depends(get_database),
    user: dict = Depends(require_user),
):
    return get_paper_by_id(
        db=db,
        paper_id=paper_id,
        litpaper_to_api_paper_fn=litpaper_to_api_paper,
        user_id=int(user["user_id"]),
    )

@router.put("/{paper_id}", response_model=UpdatePaperResponse)
def update_paper(
    paper_id: int,
    body: UpdatePaperRequest,
    db=Depends(get_database),
    user: dict = Depends(require_user),
):
    return update_paper_by_id(
        db=db,
        paper_id=paper_id,
        body=body,
        user_id=int(user["user_id"]),
    )

@router.delete("/{paper_id}", response_model=DeletePaperResponse)
def delete_paper(
    paper_id: int,
    db=Depends(get_database),
    user: dict = Depends(require_user),
):
    return delete_paper_by_id(
        db=db,
        paper_id=paper_id,
        user_id=int(user["user_id"]),
    )

@router.post("/daily/feedback", response_model=DailyRecommendFeedbackResponse)
async def record_daily_recommend_feedback(
    body: DailyRecommendFeedbackRequest,
    db_path=Depends(get_db_path),
    user: dict = Depends(require_user),
):
    try:
        return await record_daily_feedback_service(
            body=body,
            db_path=db_path,
            user_id=int(user["user_id"]),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise safe_http_500("record_daily_recommend_feedback", e)

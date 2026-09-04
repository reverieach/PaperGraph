import asyncio
import logging
import threading
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..settings import configure_logging, get_settings, print_config, validate_config
from ..services.graph.kg_relations import get_kg_metrics
from .routes import paper_routes, paper_reader_routes, search_routes
from .routes import export as export_routes
from .routes import memory as memory_routes
from .routes import auth as auth_routes
from .routes import research as research_routes

settings = get_settings()
logger = logging.getLogger(__name__)

class _MeaningfulActivityMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        request.state.request_id = request.headers.get(
            "X-Request-ID",
            uuid.uuid4().hex,
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        try:
            from ..services.daily.daily_auto_refresh import touch_meaningful_activity_if_needed

            touch_meaningful_activity_if_needed(request.app, request.method, request.url.path)
        except Exception:
            pass
        return response

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    logger.info("%s", "=" * 60)
    logger.info("📚 %s v%s", settings.app_name, settings.app_version)
    logger.info("%s", "=" * 60)

    print_config()

    try:
        validate_config()
        logger.info("✅ 配置验证通过")
    except ValueError as e:
        logger.error("❌ 配置验证失败: %s", e)
        raise

    app.state.last_meaningful_activity_monotonic = None
    app.state.ingest_worker_task = None
    app.state.ingest_worker_id = None
    daily_refresh_task: asyncio.Task | None = None
    ingest_worker_task: asyncio.Task | None = None
    ingest_stop_event: threading.Event | None = None

    from ..infrastructure.db import run_migrations
    import os

    db_path = os.path.join(settings.data_dir, "papers.db")
    run_migrations(db_path)
    logger.info("✅ 数据库迁移与 schema 校验通过")

    if settings.rag_ingest_worker_enabled:
        from ..services.ingest.factory import build_ingest_worker

        ingest_stop_event = threading.Event()
        ingest_worker = build_ingest_worker(db_path)
        ingest_worker_id = f"api-worker-{uuid.uuid4().hex[:12]}"
        ingest_worker_task = asyncio.create_task(
            asyncio.to_thread(
                ingest_worker.run_forever,
                worker_id=ingest_worker_id,
                stop_event=ingest_stop_event,
            ),
            name="papergraph-ingest-worker",
        )
        app.state.ingest_worker_task = ingest_worker_task
        app.state.ingest_worker_id = ingest_worker_id
        # The API-embedded worker is useful for a single-process local demo,
        # but production/long-running development should launch the explicit
        # worker entry point so HTTP reloads do not interrupt PDF parsing.
        logger.warning("⚠️ 开发模式内嵌 PDF 入库 worker 已启动；正式运行请使用独立 worker 进程")
    else:
        logger.info("PDF 入库 worker 未内嵌启动；请使用独立 worker 进程消费持久化任务")

    # Daily recommendations are user-scoped. A process-global background refresh
    # has no authenticated owner, so it is intentionally disabled in phase 1.
    logger.info("每日论文后台自动刷新已禁用；由用户请求触发 user-scoped 刷新")

    logger.info("%s", "=" * 60)

    yield

    logger.info("%s", "=" * 60)
    logger.info("👋 应用正在关闭...")
    if ingest_stop_event is not None:
        ingest_stop_event.set()
    if ingest_worker_task is not None:
        try:
            await asyncio.wait_for(ingest_worker_task, timeout=5.0)
        except TimeoutError:
            ingest_worker_task.cancel()
            logger.warning("PDF 入库 worker 未在 5 秒内退出")
        except Exception:
            logger.warning("PDF 入库 worker 异常退出", exc_info=True)
    if daily_refresh_task is not None:
        daily_refresh_task.cancel()
        try:
            await daily_refresh_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("每日论文后台任务结束异常", exc_info=True)
    logger.info("%s", "=" * 60)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=settings.description,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(_MeaningfulActivityMiddleware)

app.include_router(paper_routes.router, prefix="/api")
app.include_router(paper_reader_routes.router, prefix="/api")
app.include_router(search_routes.router, prefix="/api")
app.include_router(export_routes.router, prefix="/api")
app.include_router(memory_routes.router, prefix="/api")
app.include_router(auth_routes.router, prefix="/api")
app.include_router(research_routes.router, prefix="/api")

@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs_enabled": False,
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
        "kg_metrics": get_kg_metrics(),
    }


@app.get("/health/capabilities")
async def health_capabilities(request: Request):
    """Expose safe RAG readiness facts without leaking keys or local paths."""

    from ..core.runtime_capabilities import collect_runtime_capabilities

    worker_task = getattr(request.app.state, "ingest_worker_task", None)
    embedded_alive = None if worker_task is None else not worker_task.done()
    return {
        "status": "healthy",
        "capabilities": collect_runtime_capabilities(
            embedded_worker_alive=embedded_alive,
        ),
    }

def _error_code(status_code: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "AUTH_REQUIRED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "INVALID_REQUEST",
        429: "RATE_LIMITED",
        500: "INTERNAL_ERROR",
        503: "SERVICE_UNAVAILABLE",
    }.get(int(status_code), "REQUEST_FAILED")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "detail": exc.detail,
            "error_code": _error_code(exc.status_code),
            "request_id": getattr(request.state, "request_id", None),
        },
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "detail": jsonable_encoder(exc.errors()),
            "error_code": "INVALID_REQUEST",
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", None)
    logger.exception(
        "未处理异常: %s %s request_id=%s",
        request.method,
        request.url.path,
        request_id,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": "服务器内部错误",
            "error_code": "INTERNAL_ERROR",
            "request_id": request_id,
        },
    )

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )

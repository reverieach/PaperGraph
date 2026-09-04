from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from ..dependencies import get_db_path
from ..deps import require_user
from ..schemas.memory import (
    CommitMemoryDraftRequest,
    CreateMemoryDraftRequest,
    CreateUserMemoryRequest,
)
from ...repositories.memory_repository import (
    MemoryConflictError,
    MemoryOwnershipError,
    MemoryRepository,
)
from ...services.memory.memory_draft_service import (
    MemoryDraftError,
    MemoryDraftService,
)


router = APIRouter(tags=["记忆系统"])


@router.post("/papers/{paper_id}/memory-drafts")
async def create_memory_draft(
    paper_id: int,
    body: CreateMemoryDraftRequest,
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    try:
        draft = await run_in_threadpool(
            MemoryDraftService(db_path).generate_draft,
            user_id=int(user["user_id"]),
            paper_id=int(paper_id),
            conversation_id=body.conversation_id,
            from_turn_id=body.from_turn_id,
            to_turn_id=body.to_turn_id,
        )
        return {"success": True, "draft": draft}
    except MemoryDraftError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/memory-drafts/{draft_id}")
async def get_memory_draft(
    draft_id: str,
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    draft = await run_in_threadpool(
        MemoryRepository(db_path).get_draft,
        user_id=int(user["user_id"]),
        draft_id=draft_id,
    )
    if not draft:
        raise HTTPException(status_code=404, detail="Memory 草稿不存在")
    return {"success": True, "draft": draft}


@router.post("/memory-drafts/{draft_id}/cancel")
async def cancel_memory_draft(
    draft_id: str,
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    try:
        draft = await run_in_threadpool(
            MemoryRepository(db_path).cancel_draft,
            user_id=int(user["user_id"]),
            draft_id=draft_id,
        )
        return {"success": True, "status": draft["status"]}
    except MemoryOwnershipError as exc:
        raise HTTPException(status_code=404, detail="Memory 草稿不存在") from exc
    except MemoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/memory-drafts/{draft_id}/commit")
async def commit_memory_draft(
    draft_id: str,
    body: CommitMemoryDraftRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    try:
        result = await run_in_threadpool(
            MemoryRepository(db_path).commit_draft,
            user_id=int(user["user_id"]),
            draft_id=draft_id,
            paper_items=[
                item.model_dump(mode="json") for item in body.paper_items
            ],
            accepted_user_items=[
                item.model_dump(mode="json")
                for item in body.accepted_user_items
            ],
            idempotency_key=idempotency_key,
        )
        return {"success": True, **result}
    except MemoryOwnershipError as exc:
        raise HTTPException(status_code=404, detail="Memory 草稿不存在") from exc
    except MemoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/papers/{paper_id}/memories")
async def list_paper_memories(
    paper_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    repository = MemoryRepository(db_path)
    items = await run_in_threadpool(
        repository.list_memories,
        user_id=int(user["user_id"]),
        scope_type="paper",
        scope_id=str(paper_id),
        limit=int(limit),
    )
    return {"success": True, "count": len(items), "items": items}


@router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: str,
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    deleted = await run_in_threadpool(
        MemoryRepository(db_path).delete_memory,
        user_id=int(user["user_id"]),
        memory_id=memory_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory 不存在")
    return {"success": True}


@router.get("/memory/stats")
async def memory_stats(
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    stats = await run_in_threadpool(
        MemoryRepository(db_path).stats,
        user_id=int(user["user_id"]),
    )
    return {"success": True, **stats}


@router.get("/memory/user")
async def list_user_memories(
    limit: int = Query(default=200, ge=1, le=500),
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    user_id = int(user["user_id"])
    items = await run_in_threadpool(
        MemoryRepository(db_path).list_memories,
        user_id=user_id,
        scope_type="user",
        scope_id=str(user_id),
        limit=int(limit),
    )
    return {"success": True, "count": len(items), "items": items}


@router.post("/memory/user")
async def create_user_memory(
    body: CreateUserMemoryRequest,
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    try:
        item, created = await run_in_threadpool(
            MemoryRepository(db_path).add_user_memory,
            user_id=int(user["user_id"]),
            kind=body.kind,
            content=body.content,
        )
        return {
            "success": True,
            "created": bool(created),
            "item": item,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/memory/list")
async def memory_list(
    scope_type: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    items = await run_in_threadpool(
        MemoryRepository(db_path).list_memories,
        user_id=int(user["user_id"]),
        scope_type=scope_type,
        scope_id=scope_id,
        limit=int(limit),
    )
    return {"success": True, "count": len(items), "items": items}

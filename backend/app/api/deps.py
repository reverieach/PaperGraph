"""Auth dependencies for FastAPI routes.

Usage:
    from .deps import require_user
    @router.get("/protected", dependencies=[Depends(require_user)])
    async def handler(user: dict = Depends(require_user)):
        user_id = user["user_id"]
"""
from __future__ import annotations

import time
import threading
from typing import Any

from fastapi import Depends, Header, HTTPException

from ..services.auth.user_service import AuthenticationError, get_user_from_token


async def require_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Extract and verify the JWT token from Authorization header.

    Returns {"user_id": int, "username": str}.
    Raises 401 if token is missing or invalid.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录：请先登录获取 token")

    # Strip "Bearer " prefix
    token = authorization
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    try:
        user = get_user_from_token(token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=401, detail="token 无效或已过期，请重新登录")

    return user


async def optional_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Compatibility name with strict semantics.

    Missing and invalid credentials must never fall back to a shared user.
    """
    return await require_user(authorization)


# Simple in-memory rate limiter (no Redis needed)
_rate_store: dict[str, list[float]] = {}
_rate_store_lock = threading.Lock()
_RATE_WINDOW = 60  # seconds
_RATE_MAX = 30  # max requests per window per IP


def check_rate_limit(ip: str, max_requests: int = _RATE_MAX, window: int = _RATE_WINDOW) -> None:
    """Simple sliding-window rate limiter. Raises 429 if exceeded."""
    now = time.time()
    key = ip
    with _rate_store_lock:
        history = [
            timestamp
            for timestamp in _rate_store.get(key, [])
            if now - timestamp < window
        ]
        if len(history) >= max_requests:
            raise HTTPException(
                status_code=429,
                detail="请求过于频繁，请稍后再试",
            )
        history.append(now)
        _rate_store[key] = history

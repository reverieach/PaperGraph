"""User-scoped, deterministic short-lived negative-feedback signals.

This is deliberately not a long-term Memory system.  A daily-paper ``skip``
is weak feedback, so it is recorded with a TTL for audit/future ranking only.
There is no LLM summarisation and no automatic promotion into a user profile.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from ...infrastructure.db import Database

logger = logging.getLogger(__name__)


def record_skip_negative_pref(
    db_path: str,
    *,
    user_id: int,
    identity_key: str,
    title: str,
    source: str | None = None,
    keywords: list[str | None] | None = None,
    category: str | None = None,
    ttl_days: int = 14,
) -> bool:
    """Upsert one revocable, user-scoped skip signal without invoking an LLM."""

    normalized_user_id = int(user_id)
    normalized_identity = str(identity_key or "").strip()[:160]
    if normalized_user_id <= 0 or not normalized_identity:
        return False
    ttl = max(1, min(60, int(ttl_days or 14)))
    now = int(time.time())
    payload: dict[str, Any] = {
        "keywords": [
            str(value).strip()[:64]
            for value in (keywords or [])
            if str(value or "").strip()
        ][:20],
        "source": str(source or "").strip()[:80],
        "category": str(category or "").strip()[:120],
        "recorded_by": "daily_skip_v1",
    }
    try:
        with Database(db_path).transaction() as conn:
            conn.execute(
                "DELETE FROM negative_pref_memory WHERE user_id=? AND expires_at<=?",
                (normalized_user_id, now),
            )
            conn.execute(
                """
                INSERT INTO negative_pref_memory(
                    user_id,created_at,expires_at,identity_key,title,source,
                    category,payload_json,revoked_at
                ) VALUES(?,?,?,?,?,?,?,?,NULL)
                ON CONFLICT(user_id, identity_key) DO UPDATE SET
                    created_at=excluded.created_at,
                    expires_at=excluded.expires_at,
                    title=excluded.title,
                    source=excluded.source,
                    category=excluded.category,
                    payload_json=excluded.payload_json,
                    revoked_at=NULL
                """,
                (
                    normalized_user_id,
                    now,
                    now + ttl * 86400,
                    normalized_identity,
                    str(title or "").strip()[:400] or None,
                    payload["source"] or None,
                    payload["category"] or None,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )
        return True
    except Exception:
        logger.warning(
            "record_user_scoped_negative_pref_failed",
            extra={"user_id": normalized_user_id, "identity_key": normalized_identity},
            exc_info=True,
        )
        return False


__all__ = ["record_skip_negative_pref"]

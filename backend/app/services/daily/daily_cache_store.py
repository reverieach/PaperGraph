from __future__ import annotations

import json
import time
from typing import Any

from ...infrastructure.db import Database


def get_cache(
    db_path: str,
    *,
    user_id: int,
    date_key: str,
    cache_key: str,
) -> dict[str, Any] | None:
    """Read a user-scoped daily cache entry and record a cache hit."""

    with Database(db_path).transaction() as conn:
        row = conn.execute(
            """
            SELECT payload_json FROM daily_papers_cache
            WHERE user_id=? AND date_key=? AND cache_key=?
            """,
            (int(user_id), str(date_key), str(cache_key)),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["payload_json"] or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict):
            return None
        conn.execute(
            """
            UPDATE daily_papers_cache
            SET hit_count=hit_count+1, updated_at=?
            WHERE user_id=? AND date_key=? AND cache_key=?
            """,
            (int(time.time()), int(user_id), str(date_key), str(cache_key)),
        )
        return payload


def set_cache(
    db_path: str,
    *,
    user_id: int,
    date_key: str,
    cache_key: str,
    payload: dict[str, Any],
) -> None:
    """Upsert a user-scoped daily cache entry; schema is owned by migrations."""

    now = int(time.time())
    with Database(db_path).transaction() as conn:
        conn.execute(
            """
            INSERT INTO daily_papers_cache(
                user_id,date_key,cache_key,payload_json,created_at,updated_at,hit_count
            ) VALUES(?,?,?,?,?,?,0)
            ON CONFLICT(user_id,date_key,cache_key) DO UPDATE SET
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                int(user_id),
                str(date_key),
                str(cache_key),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
                now,
            ),
        )

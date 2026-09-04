from __future__ import annotations

import logging
import time

from ...infrastructure.db import Database

logger = logging.getLogger(__name__)


def get_cached_opening(
    db_path: str,
    paper_id: int,
    *,
    user_id: int,
    max_age_hours: int = 72,
) -> tuple[str | None, bool]:
    """Fetch a cached opening only from the authenticated user's scope."""

    if not db_path:
        return None, False
    now = int(time.time())
    try:
        with Database(db_path).transaction() as conn:
            row = conn.execute(
                """
                SELECT opening,updated_at FROM paper_opening_cache
                WHERE user_id=? AND paper_id=?
                """,
                (int(user_id), int(paper_id)),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO paper_opening_cache(
                        user_id,paper_id,opening,updated_at,miss_count,last_miss_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (int(user_id), int(paper_id), "", 0, 1, now),
                )
                return None, False
            opening = str(row["opening"] or "").strip()
            updated_at = int(row["updated_at"] or 0)
            fresh = bool(
                opening
                and updated_at
                and (now - updated_at) <= int(max_age_hours) * 3600
            )
            if opening:
                conn.execute(
                    """
                    UPDATE paper_opening_cache
                    SET hit_count=hit_count+1,last_hit_at=?
                    WHERE user_id=? AND paper_id=?
                    """,
                    (now, int(user_id), int(paper_id)),
                )
            else:
                conn.execute(
                    """
                    UPDATE paper_opening_cache
                    SET miss_count=miss_count+1,last_miss_at=?
                    WHERE user_id=? AND paper_id=?
                    """,
                    (now, int(user_id), int(paper_id)),
                )
            return (opening or None), fresh
    except Exception:
        logger.warning(
            "reader_opening_cache_get_failed",
            extra={"user_id": int(user_id), "paper_id": int(paper_id)},
            exc_info=True,
        )
        return None, False


def set_cached_opening(
    db_path: str,
    paper_id: int,
    opening: str,
    *,
    user_id: int,
) -> None:
    if not db_path:
        return
    now = int(time.time())
    try:
        with Database(db_path).transaction() as conn:
            conn.execute(
                """
                INSERT INTO paper_opening_cache(user_id,paper_id,opening,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(user_id,paper_id) DO UPDATE SET
                    opening=excluded.opening,
                    updated_at=excluded.updated_at
                """,
                (int(user_id), int(paper_id), str(opening or "").strip(), now),
            )
    except Exception:
        logger.warning(
            "reader_opening_cache_set_failed",
            extra={"user_id": int(user_id), "paper_id": int(paper_id)},
            exc_info=True,
        )

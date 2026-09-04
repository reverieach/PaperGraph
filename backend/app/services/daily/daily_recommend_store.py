from __future__ import annotations

import time
from collections.abc import Iterable

from ...infrastructure.db import Database
from ...utils.common import normalize_arxiv_id as _norm_arxiv_id


def record_arxiv_recommendations(
    db_path: str,
    *,
    user_id: int,
    date_key: str,
    items: Iterable[tuple[str | None, str]],
) -> int:
    """Persist a recommendation audit trail inside the authenticated user scope."""

    now = int(time.time())
    normalized_user_id = int(user_id)
    with Database(db_path).transaction() as conn:
        count = 0
        for arxiv_id, title in items:
            aid = _norm_arxiv_id(arxiv_id)
            label = (title or "").strip()
            conn.execute(
                """
                INSERT INTO daily_recommendations(
                    user_id,date_key,source,arxiv_id,title,created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    normalized_user_id,
                    str(date_key),
                    "arxiv",
                    aid,
                    label[:400] if label else None,
                    now,
                ),
            )
            count += 1
    return count

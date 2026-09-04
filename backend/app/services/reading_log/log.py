from __future__ import annotations

import datetime as dt
import time

from ...infrastructure.db import Database


def append_session(
    db_path: str,
    *,
    user_id: int,
    paper_id: int,
    duration_sec: int,
    client_ts: int | None = None,
) -> None:
    if int(duration_sec or 0) <= 0:
        return
    duration = min(int(duration_sec), 86400)
    event_ts = int(client_ts) if client_ts else int(time.time())
    day_key = dt.datetime.fromtimestamp(event_ts).strftime("%Y-%m-%d")
    with Database(db_path).transaction() as conn:
        owner = conn.execute(
            "SELECT 1 FROM papers WHERE id=? AND user_id=?",
            (int(paper_id), int(user_id)),
        ).fetchone()
        if not owner:
            raise LookupError("paper not found")
        conn.execute(
            """
            INSERT INTO paper_reading_sessions(
                user_id,paper_id,duration_sec,day_key,created_at
            ) VALUES(?,?,?,?,?)
            """,
            (
                int(user_id),
                int(paper_id),
                duration,
                day_key,
                int(time.time()),
            ),
        )


def list_daily_aggregate(
    db_path: str,
    *,
    user_id: int,
    days: int = 180,
) -> list[dict[str, int | str]]:
    days = max(7, min(int(days or 180), 366))
    start = dt.datetime.fromtimestamp(
        int(time.time()) - (days - 1) * 86400
    ).strftime("%Y-%m-%d")
    with Database(db_path).read() as conn:
        rows = conn.execute(
            """
            SELECT day_key, SUM(duration_sec) AS seconds, COUNT(*) AS sessions
            FROM paper_reading_sessions
            WHERE user_id=? AND day_key>=?
            GROUP BY day_key
            ORDER BY day_key
            """,
            (int(user_id), start),
        ).fetchall()
    return [
        {
            "date": str(row["day_key"]),
            "seconds": int(row["seconds"] or 0),
            "sessions": int(row["sessions"] or 0),
        }
        for row in rows
    ]

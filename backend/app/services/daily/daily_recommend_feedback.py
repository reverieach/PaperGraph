
from __future__ import annotations

import contextlib
import re
import time
from collections import Counter

from ...infrastructure.db import Database
from ...models.schemas import FeedbackActionEnum as FeedbackAction

@contextlib.contextmanager
def _conn(db_path: str):
    with Database(db_path).transaction() as conn:
        yield conn

def record_feedback(
    db_path: str,
    *,
    date_key: str,
    paper_identity_key: str,
    identity_type: str,
    user_id: int,
    title: str | None = None,
    action: FeedbackAction,
    source_list: str | None = None,
    score_at_recommend: float | None = None,
    keywords: list[str | None] = None,
    category: str | None = None,
) -> bool:
    try:
        now = int(time.time())
        with _conn(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO daily_recommend_feedback(user_id,date_key,paper_identity_key,identity_type,title,action,source_list,score_at_recommend,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (int(user_id), str(date_key), str(paper_identity_key), str(identity_type),
                 (title or "")[:400] if title else None, str(action.value),
                 source_list, float(score_at_recommend) if score_at_recommend is not None else None, now),
            )
        return True
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(f"记录推荐反馈失败: {e}")
        return False

def get_skipped_papers(
    db_path: str,
    *,
    user_id: int,
    days: int = 30,
    include_shown: bool = True,
) -> set[str]:
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    actions = ("skip", "shown") if include_shown else ("skip",)
    placeholders = ",".join("?" for _ in actions)
    with _conn(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT DISTINCT paper_identity_key FROM daily_recommend_feedback "
            f"WHERE user_id=? AND date_key>=? AND action IN ({placeholders})",
            (int(user_id), cutoff, *actions),
        )
        skipped = {str(row[0]) for row in cur.fetchall()}
    return skipped


def clear_daily_shown_for_date(
    db_path: str,
    date_key: str,
    *,
    user_id: int,
) -> int:
    """手动刷新时清除当日 shown 记录，避免候选池被永久锁死。"""
    with _conn(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM daily_recommend_feedback "
            "WHERE user_id=? AND date_key=? AND action='shown'",
            (int(user_id), str(date_key)),
        )
        return int(cur.rowcount or 0)

def record_daily_shown_papers(
    db_path: str,
    date_key: str,
    papers: list[dict[str, str]],
    *,
    user_id: int,
) -> None:
    if not papers:
        return
    now = int(time.time())
    with _conn(db_path) as conn:
        conn.cursor().executemany(
            """INSERT OR IGNORE INTO daily_recommend_feedback(user_id,date_key,paper_identity_key,identity_type,title,action,source_list,score_at_recommend,created_at)
            VALUES(?, ?,?,'title_hash',?,'shown','daily',0.0,?)""",
            [
                (
                    int(user_id),
                    date_key,
                    p.get("identity_key", ""),
                    p.get("title", ""),
                    now,
                )
                for p in papers
            ],
        )

def get_high_value_keywords_from_feedback(
    db_path: str,
    *,
    user_id: int,
    days: int = 21,
    top_n: int = 20,
) -> set[str]:
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    with _conn(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT title FROM daily_recommend_feedback "
            "WHERE user_id=? AND date_key>=? "
            "AND action IN ('click','save','read') AND title IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 200",
            (int(user_id), cutoff),
        )
        titles = [str(row[0]) for row in cur.fetchall() if row[0]]

    def _extract_tokens(text: str) -> list[str]:
        t = (text or "").lower()
        t = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", t)
        tokens = [x.strip() for x in t.split() if x.strip()]
        stop = {
            "the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "on",
            "we", "our", "is", "are", "be", "via", "from", "this", "that",
            "using", "use", "based", "towards", "paper", "propose", "method",
            "learning", "network", "model", "deep", "neural",
        }
        return [x for x in tokens if x not in stop and len(x) >= 4][:50]

    all_tokens = []
    for t in titles:
        all_tokens.extend(_extract_tokens(t))

    freq = Counter(all_tokens)
    top_keywords = {w for w, _ in freq.most_common(top_n)}
    return top_keywords

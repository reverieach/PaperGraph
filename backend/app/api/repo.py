
from __future__ import annotations

from dataclasses import dataclass

from ..infrastructure.db import Database

@dataclass(frozen=True)
class RelationRepository:
    db_path: str

    def fetch_relation_rows(
        self,
        *,
        focus_id: int | None,
        paper_ids: set[int | None] | None,
        limit: int,
        user_id: int,
    ) -> list[tuple[int, int, str, float, str]]:
        if int(limit) <= 0:
            return []
        rows: list[tuple[int, int, str, float, str]] = []
        with Database(self.db_path).read() as conn:
            cur = conn.cursor()
            if focus_id is not None:
                cur.execute(
                    """SELECT pr.source_paper_id, pr.target_paper_id, pr.relation, pr.score, pr.evidence
                    FROM paper_relations pr
                    JOIN papers ps ON ps.id=pr.source_paper_id AND ps.user_id=?
                    JOIN papers pt ON pt.id=pr.target_paper_id AND pt.user_id=?
                    WHERE pr.user_id=?
                      AND (pr.source_paper_id = ? OR pr.target_paper_id = ?)
                    ORDER BY pr.score DESC, pr.updated_at DESC LIMIT ?""",
                    (
                        int(user_id),
                        int(user_id),
                        int(user_id),
                        int(focus_id),
                        int(focus_id),
                        int(limit),
                    ),
                )
            else:
                ids = sorted(
                    int(x)
                    for x in (paper_ids or set())
                    if x is not None and int(x) > 0
                )[:400]
                if not ids:
                    return []
                placeholders = ",".join("?" for _ in ids)
                cur.execute(
                    f"""SELECT pr.source_paper_id, pr.target_paper_id, pr.relation, pr.score, pr.evidence
                    FROM paper_relations pr
                    INNER JOIN papers ps ON ps.id=pr.source_paper_id AND ps.user_id=?
                    INNER JOIN papers pt ON pt.id=pr.target_paper_id AND pt.user_id=?
                    WHERE pr.user_id=?
                      AND pr.source_paper_id IN ({placeholders})
                      AND pr.target_paper_id IN ({placeholders})
                    ORDER BY pr.score DESC, pr.updated_at DESC LIMIT ?""",
                    (
                        int(user_id),
                        int(user_id),
                        int(user_id),
                        *ids,
                        *ids,
                        int(limit),
                    ),
                )
            for sid, tid, rel, score, evidence in cur.fetchall():
                rows.append((int(sid), int(tid), str(rel or ""), float(score or 0.0), str(evidence or "")))
        return rows

    def papers_minimal_by_ids(
        self,
        paper_ids: set[int],
        *,
        user_id: int,
    ) -> dict[int, tuple[str, int | None, str | None]]:
        ids = sorted(int(x) for x in paper_ids if int(x) > 0)[:800]
        if not ids:
            return {}
        out: dict[int, tuple[str, int | None, str | None]] = {}
        with Database(self.db_path).read() as conn:
            cur = conn.cursor()
            placeholders = ",".join("?" for _ in ids)
            cur.execute(
                f"""SELECT p.id, p.title, p.year, p.category
                FROM papers p
                WHERE p.user_id=? AND p.id IN ({placeholders})""",
                (int(user_id), *ids),
            )
            for rid, title, year, cat in cur.fetchall():
                out[int(rid)] = (str(title or ""), int(year) if year is not None else None, str(cat) if cat else None)
        return out

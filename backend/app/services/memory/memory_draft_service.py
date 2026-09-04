from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ...domain.memory import MemoryDraftPayload
from ...infrastructure.db import Database
from ...repositories.memory_repository import MemoryRepository


class MemoryDraftError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise MemoryDraftError("LLM 未返回有效 JSON") from exc
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as inner:
            raise MemoryDraftError("LLM 未返回有效 JSON") from inner
    if not isinstance(parsed, dict):
        raise MemoryDraftError("LLM Memory 草稿必须是 JSON 对象")
    return parsed


class MemoryDraftService:
    def __init__(self, db_path: str, *, llm: Any | None = None) -> None:
        self.db_path = str(db_path)
        self.database = Database(self.db_path)
        self.repository = MemoryRepository(self.db_path)
        self._llm = llm

    def _get_llm(self) -> Any:
        if self._llm is not None:
            return self._llm
        from ..llm.llm_service import get_llm

        return get_llm()

    def generate_draft(
        self,
        *,
        user_id: int,
        paper_id: int,
        conversation_id: str,
        from_turn_id: int | None,
        to_turn_id: int | None,
    ) -> dict[str, Any]:
        with self.database.read() as conn:
            paper = conn.execute(
                "SELECT title FROM papers WHERE id=? AND user_id=?",
                (int(paper_id), int(user_id)),
            ).fetchone()
            conversation = conn.execute(
                """
                SELECT 1 FROM reader_conversations
                WHERE id=? AND user_id=? AND paper_id=?
                """,
                (conversation_id, int(user_id), int(paper_id)),
            ).fetchone()
            if not paper or not conversation:
                raise MemoryDraftError("论文或阅读会话不存在")
            rows = conn.execute(
                """
                SELECT id,role,content,created_at
                FROM paper_reader_turns
                WHERE user_id=? AND paper_id=? AND conversation_id=?
                  AND (? IS NULL OR id>=?)
                  AND (? IS NULL OR id<=?)
                ORDER BY id ASC
                """,
                (
                    int(user_id),
                    int(paper_id),
                    conversation_id,
                    from_turn_id,
                    from_turn_id,
                    to_turn_id,
                    to_turn_id,
                ),
            ).fetchall()
        if not rows:
            raise MemoryDraftError("指定范围内没有阅读对话")
        actual_from = int(rows[0]["id"])
        actual_to = int(rows[-1]["id"])
        allowed_ids = {int(row["id"]) for row in rows}
        snapshot = [
            {
                "id": int(row["id"]),
                "role": str(row["role"]),
                "content": str(row["content"]),
            }
            for row in rows
        ]
        snapshot_json = json.dumps(
            snapshot,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        snapshot_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
        clipped = snapshot[-40:]
        prompt = (
            "你是论文阅读记忆整理器。只根据给定对话生成候选草稿，不决定 user_id、"
            "paper_id 或最终 scope。输出一个 JSON 对象，字段必须为：paper_summary(string)、"
            "key_findings(array)、open_questions(array)、research_decisions(array)、"
            "user_memory_candidates(array)。数组项包含 content 和 evidence_turn_ids；"
            "user_memory_candidates 还包含 kind(preference|research_goal) 与 confidence(0-1)。"
            "paper_summary 控制在 400 字以内；key_findings 最多 3 条；"
            "open_questions 最多 2 条；research_decisions 最多 2 条；"
            "user_memory_candidates 最多 2 条。合并同义或高度相关内容，避免拆成大量碎片。"
            "不要输出 Markdown。\n\n"
            f"论文标题：{paper['title']}\n"
            f"对话：{json.dumps(clipped, ensure_ascii=False)}"
        )
        result = self._get_llm().chat(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1800,
        )
        try:
            payload = MemoryDraftPayload.model_validate(
                _extract_json(result.content)
            )
        except Exception as exc:
            if isinstance(exc, MemoryDraftError):
                raise
            raise MemoryDraftError(f"Memory 草稿结构校验失败: {exc}") from exc

        evidence_items = [
            *payload.key_findings,
            *payload.open_questions,
            *payload.research_decisions,
            *payload.user_memory_candidates,
        ]
        for item in evidence_items:
            if not set(item.evidence_turn_ids).issubset(allowed_ids):
                raise MemoryDraftError(
                    "LLM 返回了不属于当前会话快照的 evidence_turn_ids"
                )
        if not (
            payload.paper_summary.strip()
            or payload.key_findings
            or payload.open_questions
            or payload.research_decisions
            or payload.user_memory_candidates
        ):
            raise MemoryDraftError("LLM 返回了空 Memory 草稿")

        llm = self._get_llm()
        return self.repository.create_draft(
            user_id=int(user_id),
            paper_id=int(paper_id),
            conversation_id=conversation_id,
            from_turn_id=actual_from,
            to_turn_id=actual_to,
            payload=payload.model_dump(mode="json"),
            source_snapshot_hash=snapshot_hash,
            llm_model=str(getattr(llm, "model", "") or ""),
        )

"""Task-aware, bounded policies for composing a Reader ContextPackage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..retrieval.academic_query_planner import QueryPlan, RetrievalTask


ContextSource = Literal[
    "retrieved_chunk",
    "paper_metadata",
    "memory",
    "history",
    "tool_result",
    "legacy_pdf_fallback",
]


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Source ordering and per-source ceilings for one academic task."""

    name: str
    task: RetrievalTask
    source_order: tuple[ContextSource, ...]
    source_token_caps: dict[ContextSource, int]
    diversify_sections: bool = False
    prefer_content_type: str | None = None

    def cap_for(self, source_type: ContextSource, *, total_tokens: int) -> int:
        configured = int(self.source_token_caps.get(source_type, total_tokens))
        return max(0, min(configured, max(0, int(total_tokens))))


_DEFAULT_ORDER: tuple[ContextSource, ...] = (
    "retrieved_chunk",
    "paper_metadata",
    "memory",
    "history",
    "tool_result",
    "legacy_pdf_fallback",
)


def policy_for_query_plan(plan: QueryPlan | None) -> ContextPolicy:
    """Return a conservative policy without a second LLM classification call."""

    task: RetrievalTask = plan.task if plan is not None else "factual"
    caps: dict[ContextSource, int] = {
        "retrieved_chunk": 2_600,
        "paper_metadata": 420,
        "memory": 360,
        "history": 420,
        "tool_result": 260,
        "legacy_pdf_fallback": 2_400,
    }
    diversify = False
    preferred: str | None = None
    if task == "summary":
        caps["retrieved_chunk"] = 2_700
        caps["paper_metadata"] = 500
        caps["history"] = 240
        diversify = True
    elif task == "table":
        caps["retrieved_chunk"] = 2_800
        caps["paper_metadata"] = 260
        caps["memory"] = 220
        preferred = "table"
    elif task == "formula":
        caps["retrieved_chunk"] = 2_800
        caps["paper_metadata"] = 260
        caps["memory"] = 220
        preferred = "formula"
    elif task == "method":
        caps["retrieved_chunk"] = 2_700
        caps["paper_metadata"] = 360
        diversify = True
    elif task in {"limitation", "reference"}:
        caps["retrieved_chunk"] = 2_700
        caps["paper_metadata"] = 320

    return ContextPolicy(
        name=f"academic_{task}_v1",
        task=task,
        source_order=_DEFAULT_ORDER,
        source_token_caps=caps,
        diversify_sections=diversify,
        prefer_content_type=preferred,
    )


__all__ = ["ContextPolicy", "ContextSource", "policy_for_query_plan"]

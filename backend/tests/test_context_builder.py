from __future__ import annotations

from app.services.context.builder import DynamicContextBuilder
from app.agents.support.paper_analysis_helpers import prioritize_reader_context


def test_dynamic_context_builder_preserves_evidence_pages_and_deduplicates() -> None:
    result = DynamicContextBuilder(max_tokens=900, max_evidence=4).build(
        paper_metadata="标题：Hybrid Retrieval\n摘要：A concise abstract.",
        retrieved_chunks=[
            {
                "chunk_uid": "c1",
                "paper_id": 7,
                "document_version_id": "v1",
                "display_text": "The same evidence.",
                "section_path": ["3 Method"],
                "page_start": 2,
                "page_end": 3,
                "rrf_score": 0.03,
            },
            {
                "chunk_uid": "c2",
                "paper_id": 7,
                "display_text": "The same evidence.",
                "page_start": 2,
            },
        ],
        memories=[{"content": "用户关注可复现性"}],
        history="用户：它的召回策略是什么？",
    )
    assert result.text.index("检索证据") < result.text.index("已确认记忆")
    assert len(result.evidence) == 1
    assert "[E1] [p2,p3]" in result.text
    citation = result.citations()[0]
    assert citation["page_start"] == 2
    assert citation["page_end"] == 3


def test_dynamic_context_builder_reports_budget_drops() -> None:
    result = DynamicContextBuilder(max_tokens=512, max_evidence=10).build(
        paper_metadata="metadata " * 1000,
        retrieved_chunks=[
            {"chunk_uid": f"c{i}", "display_text": "evidence " * 100}
            for i in range(10)
        ],
        memories=[{"content": "memory " * 100}],
        history="history " * 1000,
        tool_results=["tool result " * 1000],
    )
    assert result.token_estimate <= 512
    assert result.dropped_sections
    assert all(f"[{item.evidence_id}]" in result.text for item in result.evidence)


def test_dynamic_context_builder_budgets_chinese_close_to_one_token_per_char() -> None:
    result = DynamicContextBuilder(max_tokens=512).build(
        paper_metadata="标题：" + ("中文上下文" * 500),
    )
    assert result.token_estimate <= 512
    assert result.text
    assert len(result.text) < 900


def test_reader_context_clip_keeps_multiple_rag_evidence_records() -> None:
    block = "【论文元数据】\n标题：x\n\n---\n\n【检索证据】\n" + "\n".join(
        f"[E{i}] [p{i}] evidence-{i} " + ("detail " * 200) for i in range(1, 7)
    )
    clipped = prioritize_reader_context(block, max_chars=3200)
    assert "[E1]" in clipped
    assert "[E2]" in clipped
    assert "[E3]" in clipped
    assert len(clipped) <= 3200

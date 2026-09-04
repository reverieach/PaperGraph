from __future__ import annotations

from app.services.retrieval.academic_query_planner import (
    AcademicQueryPlanner,
    build_trigram_query,
    build_unicode61_query,
)


def test_query_planner_preserves_academic_identifiers_and_builds_cjk_routes() -> None:
    plan = AcademicQueryPlanner().plan(
        "请问这篇论文中，作者为什么认为长上下文里 RAG 的中间信息更容易丢失？"
    )

    assert plan.language == "mixed"
    assert plan.task == "factual"
    assert "RAG" in plan.unicode_terms
    assert "长上下文" in plan.unicode_terms
    assert "长上下文" in plan.trigram_terms
    assert "long context" in plan.cross_language_terms
    assert "关键学术术语" in plan.dense_query
    assert '"长 上 下 文"' in build_unicode61_query(plan.unicode_terms)
    assert '"长上下文"' in build_trigram_query(plan.trigram_terms)


def test_query_planner_selects_task_specific_rerank_policy_without_translation() -> None:
    plan = AcademicQueryPlanner().plan(
        "What numerical result does Table 3 report for the RAG benchmark?"
    )

    assert plan.language == "en"
    assert plan.task == "table"
    assert plan.content_type_preferences == ("table",)
    assert "Table" in plan.unicode_terms
    assert "表格标题" in plan.rerank_instruction
    # The original language is preserved for dense retrieval.  No unverified
    # machine translation is introduced as a hidden mandatory dependency.
    assert plan.dense_query.startswith("What numerical result")


def test_query_planner_bridges_context_window_without_whole_sentence_translation() -> None:
    plan = AcademicQueryPlanner().plan("Llama 3 的上下文窗口最多是多少？")

    assert plan.language == "mixed"
    assert "context window" in plan.cross_language_terms
    assert "context window" in plan.unicode_terms
    assert plan.dense_query.startswith("Llama 3 的上下文窗口")


def test_query_planner_keeps_explicit_abstract_scope_as_a_structural_preference() -> None:
    plan = AcademicQueryPlanner().plan("论文在摘要中评估了哪些任务？")

    assert plan.section_preferences == ("摘要", "abstract")

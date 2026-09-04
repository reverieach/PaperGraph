from __future__ import annotations

import pytest

from app.services.rerank.base import RerankResult, RerankerUnavailable, validate_rerank_results


def test_rerank_validation_rejects_duplicate_or_nonfinite_scores() -> None:
    assert validate_rerank_results(
        [RerankResult(index=1, score=0.8), RerankResult(index=0, score=0.2)],
        document_count=2,
    )[0].index == 1
    with pytest.raises(RerankerUnavailable):
        validate_rerank_results(
            [RerankResult(index=0, score=0.8), RerankResult(index=0, score=0.2)],
            document_count=2,
        )


def test_dashscope_reranker_uses_flat_compatible_payload(monkeypatch) -> None:
    from app.services.rerank.dashscope_reranker import DashScopeReranker

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]}

    captured: dict = {}

    def fake_post(endpoint, **kwargs):
        captured["endpoint"] = endpoint
        captured.update(kwargs)
        return _Response()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = DashScopeReranker(api_key="secret", endpoint="https://example/reranks")
    result = provider.rerank("query", ["a", "b"], top_n=2)
    assert result[0].index == 1
    assert captured["json"]["query"] == "query"
    assert captured["json"]["documents"] == ["a", "b"]
    assert "input" not in captured["json"]

    provider.rerank("query", ["a", "b"], top_n=2, instruction="优先表格数值证据")
    assert captured["json"]["query"] == "任务要求：优先表格数值证据\n用户问题：query"

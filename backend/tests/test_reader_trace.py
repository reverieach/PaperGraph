from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from app.services.context import ContextPackage
from app.services.reader.reader_trace import ReaderRequestTrace


def test_reader_trace_is_correlatable_but_never_contains_prompt_or_evidence_text(caplog) -> None:
    package = ContextPackage(
        text="SECRET PDF EVIDENCE MUST NOT APPEAR",
        evidence=[],
        token_estimate=320,
        token_budget=800,
        tokenizer="fallback",
        policy_name="factual",
        source_counts={"retrieved_chunk": 2, "memory": 1},
        dropped_sections=["tool_result"],
        dropped_items=[{"source_type": "history"}],
    )
    prepared = SimpleNamespace(
        package=package,
        context_mode="hybrid_rag_v2",
        document_version_id="dv-secret",
        pdf_parsing=True,
        degradation_reasons=("dense_index_partial_scope",),
        memory_hit_count=1,
        memory_degradation_reasons=(),
        retrieval_trace={
            "mode": "hybrid",
            "candidate_count": 50,
            "query_language": "mixed",
            # A real service never inserts raw query text in this mapping.
            "degradation_reasons": ["dense_index_partial_scope"],
        },
    )
    trace = ReaderRequestTrace(
        request_id="req-trace-1",
        operation="chat",
        user_id=7,
        paper_id=11,
    )
    trace.record_prepared(prepared)
    trace.record_agent_result(
        reader_snap={"_evidence_registry": [object(), object()]},
        citation_count=2,
        related_count=1,
    )

    with caplog.at_level(logging.INFO):
        trace.emit(status="ok")

    message = next(record.message for record in caplog.records if "paper_reader_trace=" in record.message)
    assert "SECRET PDF EVIDENCE" not in message
    payload = json.loads(message.split("paper_reader_trace=", 1)[1])
    assert payload["request_id"] == "req-trace-1"
    assert payload["context"]["evidence_count"] == 0
    assert payload["context"]["token_estimate"] == 320
    assert payload["retrieval"]["candidate_count"] == 50
    assert payload["agent"]["registered_evidence_count"] == 2

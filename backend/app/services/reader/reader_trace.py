"""Bounded, content-free observability for one Paper Reader request.

The Reader handles user questions, PDF chunks, Memory and model/tool payloads.
Those values must never be copied wholesale into normal application logs.  This
small trace records only IDs, counts, policy choices, machine-readable
degradation reasons and durations so an operator can correlate a bad response
with ``X-Request-ID`` without retaining research content.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger(__name__)


def _safe_strings(values: Any, *, limit: int = 16, max_length: int = 120) -> list[str]:
    output: list[str] = []
    for value in list(values or []):
        text = str(value or "").strip()
        if not text or text in output:
            continue
        output.append(text[:max_length])
        if len(output) >= limit:
            break
    return output


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class ReaderRequestTrace:
    """Collect request-local Reader metrics and emit one structured log line."""

    request_id: str
    operation: str
    user_id: int
    paper_id: int
    started_at: float = field(default_factory=time.perf_counter)
    timings_ms: dict[str, float] = field(default_factory=dict)
    fields: dict[str, Any] = field(default_factory=dict)

    def measure(self, stage: str, started_at: float) -> None:
        name = str(stage or "stage").strip()[:48] or "stage"
        self.timings_ms[name] = round(
            max(0.0, (time.perf_counter() - float(started_at)) * 1000), 2
        )

    def record_prepared(self, prepared: Any) -> None:
        """Record packaged-context facts without recording its text."""

        package = getattr(prepared, "package", None)
        if package is None:
            return
        self.fields["context"] = {
            "mode": str(getattr(prepared, "context_mode", "") or ""),
            "active_document": bool(getattr(prepared, "document_version_id", None)),
            "pdf_parsing": bool(getattr(prepared, "pdf_parsing", False)),
            "token_estimate": _safe_int(getattr(package, "token_estimate", 0)),
            "token_budget": _safe_int(getattr(package, "token_budget", 0)),
            "evidence_count": len(getattr(package, "evidence", []) or []),
            "dropped_section_count": len(getattr(package, "dropped_sections", []) or []),
            "dropped_item_count": len(getattr(package, "dropped_items", []) or []),
            "source_counts": {
                str(key)[:48]: _safe_int(value)
                for key, value in dict(getattr(package, "source_counts", {}) or {}).items()
            },
            "degradation_reasons": _safe_strings(
                getattr(prepared, "degradation_reasons", ())
            ),
        }
        self.fields["memory"] = {
            "hit_count": _safe_int(getattr(prepared, "memory_hit_count", 0)),
            "degradation_reasons": _safe_strings(
                getattr(prepared, "memory_degradation_reasons", ())
            ),
        }
        retrieval = getattr(prepared, "retrieval_trace", None)
        if isinstance(retrieval, dict):
            self.fields["retrieval"] = {
                str(key)[:48]: value
                for key, value in retrieval.items()
                if isinstance(value, (str, int, float, bool, type(None), list, tuple))
            }

    def record_agent_result(
        self,
        *,
        reader_snap: dict[str, Any],
        citation_count: int,
        related_count: int,
    ) -> None:
        registry = reader_snap.get("_evidence_registry")
        self.fields["agent"] = {
            "citation_count": max(0, int(citation_count)),
            "related_paper_count": max(0, int(related_count)),
            "registered_evidence_count": len(registry) if registry is not None else 0,
        }

    def emit(self, *, status: str, error_type: str | None = None) -> None:
        payload: dict[str, Any] = {
            "event": "paper_reader_request",
            "request_id": str(self.request_id or "")[:128],
            "operation": str(self.operation or "unknown")[:32],
            "status": str(status or "unknown")[:32],
            "user_id": int(self.user_id),
            "paper_id": int(self.paper_id),
            "elapsed_ms": round(max(0.0, (time.perf_counter() - self.started_at) * 1000), 2),
            "timings_ms": dict(self.timings_ms),
            **self.fields,
        }
        if error_type:
            payload["error_type"] = str(error_type)[:80]
        # Keep an individually valid JSON object in the standard log stream so
        # a future collector can parse it without changing the global logging
        # formatter or leaking prompt/PDF text through ``extra`` fields.
        logger.info("paper_reader_trace=%s", json.dumps(payload, ensure_ascii=False, sort_keys=True))


__all__ = ["ReaderRequestTrace"]

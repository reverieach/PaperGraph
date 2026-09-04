"""Mutable context for one ``paper_reader_reply`` call.

Reader routes construct a request-scoped ``PaperAnalysisAgent``. ``ReaderCtx``
adds a second isolation boundary for helper methods and tool lookups within
that request, so no mutable snapshot, user message, or lookup buffer can leak
between calls.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, List, Tuple


@dataclass
class ReaderCtx:
    snap: dict[str, Any]
    user_message: str = ""
    lookup_buffer: List[Tuple[Any, str]] = field(default_factory=list)
    lookup_lock: threading.Lock = field(default_factory=threading.Lock)
    # Canonical tool material is re-entered through DynamicContextBuilder.
    # These counters/traces belong to exactly one Reader request and therefore
    # cannot consume another user's context budget or leak tool state.
    tool_context_tokens_used: int = 0
    tool_context_lock: threading.Lock = field(default_factory=threading.Lock)
    tool_trace: List[dict[str, Any]] = field(default_factory=list)
    tool_trace_lock: threading.Lock = field(default_factory=threading.Lock)

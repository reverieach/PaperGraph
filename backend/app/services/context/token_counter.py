"""Shared token accounting for chunking and reader context assembly.

The serving model may not expose its native tokenizer.  ``tiktoken`` is still
substantially more reliable than a character heuristic for mixed Chinese and
English input, so it is the default local counter.  The selected mode is
returned with every ContextPackage instead of pretending that it is an exact
provider-side usage number.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast


class TokenCounter:
    """Count and clip text with a deterministic local tokenizer.

    If ``tiktoken`` is unavailable, the fallback deliberately counts Unicode
    characters.  That overestimates many Latin inputs but keeps the hard budget
    safe; callers can expose ``mode`` in diagnostics and Golden reports.
    """

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self.encoding_name = str(encoding_name or "cl100k_base")
        self._encoding: Any | None = None
        try:
            import tiktoken

            self._encoding = tiktoken.get_encoding(self.encoding_name)
        except Exception:
            self._encoding = None

    @property
    def mode(self) -> str:
        if self._encoding is not None:
            return f"tiktoken:{self.encoding_name}:approximate_for_provider"
        return "unicode-character-fallback:conservative"

    @property
    def is_fallback(self) -> bool:
        return self._encoding is None

    def encode(self, text: str) -> list[int] | list[str]:
        value = str(text or "")
        if self._encoding is not None:
            return list(self._encoding.encode(value, disallowed_special=()))
        return list(value)

    def decode(self, tokens: Iterable[int] | Iterable[str]) -> str:
        values = list(tokens)
        if self._encoding is not None:
            return str(
                self._encoding.decode(
                    [int(cast(Any, value)) for value in values]
                )
            )
        return "".join(str(value) for value in values)

    def count(self, text: str) -> int:
        return len(self.encode(str(text or "")))

    def clip(self, text: str, max_tokens: int, *, suffix: str = "…") -> str:
        """Return a prefix that fits the requested local-token budget."""

        value = str(text or "").strip()
        budget = max(0, int(max_tokens))
        if not value or budget <= 0:
            return ""
        tokens = self.encode(value)
        if len(tokens) <= budget:
            return value
        suffix_tokens = self.encode(suffix) if suffix else []
        available = max(0, budget - len(suffix_tokens))
        clipped = self.decode(tokens[:available]).rstrip()
        if not clipped:
            return self.decode(tokens[:budget]).rstrip()
        return (clipped + suffix).strip()

    def clip_tail(self, text: str, max_tokens: int, *, prefix: str = "…\n") -> str:
        """Return the newest suffix that fits the requested local-token budget.

        Conversation history is ordered oldest to newest.  Keeping its tail is
        therefore more useful than keeping its prefix when a policy cap is
        reached.  The omission marker makes the loss explicit to the model
        instead of silently presenting a partial conversation as complete.
        """

        value = str(text or "").strip()
        budget = max(0, int(max_tokens))
        if not value or budget <= 0:
            return ""
        tokens = self.encode(value)
        if len(tokens) <= budget:
            return value
        prefix_tokens = self.encode(prefix) if prefix else []
        available = max(0, budget - len(prefix_tokens))
        clipped = self.decode(tokens[-available:]).lstrip() if available else ""
        if not clipped:
            return self.decode(tokens[-budget:]).lstrip()
        return (prefix + clipped).strip()


__all__ = ["TokenCounter"]

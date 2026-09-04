"""Context assembly primitives for paper reading and multi-paper research."""

from .builder import (
    ContextBuildResult,
    ContextEvidence,
    ContextItem,
    ContextPackage,
    DynamicContextBuilder,
)
from .policies import ContextPolicy, ContextSource, policy_for_query_plan
from .token_counter import TokenCounter

__all__ = [
    "ContextBuildResult",
    "ContextEvidence",
    "ContextItem",
    "ContextPackage",
    "ContextPolicy",
    "ContextSource",
    "DynamicContextBuilder",
    "TokenCounter",
    "policy_for_query_plan",
]

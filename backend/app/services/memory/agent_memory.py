"""Disabled legacy AgentMemory facade.

Agent-global memory had no authenticated user/paper boundary and could leak
state across requests. It remains importable during the transition, but it
cannot persist or inject data.
"""
from __future__ import annotations

from typing import Any


class AgentMemory:
    def add(self, **_: Any) -> str:
        raise RuntimeError(
            "Agent 自动记忆已禁用；请使用用户确认的 MemoryDraft 流程"
        )

    def recent(self, **_: Any) -> list[str]:
        return []

    def build_context_block(self, **_: Any) -> str:
        return ""

    def get_preferences(self) -> str:
        return ""

    def keywords_from_shared(self, **_: Any) -> set[str]:
        return set()

    def stats(self) -> dict[str, Any]:
        return {
            "disabled": True,
            "reason": "canonical user-scoped MemoryDraft workflow is active",
        }


_agent_memory_singleton = AgentMemory()


def get_agent_memory() -> AgentMemory:
    return _agent_memory_singleton


def _shared_user_id() -> str:
    raise RuntimeError("legacy shared user scope was removed")


def _agent_user_id(agent_name: str) -> str:
    raise RuntimeError("legacy agent user scope was removed")

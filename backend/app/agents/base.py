from __future__ import annotations

import logging
from typing import Any

from ..services.llm.llm_service import get_llm
from ..settings import get_settings

logger = logging.getLogger(__name__)

class BaseAgent:
    def __init__(self) -> None:
        self._settings = get_settings()
        self.llm = self._init_llm()

    def _init_llm(self) -> Any:
        try:
            return get_llm()
        except Exception as e:
            logger.exception("[%s] LLM 初始化失败", type(self).__name__)
            raise RuntimeError(f"{type(self).__name__}_llm_init_failed") from e

    def _cfg(self, name: str, default: Any = None) -> Any:
        return getattr(self._settings, name, default)

    def _cfg_int(self, name: str, default: int = 0) -> int:
        try:
            return int(self._cfg(name, default))
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def _clip(value: Any, limit: int) -> str:
        return str(value or "").strip()[:limit]

    def _get_shared_memory(self) -> Any:
        """Legacy hook disabled: Memory now requires an authenticated user scope."""
        return None

    def _read_shared_context(self, *, query: str | None = None, agent_name: str | None = None,
                           tags: list[str] | None = None) -> str:
        """Legacy shared memory is intentionally not injected."""
        return ""

    def _read_shared_recent(self, *, memory_types: list[str] | None = None, limit: int = 8,
                            tags: list[str] | None = None) -> list[str]:
        """Legacy shared memory is intentionally not injected."""
        return []

    def _write_shared(self, *, content: str, memory_type: str = "working", importance: float = 0.5,
                     agent_name: str | None = None, tags: list[str] | None = None) -> None:
        """Automatic Agent memory writes are disabled in phase 1."""
        return None

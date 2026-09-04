"""Bounded, request-scoped Reader tools backed only by canonical chunks.

These tools are deliberately separate from the legacy PDF-extract/cache tools.
They never open a PDF file or read ``paper_pdf_excerpt_cache``: every response
is scoped by ``user_id + paper_id + active document_version_id`` through
``DocumentRepository``.  Their output is useful supplemental material for the
agent, but remains a non-citable tool result until a later ContextPackage
re-entry workflow explicitly registers it as Evidence.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from ...repositories.document_repository import DocumentRepository
from ...services.retrieval.hybrid import HybridChunkRetriever


logger = logging.getLogger(__name__)
_MAX_TOOL_OUTPUT_CHARS = 6_000
_SECTION_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+-]*|[\u3400-\u9fff]{2,}")


def _clip(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, int(limit) - 1)].rstrip() + "…"


def _section_path(row: dict[str, Any]) -> list[str]:
    raw = row.get("section_path_json") or row.get("section_path") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = []
    return [str(item).strip() for item in raw if str(item).strip()] if isinstance(raw, list) else []


def _bounded_int(
    parameters: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(parameters.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


class CanonicalToolFailure(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class CanonicalToolResult:
    status: str
    text: str
    elapsed_ms: int = 0
    error_info: dict[str, str] | None = None


class _CanonicalReaderTool:
    """Shared scope and output rules for one canonical Reader tool."""

    name = ""
    description = ""
    # Agent-loop limits are enforced separately from the repository-side
    # scope checks below.  Canonical tools are local SQLite/FTS reads, so they
    # should return promptly; a timeout only stops the caller from waiting and
    # never claims to kill an already-running thread.
    timeout_sec = 4.0
    max_output_tokens = 520

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        user_id: int,
        paper_id: int,
        document_version_id: str,
    ) -> None:
        self.repository = repository
        self.user_id = int(user_id)
        self.paper_id = int(paper_id)
        self.document_version_id = str(document_version_id)

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema(),
            },
        }

    def parameters_schema(self) -> dict[str, Any]:
        raise NotImplementedError

    def run_with_timing(self, parameters: dict[str, Any]) -> CanonicalToolResult:
        started = time.monotonic()
        try:
            return CanonicalToolResult(
                status="SUCCESS",
                text=self.run(parameters),
                elapsed_ms=int((time.monotonic() - started) * 1_000),
            )
        except CanonicalToolFailure as exc:
            return CanonicalToolResult(
                status="ERROR",
                text=self._render_payload(
                    {
                        "status": "error",
                        "code": exc.code,
                        "message": exc.message,
                    }
                ),
                elapsed_ms=int((time.monotonic() - started) * 1_000),
                error_info={"code": exc.code},
            )
        except Exception as exc:
            # Do not expose a database path, query, or traceback to the LLM.
            logger.warning(
                "canonical_reader_tool_failed",
                extra={
                    "tool": self.name,
                    "paper_id": self.paper_id,
                    "document_version_id": self.document_version_id,
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )
            return CanonicalToolResult(
                status="ERROR",
                text=self._render_payload(
                    {
                        "status": "error",
                        "code": "CANONICAL_TOOL_UNAVAILABLE",
                        "message": "当前 canonical 文档工具暂不可用，请基于已提供的证据回答。",
                    }
                ),
                elapsed_ms=int((time.monotonic() - started) * 1_000),
                error_info={"code": "CANONICAL_TOOL_UNAVAILABLE"},
            )

    def _active_version(self) -> dict[str, Any]:
        active = self.repository.get_active_version(
            user_id=self.user_id,
            paper_id=self.paper_id,
        )
        if active is None or str(active.get("id") or "") != self.document_version_id:
            raise CanonicalToolFailure(
                "CANONICAL_VERSION_UNAVAILABLE",
                "当前论文的 canonical 版本已不可用或已更新；请重新发起阅读请求。",
            )
        return dict(active)

    def _chunks(self, *, level: str, limit: int) -> list[dict[str, Any]]:
        self._active_version()
        chunks = self.repository.list_chunks(
            user_id=self.user_id,
            paper_id=self.paper_id,
            document_version_id=self.document_version_id,
            level=level,
            limit=limit,
        )
        return [dict(chunk) for chunk in chunks]

    def _render_payload(self, payload: dict[str, Any]) -> str:
        payload = {
            "source_boundary": "canonical_pdf_tool_result_untrusted_non_citable",
            "paper_id": self.paper_id,
            "document_version_id": self.document_version_id,
            **payload,
        }
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(text) <= _MAX_TOOL_OUTPUT_CHARS:
            return text
        # Never character-truncate JSON: invalid JSON makes the next LLM turn
        # less predictable and defeats the structured error contract.  The
        # context adapter can still tell the model to narrow the request.
        return json.dumps(
            {
                "source_boundary": "canonical_pdf_tool_result_untrusted_non_citable",
                "paper_id": self.paper_id,
                "document_version_id": self.document_version_id,
                "status": "partial",
                "tool": str(payload.get("tool") or self.name),
                "truncated": True,
                "message": "工具结果超过安全上限；请缩小章节、表格或检索范围后重试。",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def context_chunks_from_result(
        self,
        result_text: str,
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """Hydrate only canonical chunks actually named by a tool response.

        Tool JSON is deliberately *not* trusted as evidence by itself.  This
        helper re-reads its chunk UIDs through the repository with the original
        request scope, so a model cannot inject a foreign UID into a tool
        result and have it become citable evidence.
        """

        try:
            payload = json.loads(str(result_text or ""))
        except (TypeError, ValueError):
            return []
        if not isinstance(payload, dict) or str(payload.get("status") or "").lower() != "ok":
            return []

        chunk_uids: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                uid = str(value.get("chunk_uid") or "").strip()
                if uid and uid not in chunk_uids:
                    chunk_uids.append(uid)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(payload)
        if not chunk_uids:
            return []
        try:
            self._active_version()
            rows = self.repository.get_chunks_by_uid(
                user_id=self.user_id,
                chunk_uids=chunk_uids[: max(1, min(int(limit), 16))],
                active_only=True,
            )
        except Exception as exc:
            logger.warning(
                "canonical_reader_tool_context_hydration_failed",
                extra={
                    "tool": self.name,
                    "paper_id": self.paper_id,
                    "document_version_id": self.document_version_id,
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )
            return []
        return [
            row
            for row in rows
            if int(row.get("paper_id") or 0) == self.paper_id
            and str(row.get("document_version_id") or "") == self.document_version_id
        ]

    def run(self, parameters: dict[str, Any]) -> str:
        raise NotImplementedError


class CanonicalOutlineTool(_CanonicalReaderTool):
    name = "reader_get_outline"
    description = (
        "读取当前论文 active canonical document 的章节目录和页码。"
        "仅用于了解结构；返回内容是不可直接引用的工具补充，论文结论仍需使用当前上下文中的 [E#]。"
    )

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "max_sections": {
                    "type": "integer",
                    "description": "最多返回章节数，默认 16，最大 32。",
                }
            },
        }

    def run(self, parameters: dict[str, Any]) -> str:
        max_sections = _bounded_int(
            parameters, "max_sections", default=16, minimum=1, maximum=32
        )
        parents = self._chunks(level="parent", limit=600)
        all_sections: list[dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        for row in parents:
            path = _section_path(row)
            if not path:
                continue
            key = tuple(item.casefold() for item in path)
            if key in seen:
                continue
            seen.add(key)
            all_sections.append(
                {
                    "section_path": path,
                    "page_start": int(row.get("page_start") or 0) or None,
                    "page_end": int(row.get("page_end") or 0) or None,
                    "content_type": str(row.get("content_type") or "paragraph"),
                }
            )
        sections = all_sections[:max_sections]
        return self._render_payload(
            {
                "status": "ok",
                "tool": self.name,
                "sections": sections,
                "truncated": len(all_sections) > len(sections),
            }
        )


class CanonicalSectionTool(_CanonicalReaderTool):
    name = "reader_get_section"
    description = (
        "从当前论文 active canonical document 读取指定章节的 parent chunks。"
        "适用于方法、实验、局限等深入追问；不会读取旧 PDF 全文缓存。"
    )

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "section_ref": {
                    "type": "string",
                    "description": "章节号、标题或关键词，例如 '3 Method'、'experiment'、'实验'。",
                },
                "max_chunks": {
                    "type": "integer",
                    "description": "最多返回 parent chunk 数，默认 3，最大 6。",
                },
            },
            "required": ["section_ref"],
        }

    @staticmethod
    def _matches(row: dict[str, Any], section_ref: str) -> bool:
        path = _section_path(row)
        corpus = "\n".join([" > ".join(path), _clip(row.get("display_text"), limit=700)]).casefold()
        needle = section_ref.casefold()
        if needle in corpus:
            return True
        terms = [term.casefold() for term in _SECTION_TOKEN_RE.findall(section_ref)]
        return bool(terms) and all(term in corpus for term in terms)

    def run(self, parameters: dict[str, Any]) -> str:
        section_ref = str(parameters.get("section_ref") or "").strip()
        if not section_ref or len(section_ref) > 160:
            raise CanonicalToolFailure(
                "INVALID_SECTION_REF",
                "请提供不超过 160 个字符的章节号、标题或关键词。",
            )
        max_chunks = _bounded_int(
            parameters, "max_chunks", default=3, minimum=1, maximum=6
        )
        matches = [
            row
            for row in self._chunks(level="parent", limit=600)
            if self._matches(row, section_ref)
        ][:max_chunks]
        if not matches:
            return self._render_payload(
                {
                    "status": "not_found",
                    "tool": self.name,
                    "section_ref": section_ref,
                    "message": "未在当前 canonical 章节中找到匹配内容；可先调用 reader_get_outline。",
                    "chunks": [],
                }
            )
        return self._render_payload(
            {
                "status": "ok",
                "tool": self.name,
                "section_ref": section_ref,
                "chunks": [
                    {
                        "chunk_uid": str(row.get("chunk_uid") or ""),
                        "section_path": _section_path(row),
                        "page_start": int(row.get("page_start") or 0) or None,
                        "page_end": int(row.get("page_end") or 0) or None,
                        "content_type": str(row.get("content_type") or "paragraph"),
                        "content": _clip(row.get("display_text"), limit=1_750),
                    }
                    for row in matches
                ],
            }
        )


class CanonicalTableTool(_CanonicalReaderTool):
    name = "reader_get_table"
    description = (
        "从当前论文 active canonical document 读取已解析的表格 chunk。"
        "用于 Table/Tab./表格编号或表格关键词；不重新扫描本地 PDF。"
    )

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table_ref": {
                    "type": "string",
                    "description": "表格编号或关键词，例如 '3'、'Table 2'、'ImageNet'、'ablation'。",
                }
            },
            "required": ["table_ref"],
        }

    @staticmethod
    def _matches(row: dict[str, Any], table_ref: str) -> bool:
        corpus = "\n".join([" > ".join(_section_path(row)), str(row.get("display_text") or "")])
        reference = table_ref.casefold()
        if reference.isdigit():
            return bool(
                re.search(
                    rf"(?:table|tab\.?|表)\s*{re.escape(reference)}(?!\d)",
                    corpus,
                    flags=re.IGNORECASE,
                )
            )
        return reference in corpus.casefold()

    def run(self, parameters: dict[str, Any]) -> str:
        table_ref = str(parameters.get("table_ref") or "").strip()
        if not table_ref or len(table_ref) > 160:
            raise CanonicalToolFailure(
                "INVALID_TABLE_REF",
                "请提供不超过 160 个字符的表格编号或关键词。",
            )
        tables = [
            row
            for row in self._chunks(level="child", limit=1_000)
            if str(row.get("content_type") or "") == "table"
        ]
        match = next((row for row in tables if self._matches(row, table_ref)), None)
        if match is None:
            return self._render_payload(
                {
                    "status": "not_found",
                    "tool": self.name,
                    "table_ref": table_ref,
                    "message": "当前 canonical 版本中没有匹配的已解析表格；图片表格或解析失败的表格不会被伪造。",
                    "available_table_count": len(tables),
                }
            )
        return self._render_payload(
            {
                "status": "ok",
                "tool": self.name,
                "table_ref": table_ref,
                "chunk": {
                    "chunk_uid": str(match.get("chunk_uid") or ""),
                    "section_path": _section_path(match),
                    "page_start": int(match.get("page_start") or 0) or None,
                    "page_end": int(match.get("page_end") or 0) or None,
                    "content": _clip(match.get("display_text"), limit=4_200),
                },
            }
        )


class CanonicalSearchTool(_CanonicalReaderTool):
    name = "reader_search_document"
    description = (
        "在当前论文 active canonical document 的双 FTS 索引中搜索相关 chunk。"
        "用于当前检索证据不足时补充定位；不会访问其他论文或外部网页。"
    )

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要在当前论文中定位的短问题或关键词，最多 800 字符。",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多返回匹配数，默认 4，最大 8。",
                },
            },
            "required": ["query"],
        }

    def run(self, parameters: dict[str, Any]) -> str:
        query = str(parameters.get("query") or "").strip()
        if not query or len(query) > 800:
            raise CanonicalToolFailure(
                "INVALID_SEARCH_QUERY",
                "请提供 1 到 800 个字符的当前论文检索问题。",
            )
        max_results = _bounded_int(
            parameters, "max_results", default=4, minimum=1, maximum=8
        )
        self._active_version()
        # No provider is attached here: a tool invocation must not silently
        # trigger a billable embedding/rerank request.  It reuses the same
        # scoped bilingual sparse planner as the main Reader retrieval path.
        result = HybridChunkRetriever(self.repository).retrieve(
            user_id=self.user_id,
            paper_ids=[self.paper_id],
            query=query,
            limit=max_results,
        )
        hits = [
            hit
            for hit in result.hits
            if hit.document_version_id == self.document_version_id
        ]
        return self._render_payload(
            {
                "status": "ok" if hits else "not_found",
                "tool": self.name,
                "query": query,
                "degradation_flags": list(result.degradation_reasons),
                "matches": [
                    {
                        "chunk_uid": hit.chunk_uid,
                        "section_path": list(hit.section_path),
                        "page_start": hit.page_start or None,
                        "page_end": hit.page_end or None,
                        "content_type": hit.content_type,
                        "sources": list(hit.sources),
                        "content": _clip(hit.display_text, limit=1_300),
                    }
                    for hit in hits
                ],
            }
        )


def build_canonical_reader_tools(
    *,
    db_path: str,
    user_id: int,
    paper_id: int,
    document_version_id: str,
) -> list[_CanonicalReaderTool]:
    """Create request-scoped canonical tools for one owned active document."""

    repository = DocumentRepository(str(db_path))
    return [
        CanonicalOutlineTool(
            repository=repository,
            user_id=int(user_id),
            paper_id=int(paper_id),
            document_version_id=str(document_version_id),
        ),
        CanonicalSectionTool(
            repository=repository,
            user_id=int(user_id),
            paper_id=int(paper_id),
            document_version_id=str(document_version_id),
        ),
        CanonicalTableTool(
            repository=repository,
            user_id=int(user_id),
            paper_id=int(paper_id),
            document_version_id=str(document_version_id),
        ),
        CanonicalSearchTool(
            repository=repository,
            user_id=int(user_id),
            paper_id=int(paper_id),
            document_version_id=str(document_version_id),
        ),
    ]


__all__ = [
    "CanonicalOutlineTool",
    "CanonicalSearchTool",
    "CanonicalSectionTool",
    "CanonicalTableTool",
    "CanonicalToolResult",
    "build_canonical_reader_tools",
]

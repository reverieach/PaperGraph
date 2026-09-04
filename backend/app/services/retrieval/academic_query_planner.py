"""Deterministic, dependency-light query planning for academic retrieval.

The reader must not depend on an LLM rewrite just to make Chinese, English,
and mixed academic questions searchable.  This module keeps the original
question for semantic retrieval, but derives conservative lexical terms for
the two SQLite FTS projections and a task hint for reranking/context policy.

It intentionally is *not* a Chinese word segmenter.  A small set of CJK
n-grams plus the trigram FTS projection has predictable behaviour, keeps the
dependency footprint small, and preserves acronyms/model names exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..text_normalization import normalize_pdf_layout_text


Language = Literal["zh", "en", "mixed", "unknown"]
RetrievalTask = Literal[
    "factual",
    "summary",
    "method",
    "table",
    "formula",
    "limitation",
    "reference",
]


_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff]+")
_ASCII_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_./:+-]*|\d+(?:\.\d+){1,3}")
_CJK_CHAR_RE = re.compile(r"^[\u3400-\u9fff]+$")

# These are conversational shells, not academic terms.  They are removed
# only from the lexical planning string; ``original_query`` remains intact for
# dense retrieval and the LLM.
_LOW_VALUE_CJK_PHRASES = tuple(
    sorted(
        {
            "这篇论文",
            "本文",
            "论文中",
            "论文里",
            "论文内",
            "作者提到",
            "作者认为",
            "作者指出",
            "请问",
            "帮我",
            "请解释",
            "请说明",
            "请介绍",
            "请总结",
            "我想知道",
            "我想了解",
            "能否",
            "可以",
            "为什么",
            "怎么样",
            "是什么",
            "如何",
            "哪些",
            "一下",
        },
        key=len,
        reverse=True,
    )
)
_LOW_VALUE_CJK_TERMS = {
    "论文",
    "作者",
    "内容",
    "部分",
    "相关",
    "问题",
    "什么",
    "这个",
    "那个",
    "这里",
    "里面",
    "其中",
    "一下",
    "可以",
    "是否",
    "怎么",
    "如何",
    "哪些",
    "什么样",
}

_TASK_PATTERNS: tuple[tuple[RetrievalTask, tuple[str, ...]], ...] = (
    (
        "table",
        (
            "table",
            "tables",
            "表格",
            "表中",
            "数值",
            "指标",
            "实验结果",
            "benchmark",
            "accuracy",
            "f1",
        ),
    ),
    (
        "formula",
        ("formula", "equation", "公式", "方程", "推导", "证明", "定理"),
    ),
    (
        "limitation",
        (
            "limitation",
            "limitations",
            "weakness",
            "challenge",
            "failure",
            "局限",
            "限制",
            "不足",
            "缺点",
            "失败",
            "问题所在",
        ),
    ),
    (
        "reference",
        ("reference", "citation", "related work", "引用", "参考文献", "相关工作"),
    ),
    (
        "summary",
        ("summary", "summarize", "overview", "总结", "概述", "导读", "主要内容"),
    ),
    (
        "method",
        (
            "method",
            "approach",
            "architecture",
            "algorithm",
            "模型",
            "方法",
            "架构",
            "算法",
            "机制",
        ),
    ),
)

# Small, explicit terminology bridges are safer than attempting automatic
# translation.  They only activate on an exact known phrase and are capped
# later, so a Chinese question still stays primarily in Chinese.
_TERM_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("检索增强生成", ("retrieval augmented generation", "RAG")),
    ("rag", ("检索增强生成", "retrieval augmented generation")),
    ("retrieval augmented generation", ("检索增强生成", "RAG")),
    ("重排序", ("rerank", "re-ranking")),
    ("rerank", ("重排序", "re-ranking")),
    ("向量检索", ("dense retrieval", "vector retrieval", "embedding")),
    ("dense retrieval", ("向量检索", "vector retrieval")),
    ("上下文工程", ("context engineering",)),
    ("context engineering", ("上下文工程",)),
    ("长上下文", ("long context", "lost in the middle")),
    ("lost in the middle", ("长上下文",)),
    ("上下文窗口", ("context window",)),
    ("context window", ("上下文窗口",)),
    ("大语言模型", ("large language model", "LLM")),
    ("large language model", ("大语言模型", "LLM")),
    ("记忆", ("memory",)),
    ("memory", ("记忆",)),
)

_TASK_INSTRUCTIONS: dict[RetrievalTask, str] = {
    "factual": "选择能直接、可核验地回答该问题的论文证据，优先具体陈述而非泛泛摘要。",
    "summary": "选择覆盖研究问题、方法、实验和结论的代表性论文片段，避免重复摘要。",
    "method": "选择解释方法、模型结构、训练或推理步骤的论文证据，优先方法章节。",
    "table": "选择包含实验设置、表格标题、指标或数值结果的论文证据，避免仅引用结论性描述。",
    "formula": "选择包含定义、公式、变量说明或推导上下文的论文证据，优先公式附近的解释。",
    "limitation": "选择明确描述假设、失败模式、局限或未来工作的论文证据。",
    "reference": "选择能够识别相关工作、引用关系或被比较方法的论文证据。",
}


def normalize_academic_text(value: str) -> str:
    """Normalize harmless presentation noise without deleting academic terms."""

    text = normalize_pdf_layout_text(value)
    # PDFs commonly line-break a hyphenated English word.  Preserve a normal
    # hyphen written on one line (for example ``qwen3-rerank``).
    text = re.sub(r"([A-Za-z0-9])-\s*\n\s*([A-Za-z0-9])", r"\1\2", text)
    return re.sub(r"\s+", " ", text).strip()


def _ordered_unique(values: list[str], *, limit: int) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= limit:
            break
    return tuple(result)


def _lexical_text(normalized: str) -> str:
    planned = normalized
    for phrase in _LOW_VALUE_CJK_PHRASES:
        planned = planned.replace(phrase, " ")
    # Keep ASCII punctuation in model identifiers, but replace ordinary
    # question punctuation so CJK runs can be considered independently.
    return re.sub(r"[，。；：！？?、（）()【】\[\]{}]", " ", planned)


def _cjk_ngrams(value: str) -> list[str]:
    """Return useful CJK terms without requiring an external segmenter."""

    terms: list[str] = []
    for raw_span in _CJK_RUN_RE.findall(value):
        span = raw_span.strip()
        if len(span) < 2 or span in _LOW_VALUE_CJK_TERMS:
            continue
        # A short phrase is a useful exact lexical unit.  Longer natural
        # sentences are represented by overlapping 4/3/2-char candidates;
        # unicode61 handles 2-char phrases while trigram handles >=3 chars.
        if len(span) <= 8:
            terms.append(span)
        for width in (4, 3, 2):
            if len(span) < width:
                continue
            for start in range(0, len(span) - width + 1):
                candidate = span[start : start + width]
                if candidate not in _LOW_VALUE_CJK_TERMS:
                    terms.append(candidate)
    return terms


def _alias_terms(normalized: str, *, include_matched_term: bool = False) -> list[str]:
    """Return bounded terminology bridges, optionally retaining exact source terms.

    Exact matched terms must be considered before derived CJK n-grams.  Without
    that ordering, a long natural-language question can fill the lexical cap
    and silently drop a high-value bridge such as ``上下文窗口 -> context
    window``.
    """

    lower = normalized.casefold()
    aliases: list[str] = []
    for term, values in _TERM_ALIASES:
        if term.isascii() and re.fullmatch(r"[a-z0-9 +.-]+", term):
            matched = bool(
                re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lower)
            )
        else:
            matched = term.casefold() in lower
        if matched:
            if include_matched_term:
                aliases.append(term)
            aliases.extend(values)
    return aliases


def _detect_task(normalized: str) -> RetrievalTask:
    lower = normalized.casefold()
    for task, patterns in _TASK_PATTERNS:
        if any(pattern.casefold() in lower for pattern in patterns):
            return task
    return "factual"


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Structured, inspectable retrieval plan for one user question."""

    original_query: str
    normalized_query: str
    lexical_query: str
    language: Language
    task: RetrievalTask
    unicode_terms: tuple[str, ...]
    trigram_terms: tuple[str, ...]
    cross_language_terms: tuple[str, ...]
    section_preferences: tuple[str, ...]
    content_type_preferences: tuple[str, ...]
    dense_query: str
    rerank_instruction: str

    @property
    def has_cjk(self) -> bool:
        return self.language in {"zh", "mixed"}


class AcademicQueryPlanner:
    """Build a deterministic query plan without a heavyweight segmenter."""

    def plan(self, query: str) -> QueryPlan:
        original = str(query or "").strip()
        normalized = normalize_academic_text(original)
        lexical = _lexical_text(normalized)
        has_cjk = bool(_CJK_RUN_RE.search(normalized))
        has_ascii = bool(re.search(r"[A-Za-z]", normalized))
        language: Language
        if has_cjk and has_ascii:
            language = "mixed"
        elif has_cjk:
            language = "zh"
        elif has_ascii:
            language = "en"
        else:
            language = "unknown"

        ascii_terms = _ASCII_TERM_RE.findall(lexical)
        cjk_terms = _cjk_ngrams(lexical)
        aliases = _alias_terms(normalized)
        prioritized_terms = _alias_terms(normalized, include_matched_term=True)
        unicode_terms = _ordered_unique(
            # Preserve user-entered identifiers, exact terminology bridges,
            # and their cross-language equivalents before broad n-gram recall.
            [*ascii_terms, *prioritized_terms, *cjk_terms], limit=28
        )
        trigram_terms = _ordered_unique(
            [
                *[term for term in cjk_terms if len(term) >= 3],
                *[term for term in ascii_terms if len(term) >= 3],
                *[term for term in aliases if len(term) >= 3],
            ],
            limit=24,
        )
        cross_language_terms = _ordered_unique(aliases, limit=8)
        dense_query = normalized
        if cross_language_terms:
            dense_query = (
                f"{normalized}\n关键学术术语: "
                + "; ".join(cross_language_terms)
            ).strip()
        task = _detect_task(normalized)
        section_preferences: tuple[str, ...] = ()
        lower = normalized.casefold()
        # A phrase such as "in the abstract" is an explicit source-location
        # constraint, not merely a lexical token.  Carry it to hybrid ranking
        # so a later experiment section cannot displace the requested source.
        if "摘要" in normalized or re.search(r"\babstract\b", lower):
            section_preferences = ("摘要", "abstract")
        content_type_preferences: tuple[str, ...] = ()
        if task == "table" or re.search(
            r"\b(?:table|tables|column|columns)\b|表格|表\s*\d+|列(?:名|标题|字段)",
            normalized,
            flags=re.IGNORECASE,
        ):
            content_type_preferences = ("table",)
        return QueryPlan(
            original_query=original,
            normalized_query=normalized,
            lexical_query=lexical,
            language=language,
            task=task,
            unicode_terms=unicode_terms,
            trigram_terms=trigram_terms,
            cross_language_terms=cross_language_terms,
            section_preferences=section_preferences,
            content_type_preferences=content_type_preferences,
            dense_query=dense_query,
            rerank_instruction=_TASK_INSTRUCTIONS[task],
        )


def quote_fts_term(value: str) -> str:
    return f'"{str(value or "").replace(chr(34), chr(34) * 2)}"'


def build_unicode61_query(terms: tuple[str, ...] | list[str]) -> str:
    """Render conservative OR terms for the character-spaced unicode61 FTS."""

    rendered: list[str] = []
    for raw in terms:
        term = str(raw or "").strip()
        if not term:
            continue
        if _CJK_CHAR_RE.fullmatch(term):
            term = " ".join(term)
        rendered.append(quote_fts_term(term))
    return " OR ".join(_ordered_unique(rendered, limit=32))


def build_trigram_query(terms: tuple[str, ...] | list[str]) -> str:
    """Render safe terms that the FTS5 trigram tokenizer can index."""

    rendered = [
        quote_fts_term(str(term).strip())
        for term in terms
        if len(str(term or "").strip()) >= 3
    ]
    return " OR ".join(_ordered_unique(rendered, limit=28))


__all__ = [
    "AcademicQueryPlanner",
    "Language",
    "QueryPlan",
    "RetrievalTask",
    "build_trigram_query",
    "build_unicode61_query",
    "normalize_academic_text",
]

"""自然语言查询规划：向量用整句，BM25 用主题词。

用户输入的是问句而不是关键词。向量检索需要完整原句；
BM25 需要主题词。去掉问句套话后的变体、以及抽词结果，
会并行做 embedding，再用 RRF 融合。

资料正文常用英文术语（sandbox），用户提问常用中文（沙箱）。
同义词会补进 BM25 主题词、向量变体和重排查询，避免只换了译名就召不回。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_EN_WORD = re.compile(r"[a-zA-Z0-9]+")
_ZH_RUN = re.compile(r"[\u4e00-\u9fff]+")

_PUNCT_EDGE = re.compile(r"^[\s，,。.!！？?、；;：:]+|[\s，,。.!！？?、；;：:]+$")

# 长的在前。不要单独剥「请」，避免「请求」被拆开。
_WRAPPER_PREFIXES = (
    "请你给我讲一下",
    "请你给我讲讲",
    "请给我讲一下",
    "请帮我讲一下",
    "请你介绍一下",
    "请帮我介绍一下",
    "能不能给我讲一下",
    "能不能讲一下",
    "可以给我讲一下",
    "请你帮我",
    "请帮我",
    "请给我",
    "请你",
    "请问",
    "帮我",
    "麻烦你",
    "我想知道",
    "我想了解",
    "告诉我",
    "给我讲一下",
    "给我讲讲",
    "讲一下",
    "介绍一下",
    "说明一下",
    "解释一下",
    "如何",
    "怎么",
    "怎样",
    "什么是",
    "使用",
)
_WRAPPER_SUFFIXES = (
    "的具体流程",
    "具体流程",
    "的流程",
    "是什么意思",
    "什么意思",
    "是什么",
    "怎么办",
    "怎么样",
    "怎么做",
    "有哪些",
    "为什么",
    "如何",
    "怎样",
    "吗",
    "呢",
)

_EN_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "how",
        "what",
        "why",
        "which",
        "who",
        "whom",
        "please",
        "tell",
        "me",
        "about",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "as",
        "that",
        "this",
        "these",
        "those",
        "can",
        "could",
        "would",
        "should",
        "i",
        "you",
        "we",
        "they",
        "it",
        "and",
        "or",
        "if",
    }
)
_ZH_STOP_CHARS = frozenset("的了吗呢啊吧呀着过就都也还很最更在有没不个与及等")
_ZH_STOP_WORDS = (
    "如何",
    "怎么",
    "怎样",
    "什么",
    "哪些",
    "哪个",
    "为什么",
    "是否",
    "能否",
    "可以",
    "请问",
    "介绍",
    "说明",
    "讲讲",
    "说说",
    "告诉",
)

# 组内互为同义。中文命中只补英文主词，英文命中只补中文主词。
# 不把 agent/prompt 这类高频词的形态变化全塞进去，避免稀释 BM25。
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("sandbox", "沙箱"),
    ("prompt injection", "提示词注入", "提示注入"),
    ("observability", "可观测性"),
    ("embedding", "向量嵌入"),
    ("retrieval", "检索"),
    ("rerank", "重排序"),
    ("chunk", "分块"),
    ("prompt", "提示词"),
    ("agent", "智能体"),
    ("push", "推送", "上传"),
    ("clone", "克隆"),
    ("commit", "提交"),
    ("pull", "拉取"),
    ("repository", "仓库"),
)


@dataclass(frozen=True)
class QueryPlan:
    """由用户原句得到的检索计划。"""

    original: str
    lexical: str
    variants: tuple[str, ...]
    rerank_query: str = ""
    synonym_terms: tuple[str, ...] = ()

    @property
    def embed_texts(self) -> list[str]:
        return list(self.variants)


def plan_query(query: str, *, expand: bool = True, max_variants: int = 3) -> QueryPlan:
    """根据原始查询生成检索计划。

    短关键词查询只保留一条变体。自然语言问句会额外生成去套话版本和/或主题词。
    命中中英术语表时，还会补上对译词，供 BM25、向量变体和重排使用。
    """
    original = " ".join((query or "").strip().split())
    if not original:
        return QueryPlan(original="", lexical="", variants=())

    normalized = original.casefold()
    stripped = _strip_question_wrappers(normalized)
    core = stripped or normalized
    lexical = extract_keywords(core) or core
    synonym_terms = synonym_counterparts(core)
    if synonym_terms:
        lexical = _join_unique(lexical, synonym_terms)

    english_core = _to_english_terms(core)
    variants: list[str] = [normalized]
    if expand:
        for candidate in (english_core, core, lexical):
            if candidate and candidate not in variants:
                variants.append(candidate)
            if len(variants) >= max(1, max_variants):
                break

    rerank_query = english_core
    if synonym_terms:
        rerank_query = _join_unique(rerank_query, synonym_terms)

    return QueryPlan(
        original=normalized,
        lexical=lexical,
        variants=tuple(variants),
        rerank_query=rerank_query,
        synonym_terms=synonym_terms,
    )


def extract_keywords(text: str) -> str:
    """去掉问句套话和停用词，保留英文主题词与连续中文片段。"""
    if not text:
        return ""
    stripped = _strip_question_wrappers(text.casefold())
    parts: list[str] = []
    for word in _EN_WORD.findall(stripped):
        if word not in _EN_STOP:
            parts.append(word)
    for run in _ZH_RUN.findall(stripped):
        cleaned = _clean_zh_run(run)
        if cleaned:
            parts.append(cleaned)
    return " ".join(parts)


def lexical_query(text: str) -> str:
    """给 BM25 / 关键词精排用的文本：主题词，抽不出则回退原句。"""
    keywords = extract_keywords(text)
    return keywords or " ".join((text or "").strip().split()).casefold()


def synonym_counterparts(text: str) -> tuple[str, ...]:
    """查出查询里已出现术语的对译主词。

    出现「沙箱」就补 sandbox；出现 sandbox 就补「沙箱」。
    同一组只补一个主词，避免 sandboxes/agents 这类形态变化冲淡检索。
    """
    if not text:
        return ()
    lowered = text.casefold()
    extras: list[str] = []
    seen: set[str] = set()
    for group in _SYNONYM_GROUPS:
        hits = [term for term in group if _contains_term(lowered, term)]
        if not hits:
            continue
        has_zh = any(not term.isascii() for term in hits)
        has_en = any(term.isascii() for term in hits)
        primary_en = next((term for term in group if term.isascii()), None)
        primary_zh = next((term for term in group if not term.isascii()), None)
        to_add: list[str] = []
        if has_zh and primary_en:
            to_add.append(primary_en)
        if has_en and primary_zh:
            to_add.append(primary_zh)
        for term in to_add:
            key = term.casefold()
            if key in seen or _contains_term(lowered, term):
                continue
            seen.add(key)
            extras.append(term)
    return tuple(extras)


def _to_english_terms(text: str) -> str:
    """把查询里的中文术语换成组内的英文主词。"""
    result = text
    for group in _SYNONYM_GROUPS:
        english = next((term for term in group if term.isascii()), None)
        if not english:
            continue
        chinese = sorted(
            (term for term in group if not term.isascii()),
            key=len,
            reverse=True,
        )
        for term in chinese:
            if term in result:
                result = result.replace(term, f" {english} ")
                break
    return " ".join(result.split())


def _contains_term(text: str, term: str) -> bool:
    needle = term.casefold()
    if not needle:
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in needle):
        return needle in text
    pattern = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _join_unique(base: str, extras: tuple[str, ...] | list[str]) -> str:
    parts = [base] if base else []
    seen = set(base.split()) if base else set()
    for extra in extras:
        token = extra.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        parts.append(token)
    return " ".join(parts)


def distinctive_terms(plan: QueryPlan) -> tuple[str, ...]:
    """问句里的英文实词 + 对译词，用来在重排门槛误杀后对齐文件名和正文。"""
    extras: list[str] = []
    seen: set[str] = set()
    for word in _EN_WORD.findall(plan.original or ""):
        token = word.casefold()
        if token in _EN_STOP or len(token) < 2:
            continue
        if token not in seen:
            seen.add(token)
            extras.append(token)
    for term in plan.synonym_terms:
        key = term.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        extras.append(term)
    return tuple(extras)


def _strip_question_wrappers(text: str) -> str:
    current = " ".join((text or "").split())
    changed = True
    while current and changed:
        changed = False
        lowered = current.casefold()
        for prefix in _WRAPPER_PREFIXES:
            if lowered.startswith(prefix):
                current = current[len(prefix) :]
                changed = True
                break
        lowered = current.casefold()
        for suffix in _WRAPPER_SUFFIXES:
            if lowered.endswith(suffix):
                current = current[: len(current) - len(suffix)]
                changed = True
                break
        current = _PUNCT_EDGE.sub("", current)
        current = " ".join(current.split())
    return current


def _clean_zh_run(run: str) -> str:
    cleaned = "".join(ch for ch in run if ch not in _ZH_STOP_CHARS)
    for word in _ZH_STOP_WORDS:
        cleaned = cleaned.replace(word, "")
    return cleaned

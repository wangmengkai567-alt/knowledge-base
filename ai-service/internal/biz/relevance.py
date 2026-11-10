"""结合重排分数和问题中的英文术语，筛选与问题相关的资料。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from internal.biz.query_plan import query_english_terms, text_has_term

logger = structlog.get_logger()

if TYPE_CHECKING:
    from internal.biz.entity import RetrievalResult


def apply_relevance_gate(
    query: str,
    candidates: list[RetrievalResult],
    min_relevance: float,
) -> list[RetrievalResult]:
    """按实际重排分数和原问题关键词筛选候选，返回保留结果。

    阈值小于等于 0 时原样返回。否则，仅接受具有真实重排分数的候选。
    问题无英文或数字关键词时，要求分数达到阈值；有关键词时，达到阈值还须匹配至少一个词，
    低于阈值则须匹配全部词。匹配同时检查文件名和分块正文。
    """
    if min_relevance <= 0:
        return list(candidates)

    terms = query_english_terms(query)
    kept: list[RetrievalResult] = []
    rescued = 0
    dropped_neighbor = 0
    for item in candidates:
        haystack = f"{item.document_filename or ''}\n{item.chunk_content or ''}"
        hits = sum(1 for term in terms if text_has_term(haystack, term))
        if item.score_type != "rerank" or item.score is None:
            raise ValueError("相关性门槛只接受真实重排分")
        above = item.score >= min_relevance
        if not terms:
            if above:
                kept.append(item)
            continue
        if above:
            if hits:
                kept.append(item)
            else:
                dropped_neighbor += 1
            continue
        if hits == len(terms):
            kept.append(item)
            rescued += 1

    logger.info(
        "relevance_gate_applied",
        min_relevance=min_relevance,
        term_count=len(terms),
        before=len(candidates),
        after=len(kept),
        rescued=rescued,
        dropped_neighbor=dropped_neighbor,
    )
    return kept

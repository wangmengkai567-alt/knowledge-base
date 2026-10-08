"""Retrieval relevance gate: rerank score plus query-term overlap.

Zhipu raw logits squash into a gray zone around min_relevance. A hard
score cutoff then drops notes that actually contain the asker's terms,
and can keep a high-scoring neighbor that shares none of them.
"""

from __future__ import annotations

import structlog

from internal.biz.entity import RetrievalResult
from internal.biz.query_plan import query_english_terms, text_has_term

logger = structlog.get_logger()


def apply_relevance_gate(
    query: str,
    candidates: list[RetrievalResult],
    min_relevance: float,
) -> list[RetrievalResult]:
    """Keep notes that clear the score line or carry every query term.

    - No English terms in the query: trust the rerank score only.
    - Score >= min_relevance: keep if at least one query term appears
      (drop neighbors the reranker liked but the question never mentioned).
    - Score < min_relevance: keep only if every query term appears
      (rescue on-topic notes the reranker under-scored).
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

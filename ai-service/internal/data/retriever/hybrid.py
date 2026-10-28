"""
internal/data/retriever/hybrid.py — 混合检索器（Sprint 18）

组合向量检索（语义）+ 关键词检索（BM25 精确匹配），
用 RRF（Reciprocal Rank Fusion）融合两路结果，提升召回率与准确率。

为什么用 RRF 而非加权求和：
- 两路 score 量纲不同（余弦相似度 [0,1] vs BM25 [0, +∞)），直接加权需要归一化，效果不稳定
- RRF 只依赖排名（1/(k+rank)），对分数分布不敏感，工程实践稳健
- 论文：Cormack, Clarke, and Buettcher (2009). 参数 k 通常取 60。

依赖失败直接抛出；没有关键词命中与关键词服务失败明确区分。
"""

from __future__ import annotations

import structlog

from internal.biz.entity import RetrievedChunk, SourceScore
from internal.biz.repo import KeywordStore, Retriever, VectorStore
from internal.biz.rrf import reciprocal_rank_fusion

logger = structlog.get_logger()


class HybridRetriever(Retriever):
    """向量 + 关键词混合检索器（RRF 融合）。"""

    def __init__(
        self,
        vector_store: VectorStore,
        keyword_store: KeywordStore,
        rrf_k: int = 60,
        candidate_multiplier: int = 2,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError(f"rrf_k must be positive, got {rrf_k}")
        if candidate_multiplier <= 0:
            raise ValueError(
                f"candidate_multiplier must be positive, got {candidate_multiplier}"
            )
        self._vector_store = vector_store
        self._keyword_store = keyword_store
        self._rrf_k = rrf_k
        self._candidate_multiplier = candidate_multiplier

    async def retrieve(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filter_metadata: dict | None = None,
        query_text: str | None = None,
        keyword_query: str | None = None,
    ) -> list[RetrievedChunk]:
        """两路检索 + RRF 融合。

        两路各取 ``top_k * candidate_multiplier`` 个候选，融合后截断到
        ``top_k``，避免融合后可用候选不足。候选倍率可通过构造参数调节，
        默认 2 与历史行为保持一致。

        ``keyword_query`` 供自然语言提问：向量用完整问句，BM25 用抽词后的
        主题词；未传时回退 ``query_text``。
        """
        candidate_k = top_k * self._candidate_multiplier
        bm25_query = (keyword_query or query_text or "").strip()

        # 向量分支（核心，失败即抛错）
        vector_results = await self._vector_store.search(
            query_vector=query_vector,
            top_k=candidate_k,
            filter_metadata=filter_metadata,
        )

        keyword_results = await self._keyword_store.search(
            query=bm25_query,
            top_k=candidate_k,
            filter_metadata=filter_metadata,
        ) if bm25_query else []

        fused = reciprocal_rank_fusion(
            [vector_results, keyword_results],
            k=self._rrf_k,
        )

        logger.info(
            "hybrid_retrieve_completed",
            vector_count=len(vector_results),
            keyword_count=len(keyword_results),
            fused_count=len(fused),
            top_k=top_k,
            candidate_k=candidate_k,
            candidate_multiplier=self._candidate_multiplier,
        )
        vector_scores = dict(vector_results)
        keyword_scores = dict(keyword_results)
        method = f"hybrid_rrf(k={self._rrf_k}; rank starts at 1)"
        output: list[RetrievedChunk] = []
        for cid, score in fused[:top_k]:
            scores = []
            if cid in vector_scores:
                scores.append(SourceScore("vector", vector_scores[cid], "cosine_similarity"))
            if cid in keyword_scores:
                scores.append(SourceScore("bm25", keyword_scores[cid], "BM25"))
            scores.append(SourceScore("rrf", score, method))
            output.append(RetrievedChunk(cid, score, "rrf", method, scores))
        return output



"""
internal/data/retriever/hybrid.py — 混合检索器（Sprint 18）

组合向量检索（语义）+ 关键词检索（BM25 精确匹配），
用 RRF（Reciprocal Rank Fusion）融合两路结果，提升召回率与准确率。

为什么用 RRF 而非加权求和：
- 两路 score 量纲不同（余弦相似度 [0,1] vs BM25 [0, +∞)），直接加权需要归一化，效果不稳定
- RRF 只依赖排名（1/(k+rank)），对分数分布不敏感，工程实践稳健
- 论文：Cormack, Clarke, and Buettcher (2009). 参数 k 通常取 60。

fallback 语义：
- 关键词分支失败 → 只返回向量结果（不阻断请求）
- 向量分支失败 → 抛出异常（向量是核心，失败视为服务不可用）
- 两路都为空 → 返回空列表
"""

from __future__ import annotations

import structlog

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
    ) -> list[tuple[int, float]]:
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

        # 关键词分支（可选，失败降级）
        keyword_results: list[tuple[int, float]] = []
        if bm25_query:
            try:
                keyword_results = await self._keyword_store.search(
                    query=bm25_query,
                    top_k=candidate_k,
                    filter_metadata=filter_metadata,
                )
            except Exception as e:
                # 关键词失败不阻断向量分支
                logger.warning(
                    "hybrid_keyword_branch_failed",
                    error=str(e),
                    exc_info=True,
                )
                keyword_results = []
        else:
            # Sprint 18-fix (P1)：从 debug 提升为 warning——
            # HybridRetriever 未拿到 query_text 时实际退化为纯向量检索，
            # 属于隐形降级，必须在日志中可见，便于发现调用方漏传 query_text。
            logger.warning(
                "hybrid_no_query_text_degraded_to_vector_only",
                hint="caller should pass query_text; falling back to vector-only",
            )

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
        return fused[:top_k]



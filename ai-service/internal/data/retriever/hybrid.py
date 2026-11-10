"""分别执行向量检索与 BM25 检索，按名次融合后返回候选分块。

每路请求 top_k × candidate_multiplier 个候选，融合后最多返回 top_k 个。
返回值保留两路原始分数及融合分；任一路调用失败均向上传播异常。
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
        """配置向量与关键词两路检索及融合参数。

        rrf_k 是排名贡献 1/(rrf_k+rank) 中的平滑参数，不表示候选数量。
        candidate_multiplier 是每路候选数相对于请求 top_k 的倍数，融合后仍最多返回 top_k 项。
        """
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
        *,
        document_ids: list[int] | None = None,
    ) -> list[RetrievedChunk]:
        """分别取得两路候选，按 RRF 融合后返回带原始评分记录的 RetrievedChunk 列表。

        keyword_query 优先于 query_text；两者为空时不执行关键词搜索。
        filter_metadata 和 document_ids 同时传给两路检索；任一路异常向上传播。
        """
        candidate_k = top_k * self._candidate_multiplier
        bm25_query = (keyword_query or query_text or "").strip()

        # 两路使用相同的文档和元数据范围；任一路失败均不返回部分成功结果。
        vector_results = await self._vector_store.search(
            query_vector=query_vector,
            top_k=candidate_k,
            filter_metadata=filter_metadata,
            document_ids=document_ids,
        )

        keyword_results = await self._keyword_store.search(
            query=bm25_query,
            top_k=candidate_k,
            filter_metadata=filter_metadata,
            document_ids=document_ids,
        ) if bm25_query else []

        # 同一分块在两路均出现时累加其排名贡献；原始分数随后单独保留。
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



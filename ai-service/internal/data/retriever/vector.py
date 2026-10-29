"""
internal/data/retriever/vector.py — 纯向量检索器实现

基于 VectorStore 的相似度搜索，是 Sprint 8 的默认检索器。
后续可扩展为混合检索（向量 + BM25）。
"""

from __future__ import annotations

import structlog

from internal.biz.entity import RetrievedChunk, SourceScore
from internal.biz.repo import Retriever, VectorStore

logger = structlog.get_logger()


class VectorRetriever(Retriever):
    """纯向量检索器 — 委托 VectorStore 执行相似度搜索。"""

    def __init__(self, vector_store: VectorStore) -> None:
        self._vector_store = vector_store

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
        """委托 VectorStore.search 执行相似度检索。

        `query_text` / `keyword_query` 仅供接口统一，纯向量检索忽略。
        """
        results = await self._vector_store.search(
            query_vector=query_vector,
            top_k=top_k,
            filter_metadata=filter_metadata,
            document_ids=document_ids,
        )

        logger.debug(
            "vector_retriever_search",
            top_k=top_k,
            result_count=len(results),
        )

        return [RetrievedChunk(cid, score, "vector", "cosine_similarity",
                               [SourceScore("vector", score, "cosine_similarity")])
                for cid, score in results]

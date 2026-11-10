"""将查询向量、元数据及文档范围交给 VectorStore，返回带评分类型的候选分块。"""

from __future__ import annotations

import structlog

from internal.biz.entity import RetrievedChunk, SourceScore
from internal.biz.repo import Retriever, VectorStore

logger = structlog.get_logger()


class VectorRetriever(Retriever):
    """纯向量检索器 — 委托 VectorStore 执行相似度搜索。"""

    def __init__(self, vector_store: VectorStore) -> None:
        """注入向量存储，检索时由它执行相似度搜索和元数据过滤。"""
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
        """向向量存储查询指定来源范围内的前 top_k 项；不使用 query_text 或 keyword_query。"""
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

"""
internal/service/rag.py — RAG 检索增强生成应用层

职责：DTO 校验 → 调 biz → DTO 返回。
不含业务逻辑，只做 DTO ↔ DO 转换。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from api.openapi.schemas.rag import RAGQueryResponse, RAGSourceDTO
from internal.biz.rag import RAGBiz, RAGStreamEvent


class RAGService:
    """RAG 应用层 — DTO 校验 + 调 biz + DTO 返回。"""

    def __init__(self, rag_biz: RAGBiz) -> None:
        self._rag_biz = rag_biz

    async def query(
        self,
        query: str,
        knowledge_base_id: int,
        top_k: int = 5,
        temperature: float | None = None,
        max_tokens: int | None = None,
        chunk_ids: list[int] | None = None,
        allowed_kb_ids: list[int] | None = None,
    ) -> RAGQueryResponse:
        """RAG 查询：已有分块则直接生成，否则先检索再生成。"""
        result = await self._rag_biz.query(
            query=query,
            knowledge_base_id=knowledge_base_id,
            top_k=top_k,
            temperature=temperature,
            max_tokens=max_tokens,
            chunk_ids=chunk_ids,
            allowed_kb_ids=allowed_kb_ids,
        )

        # 领域对象 → 接口 DTO
        sources = [
            RAGSourceDTO(
                chunk_id=s.chunk_id,
                content=s.chunk_content[:200],  # 截断至 200 字符
                score=round(s.score, 4),
                document_id=s.document_id,
                document_filename=s.document_filename,
                knowledge_base_id=s.knowledge_base_id,
            )
            for s in result.sources
        ]

        return RAGQueryResponse(
            answer=result.answer,
            model=result.model,
            sources=sources,
            source_count=len(sources),
        )

    async def query_stream(
        self,
        query: str,
        knowledge_base_id: int,
        top_k: int = 5,
        temperature: float | None = None,
        max_tokens: int | None = None,
        chunk_ids: list[int] | None = None,
        allowed_kb_ids: list[int] | None = None,
    ) -> AsyncIterator[RAGStreamEvent]:
        """RAG 流式查询：已有分块则直接生成，否则先检索再生成。"""
        async for event in self._rag_biz.query_stream(
            query=query,
            knowledge_base_id=knowledge_base_id,
            top_k=top_k,
            temperature=temperature,
            max_tokens=max_tokens,
            chunk_ids=chunk_ids,
            allowed_kb_ids=allowed_kb_ids,
        ):
            yield event

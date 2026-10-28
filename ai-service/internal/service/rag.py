"""问答 DTO 转换；同步与 SSE 共用来源序列化。"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from api.openapi.schemas.rag import HitScoreDTO, MaterialSegmentDTO, RAGQueryResponse, RAGSourceDTO
from internal.service.retrieval import retrieval_dto

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from internal.biz.materials import AnswerSource
    from internal.biz.rag import RAGBiz, RAGStreamEvent


def source_dto(source: AnswerSource) -> RAGSourceDTO:
    return RAGSourceDTO(
        citation_id=source.citation_id,
        source_type=source.source_type,
        content=source.content,
        chunk_ids=[segment.chunk_id for segment in source.segments],
        segments=[MaterialSegmentDTO(**asdict(segment)) for segment in source.segments],
        truncated=source.truncated,
        document_id=source.document_id,
        document_filename=source.document_filename,
        knowledge_base_id=source.knowledge_base_id,
        document_updated_at=source.document_updated_at,
        retrieval_hits=[
            HitScoreDTO(
                chunk_id=hit.chunk_id,
                score=hit.score,
                score_type=hit.score_type,
                score_method=hit.score_method,
                scores=[asdict(score) for score in hit.scores],
            )
            for hit in source.retrieval_hits
        ],
    )


class RAGService:
    def __init__(self, rag_biz: RAGBiz) -> None:
        self._rag_biz = rag_biz

    async def query(
        self,
        query: str,
        knowledge_base_ids: list[int],
        top_k: int = 5,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> RAGQueryResponse:
        result = await self._rag_biz.query(
            query, knowledge_base_ids, top_k, temperature, max_tokens
        )
        sources = [source_dto(source) for source in result.sources]
        return RAGQueryResponse(
            answer=result.answer,
            model=result.model,
            sources=sources,
            source_count=len(sources),
            retrieval_results=[retrieval_dto(hit) for hit in result.retrieval_results],
            input_measurement=result.input_measurement,
            estimated_input_tokens=result.estimated_input_tokens,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )

    async def query_stream(
        self,
        query: str,
        knowledge_base_ids: list[int],
        top_k: int = 5,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[RAGStreamEvent]:
        async for event in self._rag_biz.query_stream(
            query, knowledge_base_ids, top_k, temperature, max_tokens
        ):
            yield event

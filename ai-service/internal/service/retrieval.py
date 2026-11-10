"""
internal/service/retrieval.py — 检索应用层

调用检索业务，并将命中资料及评分转换为接口响应。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from api.openapi.schemas.retrieval import RetrievalResponse, RetrievalResultDTO

if TYPE_CHECKING:
    from internal.biz.entity import RetrievalResult
    from internal.biz.retrieval import RetrievalBiz


def _iso(dt) -> str | None:
    """把非空时间转成 ISO 8601 字符串；未记录时间时保留 None。"""
    return dt.isoformat() if dt is not None else None


# 将内部检索结果转换为接口响应，保留正文、来源和各阶段评分。
def retrieval_dto(hit: RetrievalResult) -> RetrievalResultDTO:
    return RetrievalResultDTO(
        chunk_id=hit.chunk_id, score=hit.score, score_type=hit.score_type,
        score_method=hit.score_method, scores=[asdict(score) for score in hit.scores],
        content=hit.chunk_content, position=hit.chunk_position,
        document_id=hit.document_id, document_filename=hit.document_filename,
        knowledge_base_id=hit.knowledge_base_id,
        document_updated_at=_iso(hit.document_updated_at),
    )


# 为独立检索接口提供结果列表，不负责组织问答或调用模型生成答案。
class RetrievalService:
    def __init__(self, retrieval_biz: RetrievalBiz) -> None:
        """注入检索业务，用于独立检索接口查询及结果结构转换。"""
        self._retrieval_biz = retrieval_biz

    async def retrieve(
        self,
        query: str,
        knowledge_base_id: int,
        top_k: int = 10,
    ) -> RetrievalResponse:
        """独立检索列表接口，不参与问答编排。"""
        results = await self._retrieval_biz.retrieve(
            query=query,
            knowledge_base_id=knowledge_base_id,
            top_k=top_k,
            dedupe_by_document=False,
        )
        return RetrievalResponse(
            query=query,
            knowledge_base_id=knowledge_base_id,
            results=[retrieval_dto(hit) for hit in results],
            total=len(results),
        )

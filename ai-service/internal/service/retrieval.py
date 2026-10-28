"""
internal/service/retrieval.py — 检索应用层

职责：DTO 校验 → 调 biz → DTO 返回。
不含业务逻辑，只做 DTO ↔ DO 转换。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from api.openapi.schemas.retrieval import RetrievalResponse, RetrievalResultDTO

if TYPE_CHECKING:
    from internal.biz.entity import RetrievalResult
    from internal.biz.retrieval import RetrievalBiz


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def retrieval_dto(hit: RetrievalResult) -> RetrievalResultDTO:
    return RetrievalResultDTO(
        chunk_id=hit.chunk_id, score=hit.score, score_type=hit.score_type,
        score_method=hit.score_method, scores=[asdict(score) for score in hit.scores],
        content=hit.chunk_content, position=hit.chunk_position,
        document_id=hit.document_id, document_filename=hit.document_filename,
        knowledge_base_id=hit.knowledge_base_id,
        document_updated_at=_iso(hit.document_updated_at),
    )


class RetrievalService:
    """检索应用层 — DTO 校验 + 调 biz + DTO 返回。"""

    def __init__(self, retrieval_biz: RetrievalBiz) -> None:
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

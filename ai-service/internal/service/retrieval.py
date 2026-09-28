"""
internal/service/retrieval.py — 检索应用层

职责：DTO 校验 → 调 biz → DTO 返回。
不含业务逻辑，只做 DTO ↔ DO 转换。
"""

from __future__ import annotations

from api.openapi.schemas.retrieval import RetrievalResponse, RetrievalResultDTO
from internal.biz.retrieval import RetrievalBiz


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


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
        """检索与 query 最相关的 chunks。

        搜索页只检索这一次：返回按相关度排序的分块（不过文档去重），
        列表由前端按文档去重，回答接口用同一批 chunk_ids 生成。
        """
        results = await self._retrieval_biz.retrieve(
            query=query,
            knowledge_base_id=knowledge_base_id,
            top_k=top_k,
            dedupe_by_document=False,
        )
        return RetrievalResponse(
            query=query,
            knowledge_base_id=knowledge_base_id,
            results=[
                RetrievalResultDTO(
                    chunk_id=r.chunk_id,
                    score=r.score,
                    content=r.chunk_content,
                    position=r.chunk_position,
                    document_id=r.document_id,
                    document_filename=r.document_filename,
                    document_updated_at=_iso(r.document_updated_at),
                )
                for r in results
            ],
            total=len(results),
        )

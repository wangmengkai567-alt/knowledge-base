"""
internal/service/chunk.py — 分块应用层

职责：DTO 校验 → 调 biz → DTO 返回。
不含业务逻辑，只做 DTO ↔ DO 转换。
"""

from __future__ import annotations

from api.openapi.schemas.chunk import ChunkListResponse, ChunkResponse
from internal.biz.chunk import ChunkBiz
from internal.biz.document import DocumentBiz


class ChunkService:
    """分块应用层 — DTO 校验 + 调 biz + DTO 返回。"""

    def __init__(self, chunk_biz: ChunkBiz, document_biz: DocumentBiz) -> None:
        self._chunk_biz = chunk_biz
        self._document_biz = document_biz

    async def list_chunks(self, document_id: int, user_id: int) -> ChunkListResponse:
        """列出文档下所有分块（含文档所有权校验）。"""
        # 校验文档存在 + 所有权（与 DocumentService 一致）
        await self._document_biz.get_document(document_id, user_id=user_id)
        chunks = await self._chunk_biz.list_chunks(document_id)
        return ChunkListResponse(
            chunks=[ChunkResponse.from_domain(c) for c in chunks],
            total=len(chunks),
        )

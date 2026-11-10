"""
internal/service/chunk.py — 分块应用层

读取文档当前已完成的分块，并转换为接口列表响应。
"""

from __future__ import annotations

from api.openapi.schemas.chunk import ChunkListResponse, ChunkResponse
from internal.biz.chunk import ChunkBiz
from internal.biz.document import DocumentBiz


# 向接口提供文档当前可展示的分块列表。
class ChunkService:
    def __init__(self, chunk_biz: ChunkBiz, document_biz: DocumentBiz) -> None:
        """注入分块业务和文档业务，用于核对文档并取得当前可展示的分块。"""
        self._chunk_biz = chunk_biz
        self._document_biz = document_biz

    async def list_chunks(self, document_id: int, user_id: int) -> ChunkListResponse:
        # 确认文档存在；访问权限由 HTTP 路由检查。
        document = await self._document_biz.get_document(document_id, user_id=user_id)
        chunks = []
        # 分块阶段未完成时，保留的旧分块不能作为本轮处理结果展示。
        if document.stages.get("chunk", {}).get("status") == "completed":
            chunks = await self._chunk_biz.list_chunks(document_id)
        return ChunkListResponse(
            chunks=[ChunkResponse.from_domain(c) for c in chunks],
            total=len(chunks),
        )

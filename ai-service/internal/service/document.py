"""
internal/service/document.py — 文档应用层

职责：DTO 校验 → 调 biz → DTO 返回。
不含业务逻辑，只做 DTO ↔ DO 转换。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.openapi.schemas.document import DocumentListResponse, DocumentResponse

if TYPE_CHECKING:
    from internal.biz.document import DocumentBiz



class DocumentService:
    """文档应用层 — DTO 校验 + 调 biz + DTO 返回。"""

    def __init__(
        self,
        document_biz: DocumentBiz,
    ) -> None:
        self._document_biz = document_biz

    async def upload(
        self,
        knowledge_base_id: int,
        user_id: int,
        filename: str,
        content: bytes,
    ) -> DocumentResponse:
        """上传文档 — 调 biz → DO → DTO。"""
        document = await self._document_biz.upload(
            knowledge_base_id=knowledge_base_id,
            user_id=user_id,
            filename=filename,
            content=content,
        )
        return DocumentResponse.from_domain(document)

    async def list_documents(
        self, knowledge_base_id: int, user_id: int
    ) -> DocumentListResponse:
        """列出知识库下所有文档。"""
        documents = await self._document_biz.list_documents(
            knowledge_base_id=knowledge_base_id,
            user_id=user_id,
        )
        return DocumentListResponse(
            documents=[DocumentResponse.from_domain(doc) for doc in documents],
            total=len(documents),
        )

    async def get_document(self, document_id: int, user_id: int) -> DocumentResponse:
        """查单个文档详情（含所有权校验）。"""
        document = await self._document_biz.get_document(document_id, user_id=user_id)
        return DocumentResponse.from_domain(document)

    async def delete_document(self, document_id: int, user_id: int) -> None:
        """删除文档（含所有权校验）。"""
        await self._document_biz.delete_document(document_id, user_id=user_id)

    async def parse_document(
        self, document_id: int, user_id: int, force: bool = False
    ) -> DocumentResponse:
        """手动解析 / 重解析。"""
        document = await self._document_biz.parse_document(
            document_id=document_id,
            user_id=user_id,
            force=force,
        )
        return DocumentResponse.from_domain(document)

    async def retry(self, document_id: int, user_id: int, stage: str | None = None) -> DocumentResponse:
        document = await self._document_biz.retry(document_id, user_id, stage)
        return DocumentResponse.from_domain(document)

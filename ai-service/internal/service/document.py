"""
internal/service/document.py — 文档应用层

调用文档业务，并将内部文档对象转换为接口响应。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.openapi.schemas.document import DocumentListResponse, DocumentResponse

if TYPE_CHECKING:
    from internal.biz.document import DocumentBiz



# 将接口中的文档操作交给业务层，并统一转换返回结果。
class DocumentService:
    def __init__(
        self,
        document_biz: DocumentBiz,
    ) -> None:
        """注入文档业务，统一把上传、解析、重试和详情结果转换为接口响应。"""
        self._document_biz = document_biz

    async def upload(
        self,
        knowledge_base_id: int,
        user_id: int,
        filename: str,
        content: bytes,
    ) -> DocumentResponse:
        # 调用上传和入库业务，返回文档及其处理状态。
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
        # 获取文档详情并转换为响应；访问权限由 HTTP 路由检查。
        document = await self._document_biz.get_document(document_id, user_id=user_id)
        return DocumentResponse.from_domain(document)

    async def delete_document(self, document_id: int, user_id: int) -> None:
        # 调用文档删除业务；访问权限由 HTTP 路由检查。
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
        """重试指定文档的入库阶段，并将更新后的文档状态转换为响应。"""
        document = await self._document_biz.retry(document_id, user_id, stage)
        return DocumentResponse.from_domain(document)

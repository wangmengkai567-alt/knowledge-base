"""
api/openapi/schemas/document.py — 文档相关 DTO（Pydantic Model）

DTO ↔ DO 转换在 Service 层完成。
DTO 对外暴露（OpenAPI schema），DO 内部使用。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from internal.biz.entity import Document


class DocumentResponse(BaseModel):
    """文档信息响应 DTO。"""

    id: int = Field(description="文档 ID")
    knowledge_base_id: int = Field(description="所属知识库 ID")
    filename: str = Field(description="原始文件名")
    file_size: int = Field(description="文件大小（字节）")
    mime_type: str = Field(description="MIME 类型")
    status: int = Field(description="文档状态：1=待解析, 2=已解析, 3=解析失败, 4=解析中")
    parsed_content: str | None = Field(default=None, description="解析后的纯文本内容")
    error_message: str | None = Field(default=None, description="解析失败时的错误信息")
    chunk_count: int = Field(default=0, description="分块（向量）数量")
    embedding_pending_count: int = Field(default=0, description="待嵌入分块数")
    embedding_failed_count: int = Field(default=0, description="嵌入失败分块数")
    embedding_completed_count: int = Field(default=0, description="嵌入完成分块数")
    created_at: datetime | None = Field(default=None, description="创建时间")

    @classmethod
    def from_domain(cls, doc: Document) -> DocumentResponse:
        """DO → DTO 转换。"""
        return cls(
            id=doc.id,
            knowledge_base_id=doc.knowledge_base_id,
            filename=doc.filename,
            file_size=doc.file_size,
            mime_type=doc.mime_type,
            status=doc.status,
            parsed_content=doc.parsed_content,
            error_message=doc.error_message,
            chunk_count=doc.chunk_count,
            embedding_pending_count=doc.embedding_pending_count,
            embedding_failed_count=doc.embedding_failed_count,
            embedding_completed_count=doc.embedding_completed_count,
            created_at=doc.created_at,
        )


class ParseDocumentRequest(BaseModel):
    """手动触发解析 / 重解析。"""

    force: bool = Field(
        default=False,
        description="为 true 时对已解析文档也重新解析并重分块",
    )


class DocumentListResponse(BaseModel):
    """文档列表响应 DTO。"""

    documents: list[DocumentResponse] = Field(description="文档列表")
    total: int = Field(description="文档总数")

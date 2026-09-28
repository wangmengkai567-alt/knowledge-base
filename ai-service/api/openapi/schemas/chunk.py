"""
api/openapi/schemas/chunk.py — 分块相关 DTO（Pydantic Model）

DTO ↔ DO 转换在 Service 层完成。
DTO 对外暴露（OpenAPI schema），DO 内部使用。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from internal.biz.entity import Chunk


class ChunkResponse(BaseModel):
    """分块信息响应 DTO。"""

    id: int = Field(description="分块 ID")
    document_id: int = Field(description="所属文档 ID")
    position: int = Field(description="位置序号（从 0 开始）")
    content: str = Field(description="分块文本内容")
    token_count: int = Field(description="预估 token 数")
    embedding_status: int = Field(description="嵌入状态：0=待嵌入, 1=已完成, 2=失败")
    created_at: datetime | None = Field(default=None, description="创建时间")

    @classmethod
    def from_domain(cls, chunk: Chunk) -> ChunkResponse:
        """DO → DTO 转换。"""
        return cls(
            id=chunk.id,
            document_id=chunk.document_id,
            position=chunk.position,
            content=chunk.content,
            token_count=chunk.token_count,
            embedding_status=chunk.embedding_status,
            created_at=chunk.created_at,
        )


class ChunkListResponse(BaseModel):
    """分块列表响应 DTO。"""

    chunks: list[ChunkResponse] = Field(description="分块列表")
    total: int = Field(description="分块总数")

"""
api/openapi/schemas/knowledge_base.py — 知识库相关 DTO
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from internal.biz.entity import KnowledgeBase


class KnowledgeBaseCreateRequest(BaseModel):
    """创建知识库入参。"""

    name: str = Field(min_length=1, max_length=40, description="知识库名称")
    description: str = Field(default="", max_length=200, description="描述")
    icon: str = Field(default="folder", max_length=32, description="图标标识")


class KnowledgeBaseUpdateRequest(BaseModel):
    """更新知识库入参 — 字段均可选。"""

    name: str | None = Field(default=None, min_length=1, max_length=40, description="知识库名称")
    description: str | None = Field(default=None, max_length=200, description="描述")
    icon: str | None = Field(default=None, max_length=32, description="图标标识")


class KnowledgeBaseResponse(BaseModel):
    """知识库响应 DTO。"""

    id: int = Field(description="知识库 ID")
    name: str = Field(description="名称")
    description: str = Field(description="描述")
    icon: str = Field(description="图标标识")
    status: int = Field(description="状态：1=正常 2=归档 3=索引重建中")
    owner_id: int = Field(description="创建者用户 ID")
    embedding_model: str = Field(description="嵌入模型名")
    chunk_strategy: str = Field(description="分块策略")
    chunk_size: int = Field(description="分块大小")
    chunk_overlap: int = Field(description="分块重叠")
    doc_count: int = Field(description="文档数")
    chunk_count: int = Field(description="分块（向量）数")
    created_at: datetime | None = Field(default=None, description="创建时间")
    updated_at: datetime | None = Field(default=None, description="更新时间")

    @classmethod
    def from_domain(cls, kb: KnowledgeBase) -> KnowledgeBaseResponse:
        return cls(
            id=kb.id or 0,
            name=kb.name,
            description=kb.description,
            icon=kb.icon,
            status=int(kb.status),
            owner_id=kb.owner_id,
            embedding_model=kb.embedding_model,
            chunk_strategy=kb.chunk_strategy,
            chunk_size=kb.chunk_size,
            chunk_overlap=kb.chunk_overlap,
            doc_count=kb.doc_count,
            chunk_count=kb.chunk_count,
            created_at=kb.created_at,
            updated_at=kb.updated_at,
        )


class KnowledgeBaseListResponse(BaseModel):
    """知识库列表响应 DTO。"""

    knowledge_bases: list[KnowledgeBaseResponse] = Field(description="知识库列表")
    total: int = Field(description="总数")

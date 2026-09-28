"""
api/openapi/schemas/retrieval.py — 检索相关 DTO（Pydantic Model）

DTO ↔ DO 转换在 Service 层完成。
DTO 对外暴露（OpenAPI schema），DO 内部使用。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RetrievalRequest(BaseModel):
    """检索请求 DTO。"""

    query: str = Field(min_length=1, max_length=2000, description="检索查询文本")
    top_k: int = Field(default=10, ge=1, le=50, description="返回的最相关结果数量")


class RetrievalResultDTO(BaseModel):
    """单条检索结果 DTO。"""

    chunk_id: int = Field(description="分块 ID")
    score: float = Field(description="相似度分数（0~1，越高越相关）")
    content: str = Field(description="分块文本内容")
    position: int = Field(description="分块在文档中的位置序号")
    document_id: int = Field(description="来源文档 ID")
    document_filename: str = Field(description="来源文档文件名")
    document_updated_at: str | None = Field(
        default=None,
        description="来源文档更新时间（ISO 8601，可选；前端时间筛选用）",
    )


class RetrievalResponse(BaseModel):
    """检索响应 DTO。"""

    query: str = Field(description="原始查询文本")
    knowledge_base_id: int = Field(description="检索的知识库 ID")
    results: list[RetrievalResultDTO] = Field(description="检索结果列表，按相似度降序")
    total: int = Field(description="返回的结果数量")

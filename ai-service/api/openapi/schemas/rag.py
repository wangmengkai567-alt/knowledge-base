"""
api/openapi/schemas/rag.py — RAG 检索增强生成 DTO（Pydantic Model）

DTO ↔ DO 转换在 Service 层完成。
DTO 对外暴露（OpenAPI schema），DO 内部使用。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RAGQueryRequest(BaseModel):
    """RAG 查询请求 DTO。"""

    query: str = Field(min_length=1, max_length=4000, description="用户问题")
    top_k: int = Field(default=5, ge=1, le=20, description="检索的 chunk 数量")
    temperature: float | None = Field(default=None, ge=0.0, le=2.0, description="生成温度（覆盖默认）")
    max_tokens: int | None = Field(default=None, ge=1, le=8192, description="最大生成 token 数")
    chunk_ids: list[int] | None = Field(
        default=None,
        max_length=20,
        description="已检索的分块 ID（按相关度排序）。传入则不再检索，直接用这些块生成。",
    )
    knowledge_base_ids: list[int] | None = Field(
        default=None,
        max_length=50,
        description="chunk_ids 允许来自这些知识库。缺省则仅当前路径上的知识库。",
    )


class RAGSourceDTO(BaseModel):
    """RAG 引用来源 DTO。"""

    chunk_id: int = Field(description="分块 ID")
    content: str = Field(description="分块内容（截断至 200 字符）")
    score: float = Field(description="相似度分数（0~1）")
    document_id: int = Field(description="来源文档 ID")
    document_filename: str = Field(description="来源文档文件名")
    knowledge_base_id: int | None = Field(default=None, description="来源知识库 ID")


class RAGQueryResponse(BaseModel):
    """RAG 查询响应 DTO。"""

    answer: str = Field(description="LLM 基于知识库生成的回答")
    model: str = Field(description="使用的模型名称")
    sources: list[RAGSourceDTO] = Field(description="引用的来源列表")
    source_count: int = Field(description="引用来源数量")

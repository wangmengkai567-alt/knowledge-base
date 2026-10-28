"""知识库问答协议：问题与知识库范围，返回检索命中及实际送模资料。"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from api.openapi.schemas.retrieval import RetrievalResultDTO, SourceScoreDTO


class RAGQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    allow_web: bool = Field(default=False, strict=True, description="用户明确开启后才允许联网")
    user_urls: list[str] = Field(default_factory=list, max_length=3, description="用户明确提供的公开网址")
    top_k: int = Field(default=5, ge=1, le=10)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=2048)
    knowledge_base_ids: list[Annotated[int, Field(gt=0)]] | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description="本次问答允许检索的知识库；缺省为路径中的知识库",
    )


class MaterialSegmentDTO(BaseModel):
    chunk_id: int
    position: int
    start: int = Field(description="原分块内字符起点（含）")
    end: int = Field(description="原分块内字符终点（不含）")
    content: str = Field(description="该范围实际送模的原文")


class HitScoreDTO(BaseModel):
    chunk_id: int
    score: float | None
    score_type: str | None
    score_method: str | None
    scores: list[SourceScoreDTO]


class RAGSourceDTO(BaseModel):
    citation_id: int
    source_type: str
    content: str = Field(description="完整的实际送模片段，合并与截断后与上下文一致")
    chunk_ids: list[int]
    segments: list[MaterialSegmentDTO]
    truncated: bool
    document_id: int
    document_filename: str
    knowledge_base_id: int
    document_updated_at: str | None
    retrieval_hits: list[HitScoreDTO] = Field(
        description="该资料所含检索命中的评分元数据；已使用原文仅见 content/segments"
    )


class RAGWebSourceDTO(BaseModel):
    citation_id: int
    source_type: str
    url: str
    title: str
    fetched_at: str
    content: str
    truncated: bool


class RunUsageDTO(BaseModel):
    model_calls: int
    tool_calls: int
    estimated_input_tokens: int
    prompt_tokens: int
    completion_tokens: int
    charged_tokens: int
    unreported_model_calls: int


class RunEventDTO(BaseModel):
    sequence: int
    kind: str
    elapsed_ms: int
    data: dict


class RAGQueryResponse(BaseModel):
    run_id: str
    status: str
    stop_reason: str
    error_code: str | None
    usage: RunUsageDTO
    events: list[RunEventDTO]
    answer: str
    model: str
    sources: list[RAGSourceDTO | RAGWebSourceDTO]
    source_count: int
    retrieval_results: list[RetrievalResultDTO]
    input_measurement: str
    estimated_input_tokens: int
    prompt_tokens: int
    completion_tokens: int
